"""Мост между БД и движком (ENGINE_API.md): операции из таблицы → DataFrame формата датасета → `normalize`;
результаты движка (категории, дубли, пропуски) → обратно в таблицу.

В движок `operation_id` уходит как `str(Transaction.id)` — он уникален, поэтому дубли выписки (одинаковые строки)
не склеиваются по id, а `matched_transaction_id` из план-факта сразу указывает на строку в БД. Исходный
`operation_id` выписки лежит в `Transaction.external_id` и нужен только для идемпотентного импорта.
"""
from __future__ import annotations

import pickle
from datetime import date

import pandas as pd
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models import Business, BusinessContext, CounterpartyRule, DataGap, Transaction
from app.engine.context import Context
from app.engine.normalize import normalize
from app.engine.state import EngineState

RAW_COLUMNS = ["company_id", "operation_id", "operation_date", "amount", "direction", "counterparty_inn",
               "counterparty_name", "payment_purpose", "balance_eod"]
_DIRECTION = {1: "Поступление", -1: "Списание"}
TX_RULE_PREFIX = "tx:"


class NoDataError(Exception):
    """У бизнеса ещё нет ни одной операции: нужно загрузить выписку."""


def load_ops(db: Session, business: Business, up_to: date | None = None) -> pd.DataFrame:
    """Нормализованные операции бизнеса до даты `up_to` (по умолчанию — видимые, то есть ≤ `data_as_of`)."""
    limit = up_to if up_to is not None else business.data_as_of
    stmt = select(Transaction.id, Transaction.date, Transaction.amount, Transaction.direction,
                  Transaction.counterparty_inn, Transaction.counterparty_name, Transaction.purpose,
                  Transaction.balance_eod).where(Transaction.business_id == business.id)
    if limit is not None:
        stmt = stmt.where(Transaction.date <= limit)
    rows = db.execute(stmt.order_by(Transaction.date, Transaction.id)).all()
    if not rows:
        raise NoDataError(business.id)
    raw = pd.DataFrame({
        "company_id": business.id,
        "operation_id": [str(r.id) for r in rows],
        "operation_date": [pd.Timestamp(r.date) for r in rows],
        "amount": [float(r.amount) for r in rows],
        "direction": [_DIRECTION[r.direction] for r in rows],
        "counterparty_inn": [r.counterparty_inn for r in rows],
        "counterparty_name": [r.counterparty_name for r in rows],
        "payment_purpose": [r.purpose for r in rows],
        "balance_eod": [float(r.balance_eod) for r in rows],
    })
    ops = normalize(raw[RAW_COLUMNS])
    # Правка категории одной операции: у неё свой ключ `tx:<id>`, правило с этим ключом лежит в CounterpartyRule.
    pinned = {key[len(TX_RULE_PREFIX):] for key in load_user_rules(db, business) if key.startswith(TX_RULE_PREFIX)}
    if pinned:
        mask = ops["operation_id"].isin(pinned)
        ops.loc[mask, "cp_key"] = TX_RULE_PREFIX + ops.loc[mask, "operation_id"]
    return ops


def load_user_rules(db: Session, business: Business) -> dict[str, str]:
    rules = db.scalars(select(CounterpartyRule).where(CounterpartyRule.business_id == business.id)).all()
    return {r.counterparty_key: r.category for r in rules}


def load_context(db: Session, business: Business) -> Context | None:
    row = db.get(BusinessContext, business.id)
    return pickle.loads(row.payload) if row else None      # свой контекст, записанный seed-скриптом


def save_context(db: Session, business_id: str, as_of: date, context: Context) -> None:
    payload = pickle.dumps(context, protocol=pickle.HIGHEST_PROTOCOL)
    row = db.get(BusinessContext, business_id)
    if row is None:
        db.add(BusinessContext(business_id=business_id, as_of=as_of, payload=payload))
    else:
        row.as_of, row.payload = as_of, payload
    db.commit()


def _clean(value: object) -> object:
    """NaN/NaT/pd.NA → None, Timestamp → date: значения для колонок БД."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    return value


def _flags(row: pd.Series) -> dict:
    flags: dict = {}
    if bool(row.get("gap_adjacent", False)):
        flags["gap_adjacent"] = True
    pair = _clean(row.get("reversal_pair_id"))
    if pair:
        flags["reversal_pair_id"] = str(pair)
    return flags


def persist_derived(db: Session, business: Business, state: EngineState, only_new_after: date | None = None) -> None:
    """Записывает категории, документы и флаги качества из состояния движка в таблицу операций.

    Строки, которые движок убрал как подтверждённые дубли, получают флаг `duplicate` и остаются в выписке.
    `only_new_after` — после дозагрузки (машина времени) трогаем только новые строки.
    """
    ops = state.ops
    if only_new_after is not None:
        ops = ops[ops["date"] > pd.Timestamp(only_new_after)]
    kept = set(state.ops["operation_id"])
    columns = ["category", "category_source", "category_confidence", "doc_type", "doc_no", "doc_date",
               "contract_no", "sales_date"]
    mappings: list[dict] = []
    for _, row in ops.iterrows():
        item = {"id": int(row["operation_id"]), "flags": _flags(row)}
        for column in columns:
            item[column] = _clean(row.get(column))
        if item["category_confidence"] is not None:
            item["category_confidence"] = float(item["category_confidence"])
        mappings.append(item)
    if only_new_after is None:
        for op_id in set(state.raw["operation_id"]) - kept:
            mappings.append({"id": int(op_id), "flags": {"duplicate": True}, "category": None,
                             "category_source": None, "category_confidence": None, "doc_type": None,
                             "doc_no": None, "doc_date": None, "contract_no": None, "sales_date": None})
    if mappings:
        db.execute(update(Transaction), mappings)
    replace_gaps(db, business, state)
    db.commit()


def replace_gaps(db: Session, business: Business, state: EngineState) -> None:
    db.execute(delete(DataGap).where(DataGap.business_id == business.id))
    for gap in state.quality.gaps:
        db.add(DataGap(business_id=business.id, start_date=gap.start_date, end_date=gap.end_date,
                       unexplained_amount=gap.unexplained_amount))
