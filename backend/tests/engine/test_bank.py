"""Мок-адаптер «данные банка»: события из JSON банка и замена ими регулярных платежей из истории."""
from datetime import date, timedelta

import pandas as pd

from app.engine.bank import bank_events, demo_bank_payload, merge_bank
from app.engine.categorize import categorize
from app.engine.enrich import enrich
from app.engine.types import PlannedEvent
from tests.helpers import make_statement, merge, monthly

AS_OF = date(2026, 7, 31)
PAYLOAD = {
    "loans": [{"contract": "КД-17", "schedule": [{"date": "2026-08-03", "amount": 51_000}, {"date": "2026-09-30", "amount": 51_000}]}],
    "payroll": [{"date": "2026-08-10", "amount": 300_000, "kind": "final"}],
    "pending_orders": [{"number": "245", "date": "2026-08-03", "amount": 45_000, "category": "suppliers"},
                       {"number": "246", "date": "2026-08-04", "amount": 150_000, "category": "rent"}],
}


def test_bank_events_cover_loans_payroll_and_pending_orders_within_the_horizon():
    events = {e.id: e for e in bank_events(PAYLOAD, AS_OF, 30)}
    assert set(events) == {"bank:loan:КД-17:2026-08-03", "bank:payroll:final:2026-08-10", "bank:order:245", "bank:order:246"}
    loan, salary, rent = events["bank:loan:КД-17:2026-08-03"], events["bank:payroll:final:2026-08-10"], events["bank:order:246"]
    assert loan.mandatory and loan.category == "loan_repayment" and loan.amount_cv == 0 and loan.source == "bank_mock"
    assert salary.title == "Расчёт по зарплате" and salary.mandatory
    assert rent.category == "rent" and rent.mandatory and rent.p_within_horizon == 1.0 and "№ 246" in rent.title
    assert bank_events(None, AS_OF) == [] and bank_events({}, AS_OF) == []


def _recurring(category: str, d: date, amount: float) -> PlannedEvent:
    return PlannedEvent(id=f"rec:{category}:{d}", direction=-1, amount=amount, date=d, category=category, source="recurring")


def test_bank_data_replace_recurring_payments_of_the_same_kind():
    history = [_recurring("loan_repayment", date(2026, 8, 3), 50_000), _recurring("salary", date(2026, 8, 10), 280_000),
               _recurring("rent", date(2026, 8, 5), 150_000), _recurring("telecom_it", date(2026, 8, 6), 8_000)]
    merged = merge_bank(history, bank_events(PAYLOAD, AS_OF, 30))
    ids = {e.id for e in merged}
    assert "rec:telecom_it:2026-08-06" in ids                                   # банк про связь ничего не знает
    assert not any(e.id.startswith(("rec:loan_repayment", "rec:salary", "rec:rent")) for e in merged)
    assert sum(e.category == "rent" for e in merged) == 1                        # аренда не задвоилась с платёжкой
    assert merge_bank(history, []) == history


def test_demo_payload_from_the_statement_future():
    days = merge(
        monthly("2025-07", 15, 1, "next", 51_000.0, "Погашение кредита по договору № 17", inn="0068275202"),
        monthly("2025-07", 15, 5, "prev", 300_000.0, "Окончательный расчет по заработной плате", inn=None),
        monthly("2025-07", 15, 25, "prev", 200_000.0, "Выплата аванса по заработной плате", inn=None),
        {date(2026, 8, 3): [{"sign": -1, "amount": 45_000.0, "purpose": "Оплата по счету № 5 за товар", "inn": "0055",
                             "name": "ООО «Поставщик»", "counted": True}]},
    )
    ops = enrich(categorize(make_statement(days, start=5_000_000.0)))
    payload = demo_bank_payload(ops, AS_OF, 30)
    assert payload["loans"][0]["schedule"] == [{"date": "2026-08-03", "amount": 51_000.0}]
    assert payload["payroll"] == [{"date": "2026-08-05", "amount": 300_000.0, "kind": "final"}]   # аванс 25-го — дальше 7 дней
    assert [o["category"] for o in payload["pending_orders"]] == ["suppliers"]
    assert all(pd.Timestamp(o["date"]) <= pd.Timestamp(AS_OF + timedelta(days=4)) for o in payload["pending_orders"])


def test_model_with_bank_data_uses_the_bank_schedule_instead_of_history():
    from app.engine.pipeline import build_model, evaluate

    days = merge(monthly("2025-07", 13, 1, "next", 51_000.0, "Погашение кредита по договору № 17", inn="0068275202"),
                 monthly("2025-07", 13, 5, "next", 150_000.0, "Арендная плата за помещение", inn="0099"))
    ops = make_statement(days, start=2_000_000.0)
    plain = build_model(ops, AS_OF, seed=1)
    with_bank = build_model(ops, AS_OF, seed=1, bank=PAYLOAD)
    assert any(e.source == "recurring" and e.category == "loan_repayment" for e in plain.events)
    assert not any(e.source == "recurring" and e.category in ("loan_repayment", "rent") for e in with_bank.events)
    assert {e.id for e in with_bank.events if e.source == "bank_mock"} >= {"bank:loan:КД-17:2026-08-03", "bank:order:246"}
    assert "bank" in evaluate(with_bank, 200).model_info["components"]
