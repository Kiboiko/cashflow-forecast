"""Импорт выписки (§13): CSV формата датасета → операции в БД → категоризация и контроль качества движком.

Файл разбирается в памяти и нигде не сохраняется (§10: загруженные файлы не должны оставаться на диске).
В журнал попадают только форматы и количества — ни ИНН, ни сумм, ни назначений.
"""
from __future__ import annotations

import hashlib
import io
import logging
import time

import pandas as pd
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.db.models import Account, Business, ImportBatch, Transaction
from app.schemas import GapOut, ImportResult
from app.services.engine_adapter import persist_derived
from app.services.forecast_service import ForecastService

log = logging.getLogger("kassa.import")

MAX_BYTES = 25 * 1024 * 1024
MAX_ROWS = 300_000
REQUIRED = ("operation_date", "amount", "direction", "balance_eod")
_DIRECTIONS = {"Поступление": 1, "Списание": -1}
_TEXT_COLUMNS = ("operation_id", "counterparty_inn", "counterparty_name", "payment_purpose", "direction", "company_id")
_BOM = chr(0xFEFF)


class StatementError(ValueError):
    """Файл не похож на выписку или в нём ошибки: сообщение показываем пользователю как есть."""


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise StatementError("Не удалось прочитать файл: нужна кодировка UTF-8 или Windows-1251.")


def _text(frame: pd.DataFrame, column: str) -> pd.Series:
    """Текстовый столбец без пробелов по краям; пустое значение → None."""
    if column not in frame:
        return pd.Series([None] * len(frame), dtype=object)
    return pd.Series([value.strip() or None for value in frame[column].astype(str)], dtype=object)


def _number(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column].astype(str).str.replace(",", ".", regex=False), errors="coerce")


def parse_dataset_csv(content: bytes) -> pd.DataFrame:
    """CSV формата датасета (`;`, UTF-8 с BOM, ИНН строкой) → таблица с проверенными типами."""
    if not content.strip():
        raise StatementError("Файл пустой.")
    text = _decode(content)
    header = text.split("\n", 1)[0]
    sep = ";" if header.count(";") >= header.count(",") else ","
    try:
        frame = pd.read_csv(io.StringIO(text), sep=sep, dtype={c: str for c in _TEXT_COLUMNS}, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise StatementError("Файл не удалось разобрать как таблицу CSV.") from exc
    frame.columns = [str(c).strip().lstrip(_BOM) for c in frame.columns]
    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise StatementError(f"В файле нет столбцов: {', '.join(missing)}. Нужна выписка в формате датасета.")
    if frame.empty:
        raise StatementError("В файле нет операций.")
    if len(frame) > MAX_ROWS:
        raise StatementError(f"Слишком много строк (больше {MAX_ROWS:_}). Загрузите выписку частями.".replace("_", " "))

    dates = pd.to_datetime(frame["operation_date"], errors="coerce")
    if dates.isna().any():
        raise StatementError(f"Неверная дата в строке {int(dates.isna().to_numpy().argmax()) + 2}: нужен формат ГГГГ-ММ-ДД.")
    amounts = _number(frame, "amount")
    bad_amount = amounts.isna() | (amounts <= 0)
    if bad_amount.any():
        raise StatementError(f"Неверная сумма в строке {int(bad_amount.to_numpy().argmax()) + 2}: нужно число больше нуля.")
    balances = _number(frame, "balance_eod")
    if balances.isna().any():
        raise StatementError(f"Неверный остаток в строке {int(balances.isna().to_numpy().argmax()) + 2}.")
    direction = frame["direction"].astype(str).str.strip().map(_DIRECTIONS)
    if direction.isna().any():
        raise StatementError(f"Неизвестное направление в строке {int(direction.isna().to_numpy().argmax()) + 2}: "
                             "ожидается «Поступление» или «Списание».")

    out = pd.DataFrame({
        "date": dates.dt.date, "amount": amounts.round(2), "direction": direction.astype(int),
        "counterparty_inn": _text(frame, "counterparty_inn"), "counterparty_name": _text(frame, "counterparty_name"),
        "purpose": _text(frame, "payment_purpose"), "balance_eod": balances.round(2),
        "external_id": _text(frame, "operation_id"),
    })
    out["external_id"] = _external_ids(out)
    return out.sort_values(["date"], kind="stable").reset_index(drop=True)


def _external_ids(frame: pd.DataFrame) -> pd.Series:
    """Стабильный id строки: свой `operation_id`, а если его нет — хэш содержимого + номер среди одинаковых строк.

    Так повторная загрузка того же файла не создаёт копий, а настоящие дубли внутри файла остаются разными строками.
    """
    columns = frame[["date", "amount", "direction", "counterparty_inn", "counterparty_name", "purpose"]]
    key = pd.Series(["|".join(map(str, row)) for row in columns.itertuples(index=False, name=None)], index=frame.index)
    occurrence = key.groupby(key).cumcount()
    generated = pd.Series([hashlib.sha1(f"{k}#{n}".encode()).hexdigest()[:20] for k, n in zip(key, occurrence, strict=True)],
                          index=frame.index, dtype=object)
    return frame["external_id"].where(frame["external_id"].notna(), generated)


def primary_account(db: Session, business: Business) -> Account:
    account = db.scalar(select(Account).where(Account.business_id == business.id, Account.is_primary.is_(True)))
    if account is None:
        account = Account(business_id=business.id, bank_name="Основной счёт", is_primary=True)
        db.add(account)
        db.flush()
    return account


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def insert_transactions(db: Session, business: Business, frame: pd.DataFrame, batch: ImportBatch) -> int:
    """Добавляет строки, которых ещё нет у бизнеса (по `external_id`). Возвращает, сколько добавлено."""
    existing = set(db.scalars(select(Transaction.external_id).where(Transaction.business_id == business.id)))
    fresh = frame[~frame["external_id"].isin(existing)]
    if fresh.empty:
        return 0
    account = primary_account(db, business)
    rows = [{
        "business_id": business.id, "account_id": account.id, "external_id": r.external_id, "date": r.date,
        "amount": float(r.amount), "direction": int(r.direction), "counterparty_inn": _str_or_none(r.counterparty_inn),
        "counterparty_name": _str_or_none(r.counterparty_name), "purpose": _str_or_none(r.purpose),
        "balance_eod": float(r.balance_eod), "flags": {}, "import_batch_id": batch.id,
    } for r in fresh.itertuples()]
    db.execute(insert(Transaction), rows)
    return len(rows)


def import_statement(db: Session, business: Business, service: ForecastService, frame: pd.DataFrame, filename: str | None,
                     source: str = "dataset_csv") -> ImportResult:
    """Полный импорт: вставка → пересборка состояния движка → категории и пропуски в БД → ответ-сводка."""
    started = time.perf_counter()
    batch = ImportBatch(business_id=business.id, source=source, filename=filename, stats={})
    db.add(batch)
    db.flush()
    added = insert_transactions(db, business, frame, batch)
    business.data_as_of = db.scalar(select(func.max(Transaction.date)).where(Transaction.business_id == business.id))
    business.data_version += 1
    db.commit()

    state = service.state(db, business)
    persist_derived(db, business, state)

    unknown = int((state.ops["category_source"] == "unknown").sum())
    total = len(state.ops)
    first = db.scalar(select(func.min(Transaction.date)).where(Transaction.business_id == business.id))
    gaps = [GapOut(start=g.start_date, end=g.end_date, unexplained_amount=g.unexplained_amount) for g in state.quality.gaps]
    result = ImportResult(
        batch_id=batch.id, format=source, period=(first or business.data_as_of, business.data_as_of), added=added,
        duplicates_removed=state.quality.duplicates_removed, data_gaps=gaps,
        categorized_share=round(1 - unknown / total, 4) if total else 1.0, needs_review=unknown)
    batch.stats = {"rows": len(frame), "added": added, "duplicates_removed": result.duplicates_removed,
                   "gaps": len(gaps), "categorized_share": result.categorized_share, "needs_review": unknown}
    db.commit()
    log.info("import", extra={"fields": {"business": business.id, "format": source, "rows": len(frame), "added": added,
                                         "ms": round((time.perf_counter() - started) * 1000)}})
    return result


def format_of(content: bytes) -> str:
    """Определяет формат файла: 1С (`1CClientBankExchange`) или CSV датасета."""
    head = content[:2048].decode("cp1251", errors="ignore")
    return "1c" if "1CClientBankExchange" in head else "dataset_csv"
