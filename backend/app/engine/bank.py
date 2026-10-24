"""Внутренняя интеграция «данные банка» (★4, P2): графики кредитов, зарплатный проект, неисполненные платёжные поручения.

Банк знает то, чего ещё нет в выписке: дату и сумму следующего платежа по кредиту, реестр зарплатного проекта, созданные,
но ещё не исполненные платёжки. В прототипе это мок-адаптер: JSON того вида, что отдал бы банк; `bank_events` превращает
его в события прогноза с источником `bank_mock`, а `merge_bank` убирает задвоение с регулярными потоками из истории.
`demo_bank_payload` собирает такой JSON из выписки (как демо-учётная система) — подписывать честно: «демо-данные банка».

Формат:
  {"loans": [{"contract": "КД-17", "schedule": [{"date": "2026-08-03", "amount": 51000}]}],
   "payroll": [{"date": "2026-08-10", "amount": 300000, "kind": "final"}],
   "pending_orders": [{"number": "245", "date": "2026-08-03", "amount": 45000, "category": "suppliers"}]}
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.engine.calendar_ru import is_workday
from app.engine.categorize.taxonomy import TAXONOMY
from app.engine.explain import rub
from app.engine.recurring import SALARY_TITLE
from app.engine.types import PlannedEvent

SOURCE = "bank_mock"
PAYROLL_LOOKAHEAD_DAYS = 7               # реестр зарплатного проекта банк видит за несколько дней до выплаты
PENDING_WORKDAYS = 2                     # платёжки, созданные сегодня, банк исполняет в ближайшие 1–2 рабочих дня
DATE_MATCH_DAYS = 5                      # платёжка и регулярный платёж той же категории ±5 дней — это одно и то же
AMOUNT_MATCH = 0.10
NOT_PENDING = {"salary", "tax_enp", "loan_repayment", "bank_fees"}  # отдельно у банка: зарплатный проект, кредит, налоги; комиссии банк списывает сам


def _day(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def bank_events(payload: dict | None, as_of: date, horizon: int = 30) -> list[PlannedEvent]:
    """JSON банка → события прогноза в (as_of, as_of + horizon]. Суммы известны точно, поэтому без шума."""
    if not payload:
        return []
    end = as_of + timedelta(days=horizon)
    events: list[PlannedEvent] = []
    for loan in payload.get("loans", []):
        contract = str(loan.get("contract") or "")
        for item in loan.get("schedule", []):
            d = _day(item["date"])
            if as_of < d <= end:
                events.append(PlannedEvent(
                    id=f"bank:loan:{contract}:{d.isoformat()}", direction=-1, amount=float(item["amount"]), date=d,
                    p_within_horizon=0.99, category="loan_repayment",
                    title=f"Платёж по кредиту{' ' + contract if contract else ''}", source=SOURCE, mandatory=True,
                    explain="График платежей по кредиту — из банка"))
    for item in payload.get("payroll", []):
        d, kind = _day(item["date"]), item.get("kind", "final")
        if as_of < d <= end:
            events.append(PlannedEvent(
                id=f"bank:payroll:{kind}:{d.isoformat()}", direction=-1, amount=float(item["amount"]), date=d,
                p_within_horizon=0.99, category="salary", title=SALARY_TITLE.get(kind, "Зарплата"), source=SOURCE,
                mandatory=True, explain="Реестр зарплатного проекта в банке — сумма и дата уже известны"))
    for item in payload.get("pending_orders", []):
        d = _day(item["date"])
        category = item.get("category") if item.get("category") in TAXONOMY else "suppliers"
        if as_of < d <= end:
            number = str(item.get("number") or "")
            events.append(PlannedEvent(
                id=f"bank:order:{number or d.isoformat()}", direction=-1, amount=float(item["amount"]), date=d,
                p_within_horizon=1.0, category=category,
                title=f"Платёжное поручение{' № ' + number if number else ''}: {TAXONOMY[category].title.lower()}",
                source=SOURCE, mandatory=TAXONOMY[category].mandatory,
                explain=f"Создано в банке, ещё не исполнено: {rub(float(item['amount']))}"))
    return sorted(events, key=lambda e: (e.date, e.id))


def merge_bank(events: list[PlannedEvent], bank: list[PlannedEvent]) -> list[PlannedEvent]:
    """Данные банка точнее выведенных из истории: регулярные платежи тех же категорий заменяем.

    Если банк прислал график кредита или реестр зарплаты, регулярные события этой категории из истории убираем;
    платёжка убирает регулярное событие той же категории с близкой датой и суммой (это тот же платёж).
    """
    if not bank:
        return events
    covered = {e.category for e in bank if e.category in ("loan_repayment", "salary")}
    orders = [e for e in bank if e.id.startswith("bank:order:")]

    def same_payment(e: PlannedEvent) -> bool:
        return any(o.category == e.category and e.date and abs((o.date - e.date).days) <= DATE_MATCH_DAYS
                   and abs(o.amount - e.amount) <= AMOUNT_MATCH * max(e.amount, 1.0) for o in orders)

    kept = [e for e in events if not (e.source == "recurring" and (e.category in covered or same_payment(e)))]
    return kept + bank


def demo_bank_payload(ops_full: pd.DataFrame, as_of: date, horizon: int = 30) -> dict:
    """Демо-ответ банка из выписки с «будущим» (как демо-учётная система): платежи по кредитам в горизонте — это график,
    ближайшая выплата зарплаты — реестр зарплатного проекта, расходы первых двух рабочих дней — уже созданные платёжки.

    Нужны категоризированные операции одного бизнеса. В интерфейсе — «Данные банка (демо)».
    """
    as_of_ts, end = pd.Timestamp(as_of), pd.Timestamp(as_of + timedelta(days=horizon))
    future = ops_full[(ops_full["date"] > as_of_ts) & (ops_full["date"] <= end) & (ops_full["direction"] == -1)]
    if "reversal_pair_id" in future:
        future = future[future["reversal_pair_id"].isna()]
    loans = future[future["category"] == "loan_repayment"]
    schedule = [{"date": d.date().isoformat(), "amount": round(float(a), 2)}
                for d, a in loans.groupby("date")["amount"].sum().items()]
    salary = future[(future["category"] == "salary")
                    & (future["date"] <= as_of_ts + pd.Timedelta(days=PAYROLL_LOOKAHEAD_DAYS))]
    kind = salary["salary_kind"] if "salary_kind" in salary else pd.Series("final", index=salary.index)
    payroll = [{"date": d.date().isoformat(), "amount": round(float(g["amount"].sum()), 2),
                "kind": str(kind.loc[g.index].mode().iloc[0]) if len(g) else "final"}
               for d, g in salary.groupby("date")]
    workdays, d = [], as_of
    while len(workdays) < PENDING_WORKDAYS:
        d += timedelta(days=1)
        if is_workday(d):
            workdays.append(pd.Timestamp(d))
    pending = future[future["date"].isin(workdays) & ~future["category"].isin(NOT_PENDING)
                     & ~future["category"].isin({"owner_withdrawal", "own_transfer_out", "owner_loan_repayment"})]
    orders = [{"number": str(i + 1), "date": r.date.date().isoformat(), "amount": round(float(r.amount), 2),
               "category": r.category} for i, r in enumerate(pending.itertuples())]
    return {"loans": [{"contract": "демо", "schedule": schedule}] if schedule else [], "payroll": payroll,
            "pending_orders": orders, "note": "демо-данные банка, восстановлены из выписки"}
