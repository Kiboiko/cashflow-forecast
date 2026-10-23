from datetime import date, timedelta

import numpy as np

from app.engine.categorize import categorize
from app.engine.enrich import enrich
from app.engine.quality import DataGap
from app.engine.rates import fit_rates
from app.engine.types import RecurringStream
from tests.helpers import make_statement, merge, monthly, op

AS_OF = date(2026, 3, 2)                                  # понедельник


def weekly_purchases(weeks: int = 20) -> dict[date, list[dict]]:
    """По вторникам и четвергам — закупки по 10 000 ₽ (недельная сумма 20 000)."""
    days: dict[date, list[dict]] = {}
    monday = AS_OF - timedelta(days=7 * weeks)
    for w in range(weeks):
        for offset in (1, 3):
            d = monday + timedelta(days=7 * w + offset)
            days.setdefault(d, []).append(op(-1, 10_000.0, "Оплата по счету за товар", inn="0077"))
    return days


def prepare(days: dict):
    return enrich(categorize(make_statement(days)))


def rent_stream() -> RecurringStream:
    return RecurringStream(key="rent:0099", category="rent", direction=-1, period="monthly", anchor_day=5,
                           shift_rule="next", amount=50_000.0, amount_cv=0.02, last_date=date(2026, 2, 5),
                           active=True, confidence=0.9, mandatory=True, counterparty=None, explain="")


def test_weekly_bootstrap_spreads_the_week_over_the_historical_weekdays():
    model = fit_rates(prepare(weekly_purchases()), AS_OF, horizon=14, direction=-1)
    flows = model.sample(model.dates, 500, np.random.default_rng(0))
    assert flows.shape == (500, 14) and (flows <= 0).all()
    day = {d: flows[:, i].mean() for i, d in enumerate(model.dates)}
    assert day[date(2026, 3, 3)] == -10_000.0 and day[date(2026, 3, 5)] == -10_000.0     # вт и чт
    assert day[date(2026, 3, 4)] == 0.0 and day[date(2026, 3, 7)] == 0.0                 # ср и сб
    assert model.expected().sum() == -40_000.0


def test_streams_and_excluded_flows_are_not_double_counted():
    days = merge(weekly_purchases(), monthly("2025-11", 5, 5, "next", 50_000.0, "Арендная плата за помещение",
                                             inn="0099"),
                 {date(2026, 2, 10): [op(-1, 900_000.0, "Займ учредителю возврат займа", inn="0033")]})
    ops = prepare(days)
    with_rent = fit_rates(ops, AS_OF, 7, -1)
    without_rent = fit_rates(ops, AS_OF, 7, -1, streams=[rent_stream()])
    assert without_rent.weekly_sums.max() == 20_000.0         # аренда и возврат займа владельцу исключены
    assert with_rent.weekly_sums.max() > 20_000.0


def test_inflow_direction_gets_positive_flows():
    days = {AS_OF - timedelta(days=7 * w + 5): [op(1, 30_000.0, "Оплата по счету 5 от 01.01.2026", inn="0044")]
            for w in range(1, 15)}
    model = fit_rates(prepare(days), AS_OF, 14, direction=1)
    assert (model.sample(model.dates, 10, np.random.default_rng(0)) >= 0).all()
    assert model.expected().sum() > 0


def test_too_little_history_gives_no_model():
    days = {AS_OF - timedelta(days=9): [op(-1, 10_000.0, "Оплата по счету за товар", inn="0077")],
            AS_OF - timedelta(days=2): [op(-1, 10_000.0, "Оплата по счету за товар", inn="0077")]}
    assert fit_rates(prepare(days), AS_OF, 14, -1) is None


def test_weeks_overlapping_a_data_gap_are_dropped():
    ops = prepare(weekly_purchases())
    gap = DataGap(AS_OF - timedelta(days=22), AS_OF - timedelta(days=15), -1.0)
    assert fit_rates(ops, AS_OF, 14, -1, gaps=[gap]).weeks_used < fit_rates(ops, AS_OF, 14, -1).weeks_used


def test_holiday_week_keeps_its_total():
    holiday_as_of = date(2026, 6, 8)                      # 12.06.2026 (пт) — праздник
    days = {}
    monday = holiday_as_of - timedelta(days=7 * 20)
    for w in range(20):
        for offset in (2, 4):                             # ср и пт
            days.setdefault(monday + timedelta(days=7 * w + offset), []).append(
                op(-1, 10_000.0, "Оплата по счету за товар", inn="0077"))
    model = fit_rates(prepare(days), holiday_as_of, 7, -1)
    assert abs(model.share.sum() - 1.0) < 1e-9             # неделя с праздником сохранила свою сумму
    assert model.share[model.dates.index(date(2026, 6, 10))] == 1.0     # всё ушло на среду
    assert model.share[model.dates.index(date(2026, 6, 12))] == 0.0     # праздничная пятница пуста
