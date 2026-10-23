"""Бэктест: цель B_op, бейзлайны и метрики на игрушечных данных с известным ответом."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.engine.backtest import (
    actual_flows, build_truth, coverage, detection_metrics, eligible, flow_group, naive_paths, reliability,
    rel_mae, seasonal_naive_paths, summarize,
)
from app.engine.categorize import categorize
from app.engine.enrich import enrich
from tests.helpers import make_statement, op

CUTOFF = date(2026, 3, 2)


def company(extra: dict | None = None):
    """Год ежедневных платежей: приход 1000 и расход 900 по рабочим дням + произвольные события."""
    days: dict[date, list[dict]] = {}
    d = CUTOFF - timedelta(days=200)
    while d <= CUTOFF + timedelta(days=40):
        if d.weekday() < 5:
            days[d] = [op(1, 1_000.0, "Оплата по счету № 1 от 01.01.2026", inn="0044"),
                       op(-1, 900.0, "Оплата по УПД № 5 от 01.01.2026 за товар", inn="0077")]
        d += timedelta(days=1)
    for day, ops in (extra or {}).items():
        days.setdefault(day, []).extend(ops)
    return enrich(categorize(make_statement(days, start=50_000.0)))


def test_b_op_removes_owner_money_so_a_hidden_gap_becomes_visible():
    day = CUTOFF + timedelta(days=10)                                          # 12 марта, четверг
    ops = company({day: [op(-1, 400_000.0, "Заработная плата за февраль", inn=None),
                         op(1, 400_000.0, "Займ от учредителя по договору", inn="0088")]})   # владелец закрыл дыру
    truth = build_truth(ops, CUTOFF)
    assert truth.b_fact.min() > 0                                              # на счёте минуса не видно
    assert truth.first_gap == 10 and truth.max_shortfall > 300_000             # а без денег владельца — разрыв
    assert truth.b_op.shape == (30,)


def test_b_op_equals_fact_without_excluded_flows():
    truth = build_truth(company(), CUTOFF)
    np.testing.assert_allclose(truth.b_op, truth.b_fact)
    assert truth.first_gap is None and truth.scale >= 10_000.0


def test_build_truth_requires_the_full_horizon_after_the_cutoff():
    assert build_truth(company(), CUTOFF + timedelta(days=30)) is None


def test_eligibility_needs_history_and_no_data_gap_in_the_horizon():
    ops = make_statement({CUTOFF - timedelta(days=100) + timedelta(days=i): [op(1, 10.0)] for i in range(0, 200, 2)})
    assert eligible(ops, CUTOFF)
    assert not eligible(ops, CUTOFF - timedelta(days=60))                     # меньше 90 дней истории
    assert not eligible(ops, CUTOFF + timedelta(days=90))                     # после отсечки нет 30 дней


def test_naive_and_seasonal_naive_paths():
    assert (naive_paths(123.0, 30) == 123.0).all() and naive_paths(1.0, 30).shape == (1, 30)
    ops = company()
    paths = seasonal_naive_paths(ops, CUTOFF, 1_000.0, 30, 200, np.random.default_rng(1))
    assert paths.shape == (200, 30)
    # рабочие дни дают +100 в день, по выходным 0: остаток растёт, но не быстрее 100 в день
    assert paths[0, -1] > 1_000.0 and (np.diff(paths, axis=1) <= 100.0 + 1e-6).all()
    again = seasonal_naive_paths(ops, CUTOFF, 1_000.0, 30, 200, np.random.default_rng(1))
    np.testing.assert_array_equal(paths, again)


def test_summarize_gap_probability_by_horizon_and_possible_day():
    paths = np.vstack([np.full(30, 100.0)] * 6 + [np.r_[np.full(10, 100.0), np.full(20, -50.0)]] * 4)   # 40% разрыв с 11-го дня
    s = summarize(paths)
    assert s.gap_probability[30] == pytest.approx(0.4) and s.gap_probability[7] == 0.0
    assert s.gap_probability[14] == pytest.approx(0.4) and s.possible_gap_day == 11
    assert s.shortfall_p50 == 50.0


def test_metrics():
    p50, b_op = np.array([100.0, 200.0, 300.0]), np.array([110.0, 180.0, 330.0])
    assert rel_mae(p50, b_op, 100.0, 3) == pytest.approx(0.2)
    assert coverage(np.array([90.0, 150.0, 400.0]), np.array([120.0, 190.0, 500.0]), b_op, 3) == pytest.approx(2 / 3)
    d = detection_metrics([1, 1, 0, 0, 0, 0], [0.9, 0.2, 0.15, 0.05, 0.0, 0.0])
    assert d["recall"] == 1.0 and d["precision"] == pytest.approx(2 / 3) and d["roc_auc"] == 1.0
    assert detection_metrics([0, 0, 0], [0.1, 0.2, 0.3])["roc_auc"] is None      # нет положительных — AUC не определён


def test_reliability_bins():
    rel = reliability(np.array([0.05, 0.05, 0.95, 0.95]), np.array([0, 0, 1, 0]), bins=10)
    assert [(round(r["predicted"], 2), r["observed"], r["n"]) for r in rel] == [(0.05, 0.0, 2), (0.95, 0.5, 2)]


def test_flow_groups_and_actual_flows_exclude_owner_money():
    assert flow_group("revenue_card", 1) == "retail_revenue" and flow_group("loan_repayment", -1) == "debt"
    assert flow_group("client_payment", 1) == "client_in" and flow_group("contractors", -1) == "suppliers"
    ops = company({CUTOFF + timedelta(days=3): [op(1, 500.0, "Займ от учредителя", inn="0088")]})
    flows = actual_flows(ops, CUTOFF)
    assert flows["client_in"] == pytest.approx(1_000.0 * 22) and flows["suppliers"] == pytest.approx(900.0 * 22)
    assert sum(v for k, v in flows.items() if k.startswith("other")) == 0.0      # заём владельца не в счёт


def test_pandas_dates_in_truth_are_aligned_with_horizon():
    ops = company()
    first_day = pd.Timestamp(CUTOFF + timedelta(days=1))
    expected = ops[ops["date"] <= first_day].groupby("date")["balance_eod"].last().iloc[-1]
    assert build_truth(ops, CUTOFF).b_fact[0] == pytest.approx(expected)
