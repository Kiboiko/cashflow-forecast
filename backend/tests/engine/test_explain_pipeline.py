"""explain: шаблоны текста; pipeline: сквозной прогноз на синтетике и на демо-компаниях."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.engine import explain
from app.engine.context import Context
from app.engine.pipeline import prepare, run_forecast
from app.engine.response import to_response
from app.engine.risk import RiskStats
from app.engine.types import PlannedEvent, Settings
from app.schemas import ForecastResponse
from tests.helpers import RAW_DIR, make_statement, merge, monthly, needs_dataset, op

AS_OF = date(2026, 7, 31)


def stats(level="red", p=0.64, gap=date(2026, 8, 25), p50=240_000.0, p90=410_000.0) -> RiskStats:
    return RiskStats(level=level, gap_probability=p, prob_below=np.zeros(30), possible_gap_date=gap,
                     likely_gap_date=gap, peak_date=gap, shortfall_p50=p50, shortfall_p90=p90, safe_buffer=p90,
                     min_p50=312_000.0, min_p50_date=date(2026, 8, 20))


def pay(d: date, amount: float, category: str, title: str, mandatory=True, explain_text="") -> PlannedEvent:
    return PlannedEvent(id=f"{category}:{d}", direction=-1, amount=amount, date=d, category=category, title=title,
                        mandatory=mandatory, explain=explain_text)


EVENTS = [pay(date(2026, 8, 25), 265_000.0, "salary", "Аванс по зарплате"),
          pay(date(2026, 8, 24), 120_000.0, "rent", "Аренда"),
          pay(date(2026, 8, 20), 5_000.0, "services", "Бухгалтерия", mandatory=False)]


def test_money_and_date_formatting():
    assert explain.rub(240_123) == "240 000 ₽"
    assert explain.rub(1_234_567) == "1 235 000 ₽"
    assert explain.rub(4_321) == "4 300 ₽"
    assert explain.dm(date(2026, 8, 5)) == "5 августа"


def test_plural_raz():
    assert [explain.plural_raz(n) for n in (1, 2, 4, 5, 11, 12, 21, 22)] == [
        "раз", "раза", "раза", "раз", "раз", "раз", "раз", "раза"]


def test_red_headline_names_the_biggest_mandatory_payments_in_accusative():
    text = explain.headline(stats(), EVENTS, 30)
    assert text == "25 августа может не хватить около 240 000 ₽ на аванс по зарплате и аренду. Шанс — высокий (64%)."


def test_yellow_and_green_headlines():
    assert explain.headline(stats("yellow", 0.25), EVENTS, 30) == (
        "Есть риск нехватки денег около 25 августа: в плохом сценарии до 410 000 ₽. Шанс — 25%.")
    assert explain.headline(stats("green", 0.02), EVENTS, 30) == (
        "В ближайшие 30 дней денег хватает на все обязательные платежи. "
        "Самый низкий остаток — около 312 000 ₽ (20 августа).")


def test_drivers_list_mandatory_outflows_first_and_advance_tax_note():
    tax = pay(date(2026, 8, 28), 300_000.0, "tax_enp", "Налоги (ЕНП)",
              explain_text="В августе налог обычно выше — похоже на авансовый платёж по УСН")
    drivers = explain.build_drivers(stats(), EVENTS + [tax], None, AS_OF)
    assert [d.title for d in drivers if d.kind == "outflow"][:2] == ["Аванс по зарплате", "Аренда"]
    assert any(d.kind == "deviation" and "авансовый" in d.text for d in drivers)
    assert explain.build_drivers(stats("green"), EVENTS, None, AS_OF) == []


def test_owner_money_insight_counts_distinct_days_in_the_last_year():
    days = {date(2026, 2, 3): [op(1, 100_000.0, "Займ от учредителя")],
            date(2026, 5, 4): [op(1, 50_000.0, "Перевод собственных средств")],
            date(2025, 3, 3): [op(1, 70_000.0, "Займ от учредителя")]}          # старше года — не считается
    from app.engine.categorize import categorize
    ops = categorize(make_statement(days))
    assert explain.owner_money_insight(ops, AS_OF)[0].text == (
        "За год вы 2 раза докладывали свои деньги, чтобы закрыть нехватку")
    assert explain.owner_money_insight(ops[ops["category"] == "unknown_in"], AS_OF) == []


# ---------- сквозной прогноз на синтетическом бизнесе ----------
def synthetic_business():
    """Аренда 150 000 ₽ каждое 5-е и зарплата 120 000 ₽ каждое 25-е при пополнениях от клиентов."""
    return merge(
        monthly("2025-07", 13, 5, "next", 150_000.0, "Арендная плата за помещение", inn="0099"),
        monthly("2025-07", 13, 25, "prev", 120_000.0, "Выплата заработной платы за месяц", inn=None),
        monthly("2025-07", 13, 15, "next", 270_000.0, "Оплата по счету № 1 от 01.07.2025", inn="0044", sign=1),
    )


def forecast(start_shift=0.0, **kwargs):
    ops = make_statement(synthetic_business(), start=200_000.0 + start_shift)
    return run_forecast(ops, AS_OF, n_paths=500, seed=7, **kwargs)


def test_pipeline_end_to_end_on_synthetic_business():
    res = forecast()
    assert res.dates[0] == AS_OF + timedelta(days=1) and len(res.dates) == res.horizon == 30
    assert (date(2026, 8, 5), "Аренда") in {(e.date, e.title) for e in res.events}
    assert any(e.date == date(2026, 8, 25) and e.category == "salary" for e in res.events)
    assert (res.p10 <= res.p50).all() and (res.p50 <= res.p90).all()
    assert res.risk.level in {"green", "yellow", "red"}
    assert res.model_info["version"] == "structural-mc-1.0" and "recurring" in res.model_info["components"]


def test_pipeline_is_deterministic_by_seed():
    a, b = forecast(), forecast()
    np.testing.assert_array_equal(a.p50, b.p50)
    assert a.risk.gap_probability == b.risk.gap_probability


def test_low_starting_balance_gives_red_and_names_the_payments():
    res = forecast(start_shift=-250_000.0)
    assert res.risk.level == "red"
    assert "может не хватить" in res.risk.headline
    assert res.risk.possible_gap_date is not None
    assert any(c.covered_probability < 0.5 for c in res.risk.mandatory_coverage)


def test_response_matches_contract_and_event_json_roundtrip():
    resp = to_response(forecast(), "T001")
    again = ForecastResponse.model_validate_json(resp.model_dump_json(by_alias=True))
    assert again == resp and resp.daily[0].date == AS_OF + timedelta(days=1)
    assert {e.direction for e in resp.events} <= {"in", "out"}


def test_planned_manual_event_changes_the_forecast():
    base = forecast(start_shift=-100_000.0)
    promise = PlannedEvent(id="manual:1", direction=1, amount=300_000.0, date=date(2026, 8, 20),
                           category="manual", title="Клиент обещал оплатить", source="manual", amount_cv=0.0)
    with_plan = forecast(start_shift=-100_000.0, planned=[promise])
    assert with_plan.p50[19] - base.p50[19] == pytest.approx(300_000.0, rel=0.001)
    assert any(e.id == "manual:1" for e in with_plan.events)


def test_owner_withdrawal_switch_removes_the_stream():
    days = merge(synthetic_business(),
                 monthly("2025-07", 13, 20, "next", 80_000.0, "Перевод на личную карту владельца", inn="0033"))
    ops = make_statement(days, start=2_000_000.0)
    on = run_forecast(ops, AS_OF, Settings(), n_paths=200, seed=1)
    off = run_forecast(ops, AS_OF, Settings(include_owner_withdrawals=False), n_paths=200, seed=1)
    assert any(e.category == "owner_withdrawal" for e in on.events)
    assert not any(e.category == "owner_withdrawal" for e in off.events)


def test_inactive_business_is_not_forecast():
    ops = make_statement({date(2026, 3, 2): [op(1, 10_000.0, "Оплата по счету 5 от 01.01.2026")],
                          date(2026, 3, 3): [op(-1, 1_000.0, "Аренда помещения")]}, start=50_000.0)
    res = run_forecast(ops, AS_OF, n_paths=100, seed=1)
    assert res.data_quality.inactive and "inactive" in res.risk.flags
    assert "прогноз не строим" in res.risk.headline
    assert (res.p50 == res.start_balance).all()


def test_other_accounts_are_added_only_when_requested():
    ops = make_statement(synthetic_business(), start=200_000.0)
    s = Settings(other_accounts=[{"bank": "Банк-4", "balance": 500_000.0}])
    plain = run_forecast(ops, AS_OF, s, n_paths=100, seed=1)
    full = run_forecast(ops, AS_OF, Settings(other_accounts=s.other_accounts, include_other_accounts=True),
                        n_paths=100, seed=1)
    assert full.start_balance - plain.start_balance == 500_000.0


def test_prepare_drops_future_operations():
    ops = make_statement(synthetic_business(), start=200_000.0)
    prepared = prepare(ops, date(2026, 3, 31))
    assert prepared.ops["date"].max() <= pd.Timestamp(2026, 3, 31)


# ---------- демо-компании на реальных данных ----------
@needs_dataset
@pytest.mark.parametrize("company", ["C064", "C103", "C107"])
def test_demo_companies_forecast_on_real_data(company):
    from app.engine.io import load_statement
    from app.engine.normalize import normalize

    ops = normalize(load_statement(RAW_DIR, company))
    res = run_forecast(ops, AS_OF, context=Context(), n_paths=300)
    assert res.risk.mandatory_coverage, "должны быть найдены обязательные платежи"
    assert (res.p10 <= res.p50).all() and (res.p50 <= res.p90).all()
    assert res.risk.level == "red" and res.risk.possible_gap_date <= date(2026, 8, 20)   # скрытый разрыв в августе
    ForecastResponse.model_validate(to_response(res, company).model_dump())
