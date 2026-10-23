from datetime import date, timedelta

import pandas as pd

from app.engine.quality import daily_reconciliation, run_quality
from tests.helpers import make_statement, op

D = date(2026, 3, 2)  # понедельник


def _days(n: int, per_day: list[dict] | None = None) -> dict[date, list[dict]]:
    return {D + timedelta(days=i): list(per_day or [op(1, 1000.0)]) for i in range(n)}


def test_reconciliation_is_clean_on_consistent_statement():
    recon = daily_reconciliation(make_statement(_days(5)))
    assert (recon["diff"].abs() < 1e-6).all()


def test_duplicate_removed_only_when_balance_confirms():
    days = _days(3)
    days[D + timedelta(days=1)].append(op(1, 1000.0, counted=False))   # копия, остаток её не учёл
    report = run_quality(make_statement(days))
    assert report.duplicates_removed == 1
    assert len(report.ops) == 3
    assert report.gaps == []


def test_honest_identical_payments_are_kept():
    days = _days(3)
    days[D + timedelta(days=1)].append(op(1, 1000.0, counted=True))    # остаток учёл оба платежа
    report = run_quality(make_statement(days))
    assert report.duplicates_removed == 0
    assert len(report.ops) == 4


def test_gap_is_found_and_neighbours_are_flagged():
    days = _days(12)
    gap_day = D + timedelta(days=6)
    report = run_quality(make_statement(days, jumps={gap_day: -182_000.0}))
    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.end_date == gap_day
    assert gap.start_date == gap_day - timedelta(days=1)
    assert gap.unexplained_amount == -182_000.0
    flagged = set(report.ops.loc[report.ops["gap_adjacent"], "date"].dt.date)
    assert flagged == {gap_day + timedelta(days=k) for k in range(-4, 4)} & set(days)
    assert D not in flagged                          # далёкие дни пригодны для обучения


def test_penny_discrepancy_is_ignored():
    report = run_quality(make_statement(_days(5), jumps={D + timedelta(days=2): 0.40}))
    assert report.gaps == []


def test_reversal_pair_is_linked():
    pay = "Оплата по УПД № 4424 от 01.02.2026 за хлеб"
    ret = "Возврат платежа: неверно указаны реквизиты получателя. Оплата по УПД № 4424 от 01.02.2026 за хлеб"
    days = {
        D: [op(-1, 41_697.08, pay, inn="0022")],
        D + timedelta(days=1): [op(1, 41_697.08, ret, inn="0022")],
        D + timedelta(days=2): [op(-1, 41_697.08, pay, inn="0022")],     # повторный платёж — настоящий
    }
    ops = run_quality(make_statement(days)).ops
    paired = ops[ops["reversal_pair_id"].notna()]
    assert len(paired) == 2
    assert paired["reversal_pair_id"].nunique() == 1
    assert set(paired["date"].dt.date) == {D, D + timedelta(days=1)}


def test_return_without_matching_payment_stays_unpaired():
    ret = "Возврат платежа: неверно указаны реквизиты получателя. Оплата по УПД № 1 от 01.02.2026"
    days = {D: [op(1, 500.0, ret, inn="0022")], D + timedelta(days=1): [op(-1, 500.0, "аренда", inn="0022")]}
    assert run_quality(make_statement(days)).ops["reversal_pair_id"].isna().all()


def test_history_status():
    assert run_quality(make_statement(_days(30))).history == "short_history"
    long = {D + timedelta(days=i): [op(1, 10.0)] for i in range(0, 200, 7)}
    assert run_quality(make_statement(long)).history == "no_full_year"
    year = {D + timedelta(days=i): [op(1, 10.0)] for i in range(0, 430, 7)}
    assert run_quality(make_statement(year)).history == "full"


def test_inactive_after_20_workdays_without_operations():
    ops = make_statement(_days(3))
    assert run_quality(ops, as_of=pd.Timestamp(D) + pd.Timedelta(days=60)).inactive
    assert not run_quality(ops, as_of=pd.Timestamp(D) + pd.Timedelta(days=10)).inactive
