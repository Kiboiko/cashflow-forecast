from datetime import date

import pandas as pd
import pytest

from app.engine.categorize import categorize
from app.engine.enrich import enrich
from app.engine.quality import run_quality
from app.engine.taxes import DEFAULT_ENP_INDEX, compute_enp_index, forecast_enp, tax_debt_flag
from tests.helpers import RAW_DIR, make_statement, merge, monthly, needs_dataset, op

ENP = "Единый налоговый платеж"
TREASURY = {"inn": "0001", "name": "Казначейство России (ФНС России)"}


def prepare(days: dict, as_of: date, company: str = "T001"):
    ops = make_statement(days, company=company)
    return enrich(categorize(run_quality(ops, pd.Timestamp(as_of)).ops))


def test_enp_on_weekend_moves_to_next_workday():
    days = monthly("2025-01", 13, 28, "next", 100_000.0, ENP, **TREASURY)
    as_of = date(2026, 2, 10)
    events = forecast_enp(prepare(days, as_of), as_of, 30)
    assert len(events) == 1
    assert events[0].date == date(2026, 3, 2)                            # 28.02.2026 — суббота
    assert events[0].amount == pytest.approx(100_000.0)
    assert events[0].mandatory and events[0].source == "tax_calendar" and events[0].category == "tax_enp"


def test_october_advance_peak_from_history():
    def amount(p):
        return 160_000.0 if p.month in (4, 7, 10) else 100_000.0

    days = monthly("2024-10", 24, 28, "next", amount, ENP, **TREASURY)
    as_of = date(2026, 9, 30)
    events = forecast_enp(prepare(days, as_of), as_of, 30)
    assert [e.date for e in events] == [date(2026, 10, 28)]
    assert events[0].amount == pytest.approx(160_000.0)
    assert "авансовый платёж" in events[0].explain


def test_without_year_ago_uses_seasonality_index():
    days = monthly("2026-01", 9, 28, "next", lambda p: 100_000.0 * DEFAULT_ENP_INDEX[p.month], ENP, **TREASURY)
    as_of = date(2026, 9, 30)
    events = forecast_enp(prepare(days, as_of), as_of, 30)
    truth = 100_000.0 * DEFAULT_ENP_INDEX[10]
    assert truth * 0.85 <= events[0].amount <= truth * 1.15
    assert "авансовый платёж" in events[0].explain


def test_tax_follows_revenue_growth_through_the_revenue_ratio():
    """Выручка растёт на 6% в месяц, налог — 10% выручки за 3 предыдущих месяца: оценка должна расти вместе с ней."""
    revenue = {pd.Period("2024-10", "M") + k: 1_000_000.0 * 1.06 ** k for k in range(24)}
    days = monthly("2024-10", 24, 28, "next",
                   lambda p: 0.1 * sum(revenue[p - j] for j in (1, 2, 3) if (p - j) in revenue), ENP, **TREASURY)
    days = merge(days, monthly("2024-10", 24, 14, "next", lambda p: revenue[p],
                               "Оплата по счету № 1 от 01.01.2025", inn="0044", sign=1))
    as_of = date(2026, 9, 30)
    events = forecast_enp(prepare(days, as_of), as_of, 30)
    assert len(events) == 1
    truth = 0.1 * sum(1_000_000.0 * 1.06 ** (23 - j) for j in (0, 1, 2))      # налог октября = 10% от июля–сентября
    assert events[0].amount == pytest.approx(truth, rel=0.12)


def test_no_enp_history_gives_no_events():
    days = {date(2026, 1, 12): [op(-1, 10_000.0, "Аренда помещения")]}
    as_of = date(2026, 2, 1)
    assert forecast_enp(prepare(days, as_of), as_of, 30) == []


def test_month_already_paid_is_not_forecast_again():
    days = monthly("2025-01", 13, 28, "next", 100_000.0, ENP, **TREASURY)
    days.setdefault(date(2026, 2, 3), []).append(op(-1, 100_000.0, ENP, **TREASURY))   # уплачено досрочно
    as_of = date(2026, 2, 10)
    assert forecast_enp(prepare(days, as_of), as_of, 30) == []


def test_tax_debt_flag_only_for_recent_enforcement():
    recent = {date(2026, 3, 2): [op(-1, 50_000.0, "Списание по поручению налогового органа", **TREASURY)]}
    old = {date(2025, 10, 1): [op(-1, 50_000.0, "Списание по поручению налогового органа", **TREASURY)]}
    as_of = date(2026, 3, 31)
    assert tax_debt_flag(prepare(recent, as_of), as_of)
    assert not tax_debt_flag(prepare(old, as_of), as_of)


def test_enp_index_shows_advance_peaks():
    def amount(p):
        return 300_000.0 if p.month in (4, 7, 10) else 100_000.0

    frames = [prepare(monthly("2024-10", 24, 28, "next", amount, ENP, **TREASURY), date(2026, 9, 30), cid)
              for cid in ("T001", "T002")]
    index = compute_enp_index(pd.concat(frames), date(2026, 9, 30))
    assert index[4] > 1.5 and index[7] > 1.5 and index[10] > 1.5
    assert index[5] < 1.0 and index[11] < 1.0


@needs_dataset
def test_demo_company_enp_dates_are_28th_shifted():
    from app.engine.io import load_statement
    from app.engine.normalize import normalize

    as_of = date(2026, 7, 31)
    report = run_quality(normalize(load_statement(RAW_DIR, "C064")), pd.Timestamp(as_of))
    events = forecast_enp(enrich(categorize(report.ops)), as_of, 30)
    assert [e.date for e in events] == [date(2026, 8, 28)]
    assert events[0].amount > 0
