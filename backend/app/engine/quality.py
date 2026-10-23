"""Качество данных (§6.2): сверка остатков, дубли, дыры, сторно, статус истории.

Работает с операциями ОДНОГО бизнеса (нормализованный DataFrame из normalize.py).
Дубль удаляется только если это подтверждает сверка остатка дня (бывают честные одинаковые платежи).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from app.engine.calendar_ru import workdays_between
from app.engine.enrich import parse_document

BALANCE_TOLERANCE = 1.0          # |diff| ≤ 1 ₽ — округление банка, игнорируем
GAP_MARGIN_DAYS = 3              # дни у дыры, непригодные для обучения потоковых моделей
REVERSAL_WINDOW_DAYS = 14
SHORT_HISTORY_DAYS = 90
FULL_YEAR_MONTHS = 13
INACTIVE_WORKDAYS = 20

_DUP_KEY = ["date", "amount", "direction", "counterparty_inn", "counterparty_name", "purpose"]
_RETURN_MARK = "неверно указаны реквизиты"


@dataclass
class DataGap:
    start_date: date             # последний день с данными перед дырой
    end_date: date               # первый день после дыры
    unexplained_amount: float    # на сколько остаток «скакнул» без операций


@dataclass
class QualityReport:
    ops: pd.DataFrame            # без подтверждённых дублей; + gap_adjacent, reversal_pair_id
    duplicates_removed: int
    gaps: list[DataGap] = field(default_factory=list)
    reversal_pairs: int = 0
    history: str = "full"        # full | no_full_year | short_history
    inactive: bool = False


def daily_reconciliation(ops: pd.DataFrame) -> pd.DataFrame:
    """По дням: eod, net, prev_date, prev_eod, diff = eod − (prev_eod + net).

    Остаток на начало первого дня выводится из самой выписки, поэтому diff первого дня = 0.
    """
    signed = ops["amount"] * ops["direction"]
    day = pd.DataFrame({"eod": ops["balance_eod"], "net": signed}).groupby(ops["date"]).agg(
        eod=("eod", "first"), net=("net", "sum"))
    day["prev_date"] = day.index.to_series().shift()
    day["prev_eod"] = day["eod"].shift()
    day.iloc[0, day.columns.get_loc("prev_eod")] = day["eod"].iloc[0] - day["net"].iloc[0]
    day["diff"] = day["eod"] - (day["prev_eod"] + day["net"])
    return day


def remove_confirmed_duplicates(ops: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Удаляет копии строк, только если после удаления остаток дня сходится."""
    cand = ops.duplicated(subset=_DUP_KEY, keep="first")
    if not cand.any():
        return ops, 0
    recon = daily_reconciliation(ops)
    dup_net = (ops["amount"] * ops["direction"])[cand].groupby(ops["date"][cand]).sum()
    # остаток дубль не учитывает: факт − ожидание = −(знаковая сумма дубля)
    confirmed = [
        d for d, net in dup_net.items()
        if abs(recon.at[d, "diff"]) > BALANCE_TOLERANCE and abs(recon.at[d, "diff"] + net) <= BALANCE_TOLERANCE
    ]
    drop = cand & ops["date"].isin(confirmed)
    return ops.loc[~drop].reset_index(drop=True), int(drop.sum())


def find_gaps(recon: pd.DataFrame) -> list[DataGap]:
    bad = recon[recon["diff"].abs() > BALANCE_TOLERANCE]
    return [DataGap(r["prev_date"].date(), idx.date(), round(float(r["diff"]), 2)) for idx, r in bad.iterrows()]


def mark_gap_adjacent(ops: pd.DataFrame, gaps: list[DataGap]) -> pd.Series:
    """True для операций в окне [start − 3 дня, end + 3 дня] вокруг каждой дыры."""
    flag = pd.Series(False, index=ops.index)
    margin = timedelta(days=GAP_MARGIN_DAYS)
    for g in gaps:
        lo, hi = pd.Timestamp(g.start_date - margin), pd.Timestamp(g.end_date + margin)
        flag |= ops["date"].between(lo, hi)
    return flag


def mark_reversals(ops: pd.DataFrame) -> pd.Series:
    """Сторно: входящий «Возврат платежа: неверно указаны реквизиты… Оплата по УПД № N от D».

    Ищем исходящий платёж с тем же документом, суммой и контрагентом за 14 дней до возврата.
    Обе операции пары получают общий `reversal_pair_id` и исключаются из моделей.
    """
    pair = pd.Series(None, index=ops.index, dtype="object")
    returns = ops[(ops["direction"] == 1) & ops["purpose_norm"].str.contains(_RETURN_MARK, regex=False)]
    if returns.empty:
        return pair
    doc_no = ops["purpose_norm"].map(lambda s: (parse_document(s) or (None, None))[1])
    window = timedelta(days=REVERSAL_WINDOW_DAYS)
    for idx, ret in returns.iterrows():
        doc = parse_document(ret["purpose_norm"])
        if doc is None:
            continue
        cand = ops[
            (ops["direction"] == -1) & pair.isna()
            & (ops["cp_key"] == ret["cp_key"])
            & ((ops["amount"] - ret["amount"]).abs() < 0.01)
            & ops["date"].between(ret["date"] - window, ret["date"])
            & (doc_no == doc[1])
        ]
        if cand.empty:
            continue
        orig = cand.sort_values(["date", "operation_id"]).index[-1]
        pair[idx] = pair[orig] = f"rev:{ops.at[orig, 'operation_id']}"
    return pair


def history_status(ops: pd.DataFrame, as_of: pd.Timestamp) -> str:
    first = ops["date"].min()
    if (as_of - first).days < SHORT_HISTORY_DAYS:
        return "short_history"
    if as_of < first + pd.DateOffset(months=FULL_YEAR_MONTHS):
        return "no_full_year"
    return "full"


def run_quality(ops: pd.DataFrame, as_of: pd.Timestamp | None = None) -> QualityReport:
    if ops.empty:
        raise ValueError("Нет операций")
    if ops["company_id"].nunique() > 1:
        raise ValueError("quality работает с операциями одного бизнеса")
    ops = ops.sort_values(["date", "operation_id"], kind="stable").reset_index(drop=True)
    ops, n_dup = remove_confirmed_duplicates(ops)
    gaps = find_gaps(daily_reconciliation(ops))
    ops = ops.assign(gap_adjacent=mark_gap_adjacent(ops, gaps), reversal_pair_id=mark_reversals(ops))
    as_of = as_of if as_of is not None else ops["date"].max()
    last = ops.loc[ops["date"] <= as_of, "date"].max()
    return QualityReport(
        ops=ops,
        duplicates_removed=n_dup,
        gaps=gaps,
        reversal_pairs=int(ops["reversal_pair_id"].notna().sum() // 2),
        history=history_status(ops, as_of),
        inactive=workdays_between(last.date(), as_of.date()) > INACTIVE_WORKDAYS,
    )
