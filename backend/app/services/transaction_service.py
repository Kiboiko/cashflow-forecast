"""Операции бизнеса: список с поиском, исправление категорий (правило запоминается), сводка «куда уходят деньги»."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Business, CounterpartyRule, Transaction
from app.engine.categorize.taxonomy import TAXONOMY
from app.engine.normalize import norm_text
from app.schemas import (
    CategoryInfo,
    CategoryPatch,
    CategoryPatchResult,
    CategorySummary,
    CategorySummaryRow,
    TransactionOut,
    TransactionPage,
)
from app.services.engine_adapter import TX_RULE_PREFIX, persist_derived
from app.services.forecast_service import ForecastService

REVIEW_LOOKBACK_DAYS = 365          # как в движке: unknown_ops считается за последний год


def categories() -> list[CategoryInfo]:
    return [CategoryInfo(code=c.code, title=c.title, group=c.group, mandatory=c.mandatory) for c in TAXONOMY.values()]


def visible_conditions(business: Business) -> list:
    conditions = [Transaction.business_id == business.id]
    if business.data_as_of is not None:
        conditions.append(Transaction.date <= business.data_as_of)
    return conditions


def _review_condition(business: Business) -> list:
    """Нераспознанные операции за последний год (не дубли)."""
    conditions = [Transaction.category_source == "unknown"]
    if business.data_as_of is not None:
        conditions.append(Transaction.date > business.data_as_of - timedelta(days=REVIEW_LOOKBACK_DAYS))
    return conditions


def counterparty_key(tx: Transaction) -> str | None:
    """Ключ контрагента, как у движка: ИНН, иначе нормализованное имя."""
    inn = (tx.counterparty_inn or "").strip()
    return inn or norm_text(tx.counterparty_name) or None


def to_out(tx: Transaction) -> TransactionOut:
    flags = []
    if tx.flags.get("duplicate"):
        flags.append("duplicate")
    if tx.flags.get("reversal_pair_id"):
        flags.append("reversal")
    if tx.flags.get("gap_adjacent"):
        flags.append("gap_adjacent")
    category = TAXONOMY.get(tx.category) if tx.category else None
    return TransactionOut(
        id=tx.id, date=tx.date, amount=float(tx.amount), direction="in" if tx.direction > 0 else "out",
        counterparty_inn=tx.counterparty_inn, counterparty_name=tx.counterparty_name, purpose=tx.purpose,
        category=tx.category, category_title=category.title if category else None,
        category_source=tx.category_source, needs_review=tx.category_source == "unknown", flags=flags)


def list_transactions(db: Session, business: Business, *, date_from: date | None, date_to: date | None,
                      category: str | None, query: str | None, review: bool, limit: int, offset: int) -> TransactionPage:
    stmt = select(Transaction).where(*visible_conditions(business))
    if date_from:
        stmt = stmt.where(Transaction.date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.date <= date_to)
    if category:
        stmt = stmt.where(Transaction.category == category)
    if review:
        stmt = stmt.where(*_review_condition(business))
    rows = db.scalars(stmt.order_by(Transaction.date.desc(), Transaction.id.desc())).all()
    if query and query.strip():
        needle = query.strip().casefold()            # поиск без учёта регистра: lower() в SQLite не знает кириллицу
        rows = [t for t in rows if needle in " ".join(
            (t.counterparty_name or "", t.counterparty_inn or "", t.purpose or "")).casefold()]
    review_count = db.scalar(select(func.count()).select_from(Transaction).where(*visible_conditions(business), *_review_condition(business)))
    return TransactionPage(items=[to_out(t) for t in rows[offset:offset + limit]], total=len(rows), limit=limit,
                           offset=offset, needs_review_count=int(review_count or 0))


def _same_counterparty(db: Session, business: Business, key: str) -> list[Transaction]:
    rows = db.scalars(select(Transaction).where(Transaction.business_id == business.id)).all()
    return [t for t in rows if counterparty_key(t) == key]


def apply_category(db: Session, business: Business, service: ForecastService, tx: Transaction,
                   patch: CategoryPatch) -> CategoryPatchResult:
    """Меняет категорию операции. С `apply_to_counterparty` правило запоминается для контрагента (все его операции,
    включая будущие), иначе — только для этой операции. После правки состояние движка пересобирается целиком."""
    key = counterparty_key(tx) if patch.apply_to_counterparty else None
    rule_key = key or f"{TX_RULE_PREFIX}{tx.id}"
    affected = _same_counterparty(db, business, key) if key else [tx]
    before = {t.id: t.category for t in affected}

    rule = db.scalar(select(CounterpartyRule).where(CounterpartyRule.business_id == business.id,
                                                    CounterpartyRule.counterparty_key == rule_key))
    if rule is None:
        db.add(CounterpartyRule(business_id=business.id, counterparty_key=rule_key, category=patch.category, source="user"))
    else:
        rule.category, rule.source = patch.category, "user"
    business.data_version += 1
    db.commit()

    state = service.state(db, business)
    persist_derived(db, business, state)
    db.expire_all()
    updated = sum(1 for t in affected if db.get(Transaction, t.id).category != before[t.id])
    return CategoryPatchResult(transaction=to_out(db.get(Transaction, tx.id)), updated=updated, rule_saved=key is not None)


def category_summary(db: Session, business: Business, months: int = 12) -> CategorySummary:
    as_of = business.data_as_of or date.today()
    first_month = (pd.Period(as_of, "M") - (months - 1)).start_time.date()
    rows = db.execute(select(Transaction.date, Transaction.amount, Transaction.direction, Transaction.category, Transaction.flags)
                      .where(*visible_conditions(business), Transaction.date >= first_month)).all()
    labels = [str(p) for p in pd.period_range(pd.Period(first_month, "M"), pd.Period(as_of, "M"), freq="M")]
    totals: dict[tuple[str, int], list[float]] = {}
    for r in rows:
        if not r.category or r.flags.get("duplicate"):
            continue
        index = labels.index(r.date.strftime("%Y-%m"))
        totals.setdefault((r.category, r.direction), [0.0] * len(labels))[index] += float(r.amount)
    out = []
    for (code, direction), by_month in totals.items():
        info = TAXONOMY.get(code)
        out.append(CategorySummaryRow(
            category=code, title=info.title if info else code, group=info.group if info else "—",
            direction="in" if direction > 0 else "out", total=round(sum(by_month), 2),
            by_month=[round(v, 2) for v in by_month]))
    out.sort(key=lambda r: (r.direction == "in", -r.total))
    return CategorySummary(months=labels, rows=out,
                           inflow_total=round(sum(r.total for r in out if r.direction == "in"), 2),
                           outflow_total=round(sum(r.total for r in out if r.direction == "out"), 2))
