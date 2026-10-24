"""План предпринимателя: ручные события («клиент обещал 300 000 ₽ 20-го»), счета и договоры из CSV (§13)."""
from __future__ import annotations

import io

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Business, ExpectedEvent
from app.engine.normalize import norm_text
from app.schemas import EventCreate, EventOut, EventPatch, EventsImportResult
from app.services.format import dmy_long

INVOICE_COLUMNS = ["doc_type", "doc_no", "doc_date", "direction", "counterparty_inn", "counterparty_name", "amount", "due_date"]
CONTRACT_COLUMNS = ["contract_no", "date", "amount", "direction", "counterparty_inn", "counterparty_name"]
_DIRECTIONS = {"in": "in", "out": "out", "приход": "in", "расход": "out", "поступление": "in", "списание": "out",
               "вам должны": "in", "вы должны": "out"}
_DOC_TYPES = {"invoice", "upd", "act"}
MAX_ERRORS = 5


class EventError(ValueError):
    """Ошибка плана для пользователя: сообщение показываем как есть (HTTP 422)."""


def to_out(event: ExpectedEvent) -> EventOut:
    return EventOut(
        id=event.id, kind=event.kind, source=event.source, direction=event.direction, amount=float(event.amount),
        expected_date=event.expected_date, due_date=event.due_date, doc_type=event.doc_type, doc_no=event.doc_no,
        doc_date=event.doc_date, counterparty_inn=event.counterparty_inn, counterparty_name=event.counterparty_name,
        title=event.title, probability=event.probability, mandatory=event.mandatory, status=event.status)


def list_events(db: Session, business: Business, include_cancelled: bool = False) -> list[EventOut]:
    stmt = select(ExpectedEvent).where(ExpectedEvent.business_id == business.id)
    if not include_cancelled:
        stmt = stmt.where(ExpectedEvent.status != "cancelled")
    rows = db.scalars(stmt.order_by(ExpectedEvent.expected_date.is_(None), ExpectedEvent.expected_date, ExpectedEvent.id)).all()
    return [to_out(e) for e in rows]


def _require_future(business: Business, day) -> None:
    if business.data_as_of is not None and day <= business.data_as_of:
        raise EventError(f"Дата должна быть позже даты данных ({dmy_long(business.data_as_of)}): прогноз строится вперёд.")


def create_manual(db: Session, business: Business, body: EventCreate) -> ExpectedEvent:
    _require_future(business, body.date)
    event = ExpectedEvent(
        business_id=business.id, kind="manual", source="manual", direction=body.direction, amount=body.amount,
        expected_date=body.date, title=body.title.strip(), counterparty_name=(body.counterparty_name or "").strip() or None,
        probability=body.probability, mandatory=body.mandatory, status="planned", meta={})
    db.add(event)
    business.data_version += 1
    db.commit()
    return event


def update_event(db: Session, business: Business, event: ExpectedEvent, patch: EventPatch) -> ExpectedEvent:
    changes = patch.model_dump(exclude_unset=True)
    if "date" in changes and changes["date"] is not None:
        _require_future(business, changes["date"])
        event.expected_date = changes.pop("date")
    changes.pop("date", None)
    for field, value in changes.items():
        if value is not None or field == "counterparty_name":
            setattr(event, field, value)
    business.data_version += 1
    db.commit()
    return event


def cancel_event(db: Session, business: Business, event: ExpectedEvent) -> None:
    event.status = "cancelled"
    business.data_version += 1
    db.commit()


# ---------- CSV счетов и договоров ----------
def _read(content: bytes) -> pd.DataFrame:
    if not content.strip():
        raise EventError("Файл пустой.")
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise EventError("Не удалось прочитать файл: нужна кодировка UTF-8 или Windows-1251.")
    sep = ";" if text.split("\n", 1)[0].count(";") >= text.split("\n", 1)[0].count(",") else ","
    try:
        frame = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise EventError("Файл не удалось разобрать как таблицу CSV.") from exc
    frame.columns = [str(c).strip().lstrip(chr(0xFEFF)) for c in frame.columns]
    return frame


def _clean(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _date(value: str | None, label: str, row: int, errors: list[str], required: bool = True):
    if value is None:
        if required:
            errors.append(f"Строка {row}: не указана {label}.")
        return None
    parsed = pd.to_datetime(value, errors="coerce", dayfirst="." in value)
    if pd.isna(parsed):
        errors.append(f"Строка {row}: неверная {label} «{value}», нужен формат ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.")
        return None
    return parsed.date()


def _amount(value: str | None, row: int, errors: list[str]) -> float | None:
    number = pd.to_numeric((value or "").replace(" ", "").replace(",", "."), errors="coerce")
    if pd.isna(number) or number <= 0:
        errors.append(f"Строка {row}: сумма должна быть числом больше нуля.")
        return None
    return round(float(number), 2)


def _party_key(inn: str | None, name: str | None) -> str:
    return inn or norm_text(name)


def import_events_csv(db: Session, business: Business, content: bytes) -> EventsImportResult:
    """Счета (`doc_type;doc_no;doc_date;direction;…`) или договоры (`contract_no;date;amount;…`) из CSV.

    Файл принимается целиком или отклоняется целиком: частично загруженный план хуже, чем честная ошибка.
    """
    frame = _read(content)
    kind = "contracts" if "contract_no" in frame.columns else "invoices"
    needed = CONTRACT_COLUMNS if kind == "contracts" else INVOICE_COLUMNS
    optional = {"counterparty_inn", "counterparty_name", "due_date", "doc_type"}
    missing = [c for c in needed if c not in frame.columns and c not in optional]
    if missing:
        raise EventError(f"В файле нет столбцов: {', '.join(missing)}. Образец: {'; '.join(needed)}.")
    if frame.empty:
        raise EventError("В файле нет строк.")

    existing = {(e.kind, e.direction, _party_key(e.counterparty_inn, e.counterparty_name), e.doc_no, e.doc_date)
                for e in db.scalars(select(ExpectedEvent).where(ExpectedEvent.business_id == business.id,
                                                                ExpectedEvent.status != "cancelled"))}
    errors: list[str] = []
    rows: list[ExpectedEvent] = []
    skipped = 0
    for index, record in enumerate(frame.to_dict("records")):
        number = index + 2
        problems: list[str] = []
        direction = _DIRECTIONS.get((_clean(record.get("direction")) or "").casefold())
        if direction is None:
            problems.append(f"Строка {number}: направление должно быть in (вам должны) или out (вы должны).")
        amount = _amount(_clean(record.get("amount")), number, problems)
        inn, name = _clean(record.get("counterparty_inn")), _clean(record.get("counterparty_name"))
        if kind == "invoices":
            doc_no = _clean(record.get("doc_no"))
            if not doc_no:
                problems.append(f"Строка {number}: не указан номер документа.")
            doc_date = _date(_clean(record.get("doc_date")), "дата документа", number, problems)
            due = _date(_clean(record.get("due_date")), "срок оплаты", number, problems, required=False)
            doc_type = (_clean(record.get("doc_type")) or "invoice").casefold()
            if doc_type not in _DOC_TYPES:
                problems.append(f"Строка {number}: тип документа должен быть invoice, upd или act.")
            if problems:
                errors.extend(problems)
                if len(errors) >= MAX_ERRORS:
                    break
                continue
            key = ("invoice_out" if direction == "in" else "bill_in", direction, _party_key(inn, name), doc_no, doc_date)
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            rows.append(ExpectedEvent(
                business_id=business.id, kind=key[0], source="csv", direction=direction, amount=amount, expected_date=due,
                due_date=due, doc_type=doc_type, doc_no=doc_no, doc_date=doc_date, counterparty_inn=inn, counterparty_name=name,
                title=f"Счёт № {doc_no}" + (f" от {name}" if name else ""), probability=1.0, mandatory=False,
                status="planned", meta={}))
        else:
            contract_no = _clean(record.get("contract_no"))
            if not contract_no:
                problems.append(f"Строка {number}: не указан номер договора.")
            when = _date(_clean(record.get("date")), "дата ожидаемого расчёта", number, problems)
            if problems:
                errors.extend(problems)
                if len(errors) >= MAX_ERRORS:
                    break
                continue
            key = ("contract", direction, _party_key(inn, name), contract_no, when)
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            rows.append(ExpectedEvent(
                business_id=business.id, kind="contract", source="csv", direction=direction, amount=amount, expected_date=when,
                doc_no=contract_no, counterparty_inn=inn, counterparty_name=name,
                title=f"Договор № {contract_no}" + (f" с {name}" if name else ""), probability=1.0, mandatory=False,
                status="planned", meta={}))
    if errors:
        raise EventError(" ".join(errors[:MAX_ERRORS]))
    db.add_all(rows)
    if kind == "invoices" and business.accounting_source not in ("csv", "1c"):
        business.accounting_source = "csv"             # свой файл важнее демо-«учётной системы»
    business.data_version += 1
    db.commit()
    return EventsImportResult(kind=kind, added=len(rows), skipped=skipped, accounting_source=business.accounting_source)
