from datetime import date, timedelta

import numpy as np
import pytest

from app.engine.categorize import categorize
from app.engine.enrich import enrich
from app.engine.quality import DataGap
from app.engine.revenue import credit_date, effective_weekday, fit_revenue
from tests.helpers import make_statement, op

FACTOR = [1.0, 0.9, 0.9, 1.0, 1.3, 1.6, 1.3]            # пн … вс
BASE = 100_000.0
BANK = {"inn": "0068275202", "name": "ПАО «Банк»"}


def card_credits(first: date, last: date, month_mult=lambda d: 1.0, noise: float = 0.0, seed: int = 0):
    """Продажи по дням first..last → зачисления на следующий рабочий день; возвращает (days, truth {credit: sum})."""
    rng = np.random.default_rng(seed)
    days: dict[date, list[dict]] = {}
    truth: dict[date, float] = {}
    s = first
    while s <= last:
        amount = BASE * FACTOR[effective_weekday(s)] * month_mult(s)
        amount *= rng.lognormal(0, noise) if noise else 1.0
        c = credit_date(s, "next")
        purpose = f"Зачисление средств по операциям эквайринга. Мерчант №1. Дата реестра {s:%d.%m.%Y}. Сумма операций 1"
        days.setdefault(c, []).append(op(1, round(amount, 2), purpose, **BANK))
        truth[c] = truth.get(c, 0.0) + round(amount, 2)
        s += timedelta(days=1)
    return days, truth


def prepare(days: dict, as_of: date):
    return enrich(categorize(make_statement(days)))


def truth_series(truth: dict, as_of: date, horizon: int) -> np.ndarray:
    return np.array([truth.get(as_of + timedelta(days=i), 0.0) for i in range(1, horizon + 1)])


AS_OF = date(2026, 6, 5)                                  # пятница


def test_expected_inflow_matches_known_future_including_weekend_and_holiday_lag():
    days, truth = card_credits(date(2026, 1, 1), date(2026, 6, 30))
    model = fit_revenue(prepare(days, AS_OF), AS_OF, horizon=14)
    expected, actual = model.expected(), truth_series(truth, AS_OF, 14)
    assert actual[2] > 2.5 * BASE                          # понедельник 08.06: продажи пт+сб+вс
    np.testing.assert_allclose(expected, actual, rtol=0.01, atol=1.0)
    assert expected[6] == 0.0                              # 12.06 — праздник, зачислений нет


def test_m0_baseline_also_recovers_a_stable_pattern():
    as_of = date(2026, 8, 7)                               # в последних 4 неделях нет праздников
    days, truth = card_credits(date(2026, 4, 1), date(2026, 9, 15))
    model = fit_revenue(prepare(days, as_of), as_of, horizon=7, method="m0")
    np.testing.assert_allclose(model.expected(), truth_series(truth, as_of, 7), rtol=0.01, atol=1.0)


def test_own_seasonality_from_same_month_last_year():
    def mult(d):
        return 2.0 if (d.year, d.month) == (2025, 7) else 1.0

    days, _ = card_credits(date(2025, 5, 1), date(2026, 7, 31), mult)
    as_of = date(2026, 6, 30)
    ops = prepare(days, as_of)
    flat = fit_revenue(ops, as_of, horizon=21, method="m0").expected()
    seasonal = fit_revenue(ops, as_of, horizon=21, method="m1").expected()
    mask = flat > 0
    mask[0] = False                                        # 1 июля = зачисление продаж 30 июня (ещё июнь)
    np.testing.assert_allclose(seasonal[mask] / flat[mask], 2.0, rtol=0.03)


def test_industry_season_is_used_when_company_has_no_year_of_history():
    days, _ = card_credits(date(2026, 1, 1), date(2026, 7, 31))
    as_of = date(2026, 6, 30)
    ops = prepare(days, as_of)
    flat = fit_revenue(ops, as_of, horizon=14).expected()
    seasonal = fit_revenue(ops, as_of, horizon=14, industry_season={6: 1.0, 7: 3.0}).expected()
    mask = flat > 0
    mask[0] = False                                        # 1 июля = зачисление продаж 30 июня (ещё июнь)
    np.testing.assert_allclose(seasonal[mask] / flat[mask], 3.0, rtol=0.01)


def test_samples_are_reproducible_nonnegative_and_spread_with_noise():
    days, truth = card_credits(date(2025, 10, 1), date(2026, 6, 30), noise=0.25, seed=7)
    model = fit_revenue(prepare(days, AS_OF), AS_OF, horizon=30)
    a = model.sample(model.dates, 2000, np.random.default_rng(1))
    b = model.sample(model.dates, 2000, np.random.default_rng(1))
    assert a.shape == (2000, 30) and (a >= 0).all()
    np.testing.assert_array_equal(a, b)
    total = a.sum(axis=1)
    p10, p50, p90 = np.percentile(total, [10, 50, 90])
    assert p10 < p50 < p90
    assert p10 <= truth_series(truth, AS_OF, 30).sum() <= p90 * 1.15
    assert model.expected().sum() == pytest.approx(p50, rel=0.15)


def test_zero_revenue_flag_when_sales_stopped():
    days, _ = card_credits(date(2026, 1, 1), date(2026, 4, 20))      # торговля встала за ~45 дней до as_of
    model = fit_revenue(prepare(days, AS_OF), AS_OF, horizon=14)
    assert "zero_revenue_14d" in model.flags
    assert model.expected().sum() == 0.0


def test_short_history_flag_and_wider_uncertainty():
    short_days, _ = card_credits(date(2026, 4, 1), date(2026, 6, 30), noise=0.2, seed=3)
    long_days, _ = card_credits(date(2025, 10, 1), date(2026, 6, 30), noise=0.2, seed=3)
    short = fit_revenue(prepare(short_days, AS_OF), AS_OF, 30)
    long = fit_revenue(prepare(long_days, AS_OF), AS_OF, 30)
    assert "short_history" in short.flags and "short_history" not in long.flags
    assert short.channels[0].level_ratios.std() > long.channels[0].level_ratios.std()


def test_gap_days_do_not_pull_the_level_down():
    days, truth = card_credits(date(2026, 1, 1), date(2026, 6, 30))
    # зачисления за 10 дней перед as_of «потерялись» в выгрузке
    lost = {d: v for d, v in days.items() if not (date(2026, 5, 25) <= d <= date(2026, 6, 4))}
    gap = DataGap(date(2026, 5, 24), date(2026, 6, 5), -1.0)
    ops = prepare(lost, AS_OF)
    with_gap = fit_revenue(ops, AS_OF, horizon=7, gaps=[gap]).expected().sum()
    without = fit_revenue(ops, AS_OF, horizon=7).expected().sum()
    assert with_gap == pytest.approx(truth_series(truth, AS_OF, 7).sum(), rel=0.05)
    assert without < 0.8 * with_gap


def test_no_card_sales_gives_no_model():
    days = {date(2026, 3, 2): [op(-1, 1000.0, "Аренда помещения")]}
    assert fit_revenue(prepare(days, AS_OF), AS_OF, 30) is None


def test_model_rejects_foreign_horizon():
    days, _ = card_credits(date(2026, 1, 1), date(2026, 6, 30))
    model = fit_revenue(prepare(days, AS_OF), AS_OF, 7)
    other = [AS_OF + timedelta(days=i) for i in range(2, 9)]
    with pytest.raises(ValueError):
        model.sample(other, 10, np.random.default_rng(0))


def test_credit_dates_follow_next_workday_rule():
    assert credit_date(date(2026, 6, 5), "next") == date(2026, 6, 8)      # пт → пн
    assert credit_date(date(2026, 6, 11), "next") == date(2026, 6, 15)    # чт → пт 12.06 праздник → пн
    assert credit_date(date(2026, 6, 4), "zero") == date(2026, 6, 4)
