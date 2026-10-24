"""simulate / risk: тождество остатка, порядок перцентилей, воспроизводимость, игрушечные сценарии."""
from datetime import date, timedelta

import numpy as np
import pytest

from app.engine.risk import assess_risk
from app.engine.simulate import FixedEvent, TimedEvent, simulate
from app.engine.types import PlannedEvent, Settings

AS_OF = date(2026, 3, 2)
DATES = [AS_OF + timedelta(days=i) for i in range(1, 31)]


def event(day: int, amount: float, direction: int = -1, p: float = 1.0, cv: float = 0.0, mandatory: bool = True,
          title: str = "Зарплата", eid: str | None = None) -> PlannedEvent:
    d = AS_OF + timedelta(days=day)
    return PlannedEvent(id=eid or f"e{day}{direction}", direction=direction, amount=amount, date=d,
                        p_within_horizon=p, amount_cv=cv, category="salary", title=title, mandatory=mandatory)


def run(start, events, n=2000, seed=1):
    return simulate(start, DATES, [FixedEvent(e) for e in events], n, seed)


def test_balance_identity_is_start_plus_cumulative_flows():
    sim = run(100_000.0, [event(5, 30_000.0, p=0.7, cv=0.2), event(9, 50_000.0, direction=1, cv=0.1)])
    np.testing.assert_allclose(sim.balance, 100_000.0 + sim.flows.cumsum(axis=1))
    np.testing.assert_allclose(sim.flows, sim.inflow - sim.outflow)


def test_percentiles_are_ordered():
    sim = run(100_000.0, [event(5, 30_000.0, p=0.7, cv=0.3), event(9, 50_000.0, direction=1, cv=0.2)])
    p10, p50, p90 = np.percentile(sim.balance, [10, 50, 90], axis=0)
    assert (p10 <= p50).all() and (p50 <= p90).all()


def test_same_seed_is_reproducible_and_other_seed_differs():
    events = [event(5, 30_000.0, p=0.5, cv=0.3)]
    np.testing.assert_array_equal(run(0.0, events, seed=3).balance, run(0.0, events, seed=3).balance)
    assert not np.array_equal(run(0.0, events, seed=3).balance, run(0.0, events, seed=4).balance)


def test_lognormal_noise_keeps_the_mean_and_bernoulli_the_probability():
    sim = run(0.0, [event(5, 40_000.0, p=0.25, cv=0.4)], n=20_000)
    paid = sim.outflow[:, 4]
    assert (paid > 0).mean() == pytest.approx(0.25, abs=0.015)
    assert paid[paid > 0].mean() == pytest.approx(40_000.0, rel=0.02)


def test_changing_one_component_does_not_move_random_numbers_of_the_others():
    a, b = event(5, 30_000.0, p=0.5, cv=0.3, eid="a"), event(8, 20_000.0, p=0.5, cv=0.3, eid="b")
    base = simulate(0.0, DATES, [FixedEvent(a), FixedEvent(b)], 500, 11, keep_components=True)
    moved = simulate(0.0, DATES, [FixedEvent(event(5, 45_000.0, p=0.5, cv=0.3, eid="a")), FixedEvent(b)],
                     500, 11, keep_components=True)
    np.testing.assert_array_equal(base.components["event:b"], moved.components["event:b"])
    assert (moved.outflow[:, 4] >= base.outflow[:, 4]).all()          # те же сценарии, сумма больше


def test_event_outside_horizon_contributes_nothing():
    sim = run(10.0, [event(45, 99_999.0)])
    assert (sim.balance == 10.0).all()


def test_timed_event_follows_date_distribution():
    e = event(1, 100_000.0, direction=1, eid="inv")
    e.date = None
    e.date_pmf = [(DATES[2], 0.5), (DATES[4], 0.3), (AS_OF + timedelta(days=60), 0.1)]      # последняя — вне горизонта
    sim = simulate(0.0, DATES, [TimedEvent(e)], 20_000, 5)
    assert (sim.inflow[:, 2] > 0).mean() == pytest.approx(0.5, abs=0.02)
    assert (sim.inflow[:, 4] > 0).mean() == pytest.approx(0.3, abs=0.02)
    assert (sim.inflow.sum(axis=1) > 0).mean() == pytest.approx(0.8, abs=0.02)            # 0.1 вне горизонта, 0.1 — никогда


def test_extra_flows_reuse_the_same_scenarios():
    sim = run(0.0, [event(5, 30_000.0, p=0.5, cv=0.3)])
    extra = np.zeros(sim.flows.shape[1])
    extra[2] = 25_000.0
    after = sim.with_extra_flows(extra)
    expected = np.broadcast_to(np.where(np.arange(30) >= 2, 25_000.0, 0.0), sim.balance.shape)
    np.testing.assert_allclose(after.balance - sim.balance, expected)


# ---------- риск: игрушечные сценарии с известным ответом ----------
def test_certain_gap_has_exact_probability_date_and_shortfall():
    sim = run(100_000.0, [event(10, 180_000.0, title="Зарплата")])
    stats = assess_risk(sim, AS_OF, Settings(), [event(10, 180_000.0)], calibrated=False)
    assert stats.level == "red"
    assert stats.gap_probability == 1.0
    assert stats.possible_gap_date == stats.likely_gap_date == DATES[9]
    assert stats.shortfall_p50 == stats.shortfall_p90 == stats.safe_buffer == 80_000.0
    assert stats.coverage[0].covered_probability == 0.0


def test_no_gap_is_green_with_zero_shortfall():
    sim = run(500_000.0, [event(10, 180_000.0)])
    stats = assess_risk(sim, AS_OF, Settings(), [event(10, 180_000.0)], calibrated=False)
    assert (stats.level, stats.gap_probability, stats.shortfall_p50, stats.safe_buffer) == ("green", 0.0, 0.0, 0.0)
    assert stats.possible_gap_date is None and stats.coverage[0].covered_probability == 1.0
    assert stats.min_p50 == 320_000.0 and stats.min_p50_date == DATES[9]


def test_coin_flip_payment_gives_half_probability_and_yellow_or_red_by_thresholds():
    ev = event(10, 180_000.0, p=0.5)
    sim = run(100_000.0, [ev], n=20_000)
    stats = assess_risk(sim, AS_OF, Settings(), [ev], calibrated=False)
    assert stats.gap_probability == pytest.approx(0.5, abs=0.02)
    assert stats.level == "red"
    assert assess_risk(sim, AS_OF, Settings(risk_red=0.9), [ev], calibrated=False).level == "yellow"
    assert assess_risk(sim, AS_OF, Settings(risk_red=0.9, risk_yellow=0.9), [ev], calibrated=False).level == "green"


def test_gap_within_a_week_is_red_even_if_probability_is_below_red_threshold():
    ev = event(3, 180_000.0, p=0.45)
    sim = run(100_000.0, [ev], n=20_000)
    stats = assess_risk(sim, AS_OF, Settings(risk_red=0.9), [ev], calibrated=False)
    assert stats.likely_gap_date is None                 # шанс 0,45 < 0,5 — «вероятной» даты нет, уровень по порогам
    assert stats.level == "yellow"
    ev = event(3, 180_000.0, p=0.6)
    stats = assess_risk(run(100_000.0, [ev], n=20_000), AS_OF, Settings(risk_red=0.9), [ev], calibrated=False)
    assert stats.likely_gap_date == DATES[2] and stats.level == "red"


def test_overdraft_limit_lowers_the_threshold():
    ev = event(10, 180_000.0)
    sim = run(100_000.0, [ev])
    assert assess_risk(sim, AS_OF, Settings(overdraft_limit=100_000.0), [ev], calibrated=False).level == "green"
    assert assess_risk(sim, AS_OF, Settings(threshold=50_000.0), [ev], calibrated=False).shortfall_p50 == 130_000.0


def test_mandatory_payments_on_the_same_day_share_the_money_salary_first():
    # 28-го зарплата 150 тыс. и налог 80 тыс., на счёте 200 тыс.: на зарплату хватает, на налог после неё — нет
    salary = event(10, 150_000.0, eid="sal")
    tax = PlannedEvent(id="tax", direction=-1, amount=80_000.0, date=AS_OF + timedelta(days=10), category="tax_enp",
                       title="Налоги (ЕНП)", mandatory=True)
    sim = run(200_000.0, [tax, salary])
    coverage = {c.event_id: c.covered_probability for c in assess_risk(sim, AS_OF, Settings(), [tax, salary]).coverage}
    assert coverage == {"sal": 1.0, "tax": 0.0}


def test_default_red_threshold_is_35_percent():
    ev = event(10, 180_000.0, p=0.37)
    stats = assess_risk(run(100_000.0, [ev], n=20_000), AS_OF, Settings(), [ev], calibrated=False)
    assert 0.35 < stats.gap_probability < 0.40 and stats.likely_gap_date is None and stats.level == "red"


def test_incoming_money_on_the_payment_day_counts_for_coverage():
    pay, income = event(10, 150_000.0), event(10, 100_000.0, direction=1, mandatory=False)
    sim = run(100_000.0, [pay, income])
    assert assess_risk(sim, AS_OF, Settings(), [pay, income], calibrated=False).coverage[0].covered_probability == 1.0
