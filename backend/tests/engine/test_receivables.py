"""Счета, договоры и задержки клиентов: профили, события с распределением даты, выставление × задержка."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.engine.calendar_ru import is_workday
from app.engine.categorize import categorize
from app.engine.enrich import enrich
from app.engine.pipeline import build_model, evaluate
from app.engine.plan import manual_event
from app.engine.receivables import (
    FutureInvoices,
    Invoice,
    build_profiles,
    contract_events,
    fit_future_invoices,
    invoice_events,
    invoices_from_frame,
    payment_pmf,
    receivables_summary,
    restore_open_documents,
)
from app.engine.scenarios import INVOICE_LEVEL_CV, SharedFactor
from app.engine.simulate import FixedEvent, simulate
from app.engine.types import PlannedEvent
from app.schemas import ClientDelayShock, EventCreate
from tests.helpers import make_statement, merge, monthly, op

AS_OF = date(2026, 7, 31)


def paid_docs(inn: str, delays: list[int], first_doc: date = date(2025, 9, 1), amount: float = 100_000.0,
              name: str = "ООО «Клиент»", step: int = 14) -> dict[date, list[dict]]:
    """Клиент платит по документам (УПД), выставленным раз в `step` дней, с заданными задержками."""
    days: dict[date, list[dict]] = {}
    for k, delay in enumerate(delays):
        doc_date = first_doc + timedelta(days=step * k)
        pay = doc_date + timedelta(days=delay)
        while not is_workday(pay):
            pay += timedelta(days=1)
        text = f"Оплата по УПД № {inn[-2:]}{k} от {doc_date:%d.%m.%Y} за товар"
        days.setdefault(pay, []).append(op(1, amount, text, inn=inn, name=name))
    return days


def prepared_ops(days):
    return enrich(categorize(make_statement(days, start=1_000_000.0)))


REGULAR = [20, 22, 25, 18, 21, 23, 19, 24]            # клиент 0044: платит через ~21±2 дня
SLOW = [60, 55, 70, 62, 58, 65]                       # клиент 0066: платит через ~60 дней


def test_delay_profile_levels_fall_back_from_client_to_company_to_default():
    days = merge(paid_docs("0044", REGULAR), paid_docs("0066", SLOW), paid_docs("0099", [30, 31]))
    profiles = build_profiles(prepared_ops(days), AS_OF)
    assert profiles.get(1, "0044").level == "client" and 19 <= profiles.get(1, "0044").median <= 23
    assert profiles.get(1, "0066").median > 55
    new_client = profiles.get(1, "0099")                                  # всего 2 оплаты → профиль компании
    assert new_client.level == "company" and new_client.n >= 10
    assert build_profiles(prepared_ops({date(2026, 3, 2): [op(1, 10.0, "Оплата")]}), AS_OF).get(1, "x").level == "default"


def test_open_invoice_gets_a_conditional_payment_distribution():
    profiles = build_profiles(prepared_ops(paid_docs("0044", REGULAR)), AS_OF)
    young = Invoice("77", AS_OF - timedelta(days=5), "in", 200_000.0, "0044", "ООО «Клиент»")
    pmf, overdue = payment_pmf(young, profiles.get(1, "0044"), AS_OF)
    assert not overdue and sum(p for _, p in pmf) == pytest.approx(1.0)
    lo, hi = min(d for d, _ in pmf), max(d for d, _ in pmf)
    assert AS_OF + timedelta(days=11) <= lo and hi <= AS_OF + timedelta(days=33)     # задержки 18–35 дней от выставления
    assert all(is_workday(d) for d, _ in pmf)


def test_invoice_older_than_every_known_delay_is_overdue_by_the_clients_standards():
    profiles = build_profiles(prepared_ops(paid_docs("0044", REGULAR)), AS_OF)
    old = Invoice("5", AS_OF - timedelta(days=50), "in", 200_000.0, "0044", "ООО «Клиент»")
    pmf, overdue = payment_pmf(old, profiles.get(1, "0044"), AS_OF)
    assert overdue and sum(p for _, p in pmf) == pytest.approx(0.5)
    assert max(d for d, _ in pmf) <= AS_OF + timedelta(days=30) and all(is_workday(d) for d, _ in pmf)
    event = invoice_events([old], profiles, AS_OF, 30)[0]
    assert "просрочен по меркам клиента" in event.explain and event.p_within_horizon == pytest.approx(0.5, abs=0.001)


def test_invoice_event_fields_and_explanation():
    profiles = build_profiles(prepared_ops(paid_docs("0044", REGULAR)), AS_OF)
    inv = Invoice("77", AS_OF - timedelta(days=5), "in", 200_000.0, "0044", "ООО «Клиент»", source="csv")
    event = invoice_events([inv], profiles, AS_OF, 30)[0]
    assert event.id == "inv:in:77" and event.direction == 1 and event.category == "client_payment"
    assert event.counterparty_inn == "0044" and event.source == "csv" and not event.mandatory
    assert event.date is not None and event.date_pmf and 0.85 <= event.p_within_horizon <= 1.0   # часть задержек >30 дн.
    assert "этот клиент обычно платит через 2" in event.explain and "200 000 ₽" in event.explain


def test_supplier_bill_with_due_date_is_paid_around_the_due_date():
    profiles = build_profiles(prepared_ops({date(2026, 3, 2): [op(1, 10.0, "Оплата")]}), AS_OF)
    bill = Invoice("A1", AS_OF - timedelta(days=3), "out", 55_000.0, "0077", "ООО «Поставщик»",
                   due_date=AS_OF + timedelta(days=10))
    event = invoice_events([bill], profiles, AS_OF, 30)[0]
    assert event.direction == -1 and event.category == "suppliers"
    assert min(d for d, _ in event.date_pmf) >= AS_OF + timedelta(days=8)
    assert max(d for d, _ in event.date_pmf) <= AS_OF + timedelta(days=13)


def test_future_invoices_expected_flow_and_sampling():
    days = merge(paid_docs("0044", REGULAR * 8, first_doc=date(2025, 3, 3), step=7))
    ops = prepared_ops(days)
    profiles = build_profiles(ops, AS_OF)
    future = fit_future_invoices(ops, AS_OF, 30, profiles, 1)
    assert isinstance(future, FutureInvoices) and future.per_workday > 0
    expected = future.expected()
    assert expected.sum() > 0 and (expected[:10] == 0).all()                 # раньше ~15-го дня оплат от новых счетов нет
    sims = future.sample(future.dates, 4000, np.random.default_rng(1))
    assert sims.shape == (4000, 30) and (sims >= 0).all()
    assert sims.sum(axis=1).mean() == pytest.approx(expected.sum(), rel=0.12)
    again = future.sample(future.dates, 4000, np.random.default_rng(1))
    np.testing.assert_array_equal(sims, again)


def test_future_invoices_need_enough_documents():
    ops = prepared_ops(paid_docs("0044", [20, 21, 22]))
    assert fit_future_invoices(ops, AS_OF, 30, build_profiles(ops, AS_OF), 1) is None


def test_restore_open_documents_from_the_statement():
    days = paid_docs("0044", REGULAR)
    future_doc = AS_OF - timedelta(days=5)
    pay = AS_OF + timedelta(days=12)
    days.setdefault(pay, []).append(op(1, 120_000.0, f"Оплата по УПД № 900 от {future_doc:%d.%m.%Y}", inn="0044"))
    days.setdefault(pay + timedelta(days=3), []).append(
        op(1, 30_000.0, f"Оплата по УПД № 900 от {future_doc:%d.%m.%Y}", inn="0044"))        # вторая часть оплаты
    ops = prepared_ops(days)
    invoices = restore_open_documents(ops, AS_OF)
    assert [(i.doc_no, i.amount, i.direction) for i in invoices] == [("900", 150_000.0, "in")]
    assert invoices[0].source == "demo_accounting" and invoices[0].doc_date == future_doc


def test_contract_events_from_prepayments_without_final_settlement():
    days = {}
    for k in range(8):                                                       # 8 закрытых договоров: аванс 57%, расчёт через 35 дней
        start = date(2025, 9, 1) + timedelta(days=10 * k)
        days.setdefault(start, []).append(op(1, 57_000.0, f"Предоплата по договору № {k}-25 от {start:%d.%m.%Y}",
                                             inn="0055", name="ООО «Заказчик»"))
        end = start + timedelta(days=35)
        days.setdefault(end, []).append(op(1, 43_000.0, f"Окончательный расчет по договору № {k}-25 от {start:%d.%m.%Y}",
                                           inn="0055", name="ООО «Заказчик»"))
    open_start = AS_OF - timedelta(days=20)
    days.setdefault(open_start, []).append(op(1, 80_000.0, f"Предоплата по договору № 99-26 от {open_start:%d.%m.%Y}",
                                              inn="0055", name="ООО «Заказчик»"))
    events = contract_events(prepared_ops(days), AS_OF, 30)
    assert len(events) == 1
    e = events[0]
    assert e.id == "contract:99-26" and e.source == "contract_detected" and e.direction == 1
    assert e.amount == pytest.approx(80_000.0 * 43 / 57, rel=0.01)           # предоплата × (1 − s) / s
    assert min(d for d, _ in e.date_pmf) >= AS_OF + timedelta(days=13)       # лаг 35 дней − 20 уже прошло − шум ±2


def test_receivables_summary_lists_clients_with_typical_delay():
    profiles = build_profiles(prepared_ops(merge(paid_docs("0044", REGULAR), paid_docs("0066", SLOW))), AS_OF)
    invoices = [Invoice("1", AS_OF - timedelta(days=40), "in", 100_000.0, "0044", "ООО «Клиент»"),
                Invoice("2", AS_OF - timedelta(days=10), "in", 300_000.0, "0066", "ООО «Медленный»"),
                Invoice("3", AS_OF - timedelta(days=5), "out", 50_000.0, "0077", "ООО «Поставщик»")]
    rows = receivables_summary(invoices, profiles, AS_OF, "in")
    assert [r["counterparty_inn"] for r in rows] == ["0066", "0044"] and len(rows) == 2     # по убыванию долга, без «out»
    regular = next(r for r in rows if r["counterparty_inn"] == "0044")
    assert regular["overdue_amount"] == 100_000.0 and regular["invoices"][0]["overdue_by_client_standard"]
    assert 19 <= regular["typical_delay_days"] <= 23 and regular["profile_level"] == "client"


def test_invoices_from_csv_frame():
    df = pd.DataFrame([{"doc_type": "upd", "doc_no": "12", "doc_date": "2026-07-20", "direction": "in",
                        "counterparty_inn": "0044", "counterparty_name": "ООО «Клиент»", "amount": "150000.5",
                        "due_date": ""},
                       {"doc_type": "invoice", "doc_no": "A5", "doc_date": "2026-07-25", "direction": "out",
                        "counterparty_inn": None, "counterparty_name": None, "amount": 9000, "due_date": "2026-08-10"}])
    a, b = invoices_from_frame(df)
    assert (a.doc_no, a.amount, a.due_date, a.counterparty_inn) == ("12", 150000.5, None, "0044")
    assert b.due_date == date(2026, 8, 10) and b.counterparty_inn is None and b.direction == "out"


def test_invoices_csv_with_russian_dates_comma_amounts_and_leading_zero_inn(tmp_path):
    from app.engine.io import load_invoices

    path = tmp_path / "invoices.csv"
    path.write_text("doc_type;doc_no;doc_date;direction;counterparty_inn;counterparty_name;amount;due_date\n"
                    "upd;45;05.08.2026;in;0044123456;ООО «Клиент»;150 000,50;\n"
                    "invoice;A7;2026-07-03;out;;;9000;12.08.2026\n", encoding="utf-8-sig")
    a, b = invoices_from_frame(load_invoices(path))
    assert a.doc_date == date(2026, 8, 5) and a.amount == 150_000.5 and a.counterparty_inn == "0044123456"
    assert a.due_date is None and b.counterparty_inn is None and b.counterparty_name is None
    assert b.doc_date == date(2026, 7, 3) and b.due_date == date(2026, 8, 12)
    with pytest.raises(ValueError, match="Строка 1"):
        invoices_from_frame(pd.DataFrame([{"doc_no": "1", "doc_date": "", "direction": "in", "amount": "1"}]))


def test_manual_event_mapping():
    e = manual_event(EventCreate(title="Клиент обещал оплатить", direction="in", amount=300_000.0,
                                 date=date(2026, 8, 20), counterparty_name="ООО «Ромашка»", probability=0.8), 7)
    assert (e.id, e.direction, e.amount, e.source, e.p_within_horizon) == ("manual:7", 1, 300_000.0, "manual", 0.8)
    assert "уверенность 80%" in e.explain


# ---------- в модели и сценариях ----------
def business_with_clients():
    return merge(
        monthly("2025-07", 13, 5, "next", 150_000.0, "Арендная плата за помещение", inn="0099"),
        paid_docs("0044", REGULAR * 3, first_doc=date(2025, 7, 7), amount=250_000.0),
    )


def test_model_with_invoices_uses_explicit_events_instead_of_the_client_bootstrap():
    ops = make_statement(business_with_clients(), start=900_000.0)
    ops = ops[ops["date"] <= pd.Timestamp(AS_OF)]
    invoices = [Invoice("77", AS_OF - timedelta(days=5), "in", 250_000.0, "0044", "ООО «Клиент»")]
    plain = build_model(ops, AS_OF, seed=1)
    connected = build_model(ops, AS_OF, seed=1, invoices=invoices)
    assert plain.invoices is None and not plain.future
    assert any(e.id == "inv:in:77" for e in connected.events)
    assert connected.future and connected.future[0].direction == 1
    assert not any(r.direction == 1 and "client_payment" in r.category_share for r in connected.rates)
    assert any(r.direction == 1 and "client_payment" in r.category_share for r in plain.rates)
    result = evaluate(connected, 300)
    assert "invoices" in result.model_info["components"]


def test_client_delay_shock_moves_invoice_payment_dates():
    ops = make_statement(business_with_clients(), start=900_000.0)
    ops = ops[ops["date"] <= pd.Timestamp(AS_OF)]
    invoices = [Invoice("77", AS_OF - timedelta(days=5), "in", 250_000.0, "0044", "ООО «Клиент»")]
    model = build_model(ops, AS_OF, seed=1, invoices=invoices)
    from app.engine.scenarios import apply_shocks

    base_events, _ = apply_shocks(model, [])
    late_events, _ = apply_shocks(model, [ClientDelayShock(type="client_delay", counterparty_inn="0044", delay_days=21)])
    base = next(e for e in base_events if e.id == "inv:in:77")
    late = next(e for e in late_events if e.id == "inv:in:77")
    assert late.date == base.date + timedelta(days=21)
    assert min(d for d, _ in late.date_pmf) == min(d for d, _ in base.date_pmf) + timedelta(days=21)


def test_client_delay_with_invoices_moves_the_clients_share_of_future_invoices():
    ops = make_statement(business_with_clients(), start=900_000.0)
    ops = ops[ops["date"] <= pd.Timestamp(AS_OF)]
    invoices = [Invoice("77", AS_OF - timedelta(days=5), "in", 250_000.0, "0044", "ООО «Клиент»")]
    model = build_model(ops, AS_OF, seed=1, invoices=invoices)
    assert model.future and not any(r.direction == 1 for r in model.rates)        # оплаты клиентов ведут счета
    from app.engine.scenarios import _client_flows, apply_shocks

    np.testing.assert_allclose(_client_flows(model, "0044"), model.future[0].expected())   # единственный клиент
    _, components = apply_shocks(model, [ClientDelayShock(type="client_delay", counterparty_inn="0044", delay_days=21)])
    assert any(c.key.startswith("shock:client_delay") for c in components)


def test_client_and_supplier_invoices_get_separate_shared_factors():
    ops = make_statement(business_with_clients(), start=900_000.0)
    ops = ops[ops["date"] <= pd.Timestamp(AS_OF)]
    invoices = [Invoice("77", AS_OF - timedelta(days=5), "in", 250_000.0, "0044", "ООО «Клиент»"),
                Invoice("S1", AS_OF - timedelta(days=3), "out", 100_000.0, "0077", "ООО «Поставщик»",
                        due_date=AS_OF + timedelta(days=6))]
    from app.engine.scenarios import apply_shocks

    _, components = apply_shocks(build_model(ops, AS_OF, seed=1, invoices=invoices), [])
    names = {c.key: c.name for c in components if isinstance(c, SharedFactor)}
    assert names["event:inv:in:77"] == "invoices:in" and names["event:inv:out:S1"] == "invoices:out"


def slow_client_business(balance: float, rent: float = 150_000.0, rent_day: int = 5):
    """Аренда `rent` `rent_day`-го; клиент 0066 платит по УПД через ~60 дней; остаток `balance` на 31.07.2026."""
    days = merge(monthly("2025-07", 13, rent_day, "next", rent, "Арендная плата за помещение", inn="0099"),
                 paid_docs("0066", SLOW * 3, first_doc=date(2025, 3, 3), amount=200_000.0, name="ООО «Медленный»",
                           step=21))
    days = {d: v for d, v in days.items() if d <= AS_OF}
    net = sum(o["sign"] * o["amount"] for ops in days.values() for o in ops)
    return make_statement(days, start=balance - net)


def test_open_invoice_paid_after_the_gap_gives_a_collect_receivable_recommendation():
    invoices = [Invoice("901", AS_OF - timedelta(days=5), "in", 400_000.0, "0066", "ООО «Медленный»")]
    res = evaluate(build_model(slow_client_business(50_000.0), AS_OF, seed=2, invoices=invoices), 600)
    assert res.risk.level == "red" and res.risk.possible_gap_date == date(2026, 8, 5)
    collect = next(r for r in res.recommendations if r.type == "collect_receivable")
    assert collect.title == "Попросите ООО «Медленный» оплатить документ № 901 (400 000 ₽) до 4 августа"
    assert collect.deadline == date(2026, 8, 4) and collect.amount == 400_000.0
    assert collect.effect.gap_probability_after < 0.1 < res.risk.gap_probability
    assert res.recommendations.index(collect) < len(res.recommendations) - 1          # раньше кредита


def test_invoice_expected_before_the_gap_is_not_worth_chasing():
    # счёту 58 дней: клиент заплатит до ~14 августа, а разрыв — 25-го, на аренде; просить ускорить нечего
    invoices = [Invoice("902", AS_OF - timedelta(days=58), "in", 400_000.0, "0066", "ООО «Медленный»")]
    ops = slow_client_business(50_000.0, rent=900_000.0, rent_day=25)
    res = evaluate(build_model(ops, AS_OF, seed=2, invoices=invoices), 600)
    assert res.risk.level == "red" and any(e.id == "inv:in:902" for e in res.events)
    assert res.recommendations and not any(r.type == "collect_receivable" for r in res.recommendations)


def test_shared_factor_scales_all_wrapped_components_by_the_same_multiplier():
    e1 = PlannedEvent(id="a", direction=-1, amount=100.0, date=AS_OF + timedelta(days=2), amount_cv=0.0)
    e2 = PlannedEvent(id="b", direction=-1, amount=300.0, date=AS_OF + timedelta(days=4), amount_cv=0.0)
    dates = [AS_OF + timedelta(days=i) for i in range(1, 11)]
    plain = simulate(0.0, dates, [FixedEvent(e1), FixedEvent(e2)], 500, seed=3, keep_components=True)
    shared = simulate(0.0, dates, [SharedFactor(FixedEvent(e1), 3, "invoices", INVOICE_LEVEL_CV),
                                   SharedFactor(FixedEvent(e2), 3, "invoices", INVOICE_LEVEL_CV)], 500, seed=3,
                      keep_components=True)
    ratio_a = shared.components["event:a"][:, 1] / plain.components["event:a"][:, 1]
    ratio_b = shared.components["event:b"][:, 3] / plain.components["event:b"][:, 3]
    np.testing.assert_allclose(ratio_a, ratio_b)                             # один множитель на сценарий
    assert ratio_a.std() > 0.1 and ratio_a.mean() == pytest.approx(1.0, abs=0.05)
