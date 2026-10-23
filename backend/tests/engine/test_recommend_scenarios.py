"""Рекомендации с эффектом и сценарии «что если» на общих случайных числах."""
from datetime import date, timedelta

import numpy as np
import pytest

from app.engine.pipeline import build_model, evaluate, run_forecast, run_scenario
from app.engine.response import to_scenario_response
from app.engine.types import Settings
from app.schemas import (
    ClientDelayShock,
    FixedCostIncreaseShock,
    OneOffExpenseShock,
    RevenueDropShock,
    ScenarioResponse,
)
from tests.helpers import make_statement, merge, monthly, op

AS_OF = date(2026, 7, 31)


def business(extra=None):
    """Аренда 150 тыс. 5-го, зарплата 120 тыс. 25-го, поставщик 40 тыс. 12-го; клиент платит 270 тыс. 15-го."""
    days = merge(
        monthly("2025-07", 13, 5, "next", 150_000.0, "Арендная плата за помещение", inn="0099"),
        monthly("2025-07", 13, 25, "prev", 120_000.0, "Выплата заработной платы за месяц", inn=None),
        monthly("2025-07", 13, 12, "next", 40_000.0, "Оплата рекламных услуг", inn="0055", name="ООО «Реклама»"),
        monthly("2025-07", 13, 15, "next", 270_000.0, "Оплата по счету № 1 от 01.07.2025", inn="0044",
                name="ООО «Клиент»", sign=1),
        *(extra or []),
    )
    return days


def model(balance=200_000.0, settings=None, extra=None, planned=None):
    """Модель бизнеса с остатком `balance` на 31.07.2026 (остаток на начало истории подбирается)."""
    days = business(extra)
    net = sum(o["sign"] * o["amount"] for ops in days.values() for o in ops)
    return build_model(make_statement(days, start=balance - net), AS_OF, settings, planned, seed=5)


def test_green_forecast_has_no_recommendations():
    res = evaluate(model(balance=5_000_000.0), n_paths=300)
    assert res.risk.level == "green" and res.recommendations == []


def test_credit_line_is_last_and_closes_the_gap():
    res = evaluate(model(balance=-80_000.0), n_paths=600)
    assert res.risk.level == "red"
    assert res.recommendations[-1].type == "credit_line"
    credit = res.recommendations[-1]
    assert credit.effect.gap_probability_after < res.risk.gap_probability
    assert credit.effect.gap_probability_after <= 0.15
    assert [r.id for r in res.recommendations] == [f"r{i}" for i in range(1, len(res.recommendations) + 1)]
    assert "ставке" in credit.title and credit.deadline >= res.dates[0]


def test_own_transfer_history_gives_move_own_funds_with_effect():
    owner = {date(2026, 3, 10): [op(1, 400_000.0, "Перевод собственных средств", inn="0088")],
             date(2026, 5, 12): [op(1, 300_000.0, "Перевод собственных средств", inn="0088")]}
    res = evaluate(model(balance=-80_000.0, extra=[owner]), n_paths=600)
    move = next(r for r in res.recommendations if r.type == "move_own_funds")
    assert "вы так делали 2 раза" in move.title
    assert move.effect.gap_probability_after < res.risk.gap_probability
    assert res.recommendations.index(move) < res.recommendations.index(res.recommendations[-1])


def test_other_account_balance_caps_the_transfer_amount():
    settings = Settings(other_accounts=[{"bank": "Банк-4", "balance": 90_000.0}])
    res = evaluate(model(balance=-80_000.0, settings=settings), n_paths=400)
    move = next(r for r in res.recommendations if r.type == "move_own_funds")
    assert move.amount == 90_000.0 and "Банк-4" in move.title


def test_deferring_a_supplier_payment_closes_a_small_gap():
    # на 31.07 — 170 тыс.: после аренды 5-го остаётся 20 тыс., поставщику 12-го нужно 40 тыс., клиент платит 17-го
    res = evaluate(model(balance=170_000.0), n_paths=600)
    assert res.risk.level == "red" and res.risk.possible_gap_date == date(2026, 8, 12)
    deferral = next(r for r in res.recommendations if r.type == "defer_supplier")
    assert deferral.title == "Договоритесь с ООО «Реклама» перенести 40 000 ₽ с 12 августа на 26 августа"
    assert deferral.effect.gap_probability_after < 0.1 < res.risk.gap_probability
    assert res.recommendations.index(deferral) < len(res.recommendations) - 1        # раньше кредита


def test_owner_loan_history_gives_a_deposit_recommendation():
    loans = {date(2026, 2, 3): [op(1, 400_000.0, "Займ от учредителя по договору", inn="0088")]}
    res = evaluate(model(balance=-80_000.0, extra=[loans]), n_paths=500)
    move = next(r for r in res.recommendations if r.type == "move_own_funds")
    assert move.title.startswith("Внесите ") and "своих средств" in move.title and "вы так делали 1 раз" in move.title
    assert move.amount <= 600_000.0                                    # не больше 1,5 максимума прошлых вложений


def test_mandatory_payments_are_never_postponed():
    res = evaluate(model(balance=-80_000.0), n_paths=400)
    titles = " ".join(r.title.lower() for r in res.recommendations)
    assert "зарплат" not in titles and "налог" not in titles and "аренд" not in titles


def test_recommendation_effects_do_not_change_the_base_forecast():
    m = model(balance=-80_000.0)
    with_recs, without = evaluate(m, 300), evaluate(m, 300, with_recommendations=False)
    np.testing.assert_array_equal(with_recs.p50, without.p50)
    assert with_recs.risk.gap_probability == without.risk.gap_probability


# ---------- сценарии ----------
def test_one_off_expense_cannot_reduce_balance_percentiles_or_chance():
    m = model(balance=700_000.0)
    base, scen = run_scenario(m, [OneOffExpenseShock(type="one_off_expense", amount=150_000.0,
                                                     date=date(2026, 8, 12), title="Ремонт")], 500)
    assert (scen.p50 <= base.p50 + 1e-6).all()
    assert scen.risk.gap_probability >= base.risk.gap_probability
    np.testing.assert_allclose(base.p50[:11], scen.p50[:11])                 # до 12 августа всё совпадает
    assert base.p50[11] - scen.p50[11] == pytest.approx(150_000.0, abs=1.0)  # общие случайные числа → ровно 150 тыс.


def test_revenue_drop_does_not_increase_balance():
    m = model(balance=500_000.0)
    base, scen = run_scenario(m, [RevenueDropShock(type="revenue_drop", pct=30)], 400)
    assert (scen.p50 <= base.p50 + 1e-6).all()
    assert scen.risk.gap_probability >= base.risk.gap_probability


def test_client_delay_does_not_reduce_gap_probability_and_moves_the_client_payment():
    m = model(balance=400_000.0)
    base, scen = run_scenario(m, [ClientDelayShock(type="client_delay", counterparty_inn="0044", delay_days=21)], 500)
    assert scen.risk.gap_probability >= base.risk.gap_probability
    assert (scen.p50 <= base.p50 + 1e-6).all()
    client = next(e for e in scen.events if e.counterparty_inn == "0044")
    assert client.date == next(e for e in base.events if e.counterparty_inn == "0044").date + timedelta(days=21)


def test_largest_client_is_chosen_when_inn_is_not_given():
    m = model(balance=400_000.0)
    base, scen = run_scenario(m, [ClientDelayShock(type="client_delay", delay_days=14)], 300)
    assert (scen.p50 <= base.p50 + 1e-6).all() and (scen.p50 < base.p50 - 1.0).any()


def test_fixed_cost_increase_scales_selected_categories_only():
    m = model(balance=2_000_000.0)
    base, scen = run_scenario(m, [FixedCostIncreaseShock(type="fixed_cost_increase", pct=10, categories=["rent"])], 300)
    rent_b = next(e for e in base.events if e.category == "rent")
    rent_s = next(e for e in scen.events if e.category == "rent")
    assert rent_s.amount == pytest.approx(rent_b.amount * 1.1)
    assert next(e for e in scen.events if e.category == "salary").amount == next(
        e for e in base.events if e.category == "salary").amount


def test_scenario_summary_reads_like_the_spec():
    m = model(balance=400_000.0)
    shocks = [ClientDelayShock(type="client_delay", counterparty_inn="0044", delay_days=21)]
    base, scen = run_scenario(m, shocks, 500)
    resp = to_scenario_response(base, scen, shocks)
    assert isinstance(resp, ScenarioResponse)
    assert resp.summary.startswith("Если клиент заплатит на 3 недели позже,")
    ScenarioResponse.model_validate_json(resp.model_dump_json(by_alias=True))


def test_run_forecast_accepts_shocks_and_skips_recommendations():
    ops = make_statement(business(), start=200_000.0)
    res = run_forecast(ops, AS_OF, n_paths=200, seed=1,
                       shocks=[OneOffExpenseShock(type="one_off_expense", amount=50_000.0, date=date(2026, 8, 3))])
    assert res.recommendations == [] and any(e.category == "one_off" for e in res.events)
