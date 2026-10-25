"""Граничные случаи (§12): короткая история, закрытый счёт, овердрафт, дыры, пропуски, странные данные."""
from datetime import date, timedelta

import numpy as np

from app.engine.io import load_companies, load_statement
from app.engine.normalize import normalize
from app.engine.pipeline import run_forecast
from app.engine.response import to_response
from app.engine.types import Settings
from tests.helpers import RAW_DIR, make_statement, merge, monthly, needs_dataset, op

AS_OF = date(2026, 7, 31)


def sane(result):
    assert np.isfinite(result.p50).all() and (result.p10 <= result.p50 + 1e-6).all()
    assert (result.p50 <= result.p90 + 1e-6).all() and 0.0 <= result.risk.gap_probability <= 1.0
    to_response(result, "T001")                                              # валидный контракт


def test_history_shorter_than_90_days_is_flagged_and_still_forecast():
    days = {AS_OF - timedelta(days=60) + timedelta(days=i): [op(1, 10_000.0, "Оплата по счету № 1 от 01.01.2026"),
                                                             op(-1, 7_000.0, "Оплата по УПД № 5 от 01.01.2026 за товар")]
            for i in range(60) if (AS_OF - timedelta(days=60) + timedelta(days=i)).weekday() < 5}
    result = run_forecast(make_statement(days, start=100_000.0), AS_OF, n_paths=200, seed=1)
    sane(result)
    assert result.data_quality.history == "short_history" and "short_history" in result.risk.flags
    assert any(i.kind == "history" and "ориентировочный" in i.text for i in result.insights)


def test_closed_account_is_not_forecast_but_explained():
    days = {date(2026, 3, 2): [op(1, 50_000.0, "Оплата по счету № 1 от 01.01.2026")],
            date(2026, 3, 3): [op(-1, 10_000.0, "Аренда помещения")]}
    result = run_forecast(make_statement(days, start=20_000.0), AS_OF, n_paths=100, seed=1)
    sane(result)
    assert result.data_quality.inactive and (result.p50 == result.start_balance).all()
    assert "прогноз не строим" in result.risk.headline


def test_overdraft_limit_moves_the_threshold_and_a_hint_appears_when_the_limit_is_unknown():
    days = merge(monthly("2025-07", 13, 5, "next", 150_000.0, "Арендная плата за помещение", inn="0099"),
                 monthly("2025-07", 13, 20, "next", 12_000.0, "Проценты по договору кредитной линии, овердрафт",
                         inn="0068275202"))
    ops = make_statement(days, start=-sum(o["sign"] * o["amount"] for v in days.values() for o in v) + 100_000.0)
    no_limit = run_forecast(ops, AS_OF, Settings(), n_paths=200, seed=1)
    with_limit = run_forecast(ops, AS_OF, Settings(overdraft_limit=2_000_000.0), n_paths=200, seed=1)
    assert "overdraft_hint" in no_limit.risk.flags and "overdraft_hint" not in with_limit.risk.flags
    assert with_limit.threshold == -2_000_000.0 and with_limit.risk.gap_probability < no_limit.risk.gap_probability


def test_negative_start_balance_does_not_break_anything():
    ops = make_statement(monthly("2025-07", 13, 5, "next", 1_000.0, "Арендная плата за помещение", inn="0099"),
                         start=-500_000.0)
    result = run_forecast(ops, AS_OF, n_paths=200, seed=1)
    sane(result)
    assert result.start_balance < 0 and result.risk.level == "red"


def test_everything_unrecognised_still_gives_a_forecast():
    days = {AS_OF - timedelta(days=i): [op(1 if i % 2 else -1, 1_000.0 + i, "Загадочный платёж", inn=None, name="???")]
            for i in range(1, 120) if (AS_OF - timedelta(days=i)).weekday() < 5}
    result = run_forecast(make_statement(days, start=500_000.0), AS_OF, n_paths=200, seed=1)
    sane(result)
    assert result.data_quality.unknown_ops > 50


def test_empty_horizon_inputs_and_large_horizon():
    days = monthly("2025-07", 13, 5, "next", 100_000.0, "Арендная плата за помещение", inn="0099")
    ops = make_statement(days, start=3_000_000.0)
    for horizon in (7, 60, 90):
        result = run_forecast(ops, AS_OF, horizon=horizon, n_paths=100, seed=1)
        sane(result)
        assert len(result.dates) == horizon


def test_data_gap_is_reported_and_marked_for_the_user():
    days = monthly("2025-07", 13, 5, "next", 100_000.0, "Арендная плата за помещение", inn="0099")
    ops = make_statement(days, start=3_000_000.0, jumps={date(2026, 7, 6): -400_000.0})
    result = run_forecast(ops, AS_OF, n_paths=100, seed=1)
    sane(result)
    assert [g.end for g in result.data_quality.gaps] == [date(2026, 7, 6)] and "data_gap" in result.risk.flags


def test_weekend_as_of_and_holiday_as_of():
    days = monthly("2025-07", 13, 5, "next", 100_000.0, "Арендная плата за помещение", inn="0099")
    ops = make_statement(days, start=3_000_000.0)
    for as_of in (date(2026, 8, 1), date(2026, 6, 12)):                      # суббота и праздник
        sane(run_forecast(ops, as_of, n_paths=100, seed=1))


@needs_dataset
def test_every_kind_of_company_in_the_dataset_gets_a_forecast():
    """Короткие истории, закрытые счета и случайные компании на живой дате 30.09.2026."""
    companies = load_companies(RAW_DIR / "companies.csv")
    months = (companies["last_operation_date"] - companies["first_operation_date"]).dt.days / 30.44
    short = companies[months < 13]["company_id"].tolist()
    closed = companies[companies["last_operation_date"] < "2026-09-01"]["company_id"].tolist()
    sample = companies["company_id"].sample(10, random_state=0).tolist()
    seen_inactive = 0
    for cid in dict.fromkeys(short + closed + sample):
        ops = normalize(load_statement(RAW_DIR, cid))
        result = run_forecast(ops, date(2026, 9, 30), n_paths=100)
        sane(result)
        seen_inactive += result.data_quality.inactive
    assert seen_inactive >= len(closed) - 1                                  # закрытые счета не прогнозируем


def test_direction_synonyms_from_other_exports_and_unknown_direction_fails():
    import pandas as pd
    import pytest

    from app.engine.normalize import normalize

    raw = pd.DataFrame({"company_id": "X", "operation_id": ["1", "2", "3", "4"],
                        "operation_date": ["2026-01-05"] * 4, "amount": [1.0, 2.0, 3.0, 4.0],
                        "direction": ["Поступление", " приход ", "OUT", "Списание"], "counterparty_inn": None,
                        "counterparty_name": None, "payment_purpose": "x", "balance_eod": 10.0})
    assert normalize(raw)["direction"].tolist() == [1, 1, -1, -1]
    with pytest.raises(ValueError, match="Неизвестное направление"):
        normalize(raw.assign(direction=["Перевод"] * 4))


def test_industry_season_skips_a_company_whose_revenue_started_this_month():
    # на 16.08 у новой компании выручка только в августе (месяц не завершён) — индекс отрасли считается по остальным
    import pandas as pd

    from app.engine.categorize import categorize
    from app.engine.context import compute_industry_season
    from tests.helpers import make_statement, monthly

    old = make_statement(monthly("2025-01", 19, 15, "next", 100_000.0, "Зачисление средств по операциям эквайринга",
                                 inn="0068275202", sign=1), company="A001")
    new = make_statement({date(2026, 8, 3): [op(1, 50_000.0, "Зачисление средств по операциям эквайринга", inn="0068275202")]},
                         company="A002")
    ops = categorize(pd.concat([old, new], ignore_index=True))
    companies = pd.DataFrame({"company_id": ["A001", "A002"], "industry": ["Кофейня", "Кофейня"]})
    season = compute_industry_season(ops, companies, date(2026, 8, 16))
    assert set(season) == {"Кофейня"} and len(season["Кофейня"]) == 12
