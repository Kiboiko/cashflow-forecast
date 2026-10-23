"""Бэктест (§7): целевая величина, простые бейзлайны и метрики. Без утечек: модели видят только данные ≤ as_of.

Целевая величина — «операционный остаток» `B_op(t) = B_факт(t) − Σ_{as_of < s ≤ t} (исключённые потоки)`:
модель намеренно не прогнозирует деньги владельца, финансирование и разовые операции (EXCLUDED_FROM_MODEL).
Если владелец 25-го внёс 300 000 ₽, чтобы выплатить зарплату, у B_op 25-го будет минус — это скрытый
разрыв, который нужно было предсказать.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.categorize.taxonomy import EXCLUDED_FROM_MODEL
from app.engine.quality import daily_reconciliation, find_gaps

HORIZONS = (7, 14, 30)
SCALE_MIN = 10_000.0                 # крошечный остаток: минимальный масштаб в rel-MAE
SCALE_DAYS = 90
MIN_HISTORY_DAYS = 90
POSSIBLE_PROB = 0.2
YELLOW_PROB = 0.10                   # порог «жёлтого» для precision/recall
RED_PROB = 0.40                      # порог «красного»
NAIVE = "naive"
SEASONAL_NAIVE = "seasonal_naive"
STRUCTURAL_M0 = "structural_m0"
STRUCTURAL = "structural"
STRUCTURAL_CAL = "structural_calibrated"
STRUCTURAL_EXT = "structural_external"
STRUCTURAL_M2 = "structural_m2"


@dataclass
class Truth:
    b_fact: np.ndarray               # фактический остаток по дням горизонта
    b_op: np.ndarray                 # операционный остаток
    scale: float                     # масштаб для rel-MAE
    first_gap: int | None            # первый день (1..H), когда B_op < 0, иначе None
    max_shortfall: float             # max_t (0 − B_op)+


def _daily_balance(ops: pd.DataFrame) -> pd.Series:
    day = ops.groupby("date")["balance_eod"].last()
    return day.reindex(pd.date_range(day.index.min(), day.index.max(), freq="D")).ffill()


def has_data_gap(ops_full: pd.DataFrame, start: date, end: date) -> bool:
    """Попадает ли в (start, end] дыра в выгрузке — такие отсечки из оценки исключаем."""
    gaps = find_gaps(daily_reconciliation(ops_full))
    return any(start < g.end_date <= end for g in gaps)


def build_truth(ops_full: pd.DataFrame, cutoff: date, horizon: int = 30) -> Truth | None:
    """Цель для одной компании и отсечки по ПОЛНЫМ категоризированным операциям. None — истории после нет."""
    cutoff_ts = pd.Timestamp(cutoff)
    end_ts = pd.Timestamp(cutoff + timedelta(days=horizon))
    if ops_full["date"].max() < end_ts:
        return None
    balance = _daily_balance(ops_full)
    days = pd.date_range(cutoff_ts + pd.Timedelta(days=1), end_ts, freq="D")
    b_fact = balance.reindex(days).ffill().values
    excluded = ops_full[(ops_full["date"] > cutoff_ts) & (ops_full["date"] <= end_ts)
                        & ops_full["category"].isin(EXCLUDED_FROM_MODEL)]
    signed = (excluded["amount"] * excluded["direction"]).groupby(excluded["date"]).sum()
    cum_excluded = signed.reindex(days, fill_value=0.0).cumsum().values
    b_op = b_fact - cum_excluded
    before = balance[(balance.index > cutoff_ts - pd.Timedelta(days=SCALE_DAYS)) & (balance.index <= cutoff_ts)]
    scale = max(float(before.abs().mean()) if len(before) else 0.0, SCALE_MIN)
    below = np.nonzero(b_op < 0)[0]
    return Truth(b_fact, b_op, scale, int(below[0]) + 1 if len(below) else None,
                 float(np.maximum(-b_op, 0).max()))


# ---------- бейзлайны ----------
def naive_paths(start_balance: float, horizon: int) -> np.ndarray:
    """Наивный: остаток не меняется."""
    return np.full((1, horizon), float(start_balance))


def seasonal_naive_paths(ops: pd.DataFrame, cutoff: date, start_balance: float, horizon: int, n_paths: int,
                         rng: np.random.Generator, weeks: int = 12) -> np.ndarray:
    """Сезонно-наивный: повторить чистые потоки прошлых недель (без исключённых операций).

    Каждая будущая неделя — случайный из последних `weeks` 7-дневных блоков (выровнены по отсечке), поэтому
    сохраняется структура дней недели и получается разброс для интервалов и шанса разрыва.
    """
    cutoff_ts = pd.Timestamp(cutoff)
    flows = ops[(ops["date"] <= cutoff_ts) & ~ops["category"].isin(EXCLUDED_FROM_MODEL)]
    signed = (flows["amount"] * flows["direction"]).groupby(flows["date"]).sum()
    first = ops["date"].min()
    blocks = []
    for k in range(weeks):
        start = cutoff_ts - pd.Timedelta(days=7 * (k + 1))
        if start < first:
            break
        days = pd.date_range(start + pd.Timedelta(days=1), periods=7, freq="D")
        blocks.append(signed.reindex(days, fill_value=0.0).values)
    if not blocks:
        return naive_paths(start_balance, horizon)
    blocks = np.array(blocks)                                        # [K, 7]
    n_weeks = -(-horizon // 7)
    draw = rng.integers(0, len(blocks), size=(n_paths, n_weeks))
    daily = blocks[draw].reshape(n_paths, n_weeks * 7)[:, :horizon]
    return start_balance + daily.cumsum(axis=1)


# ---------- сводка прогноза ----------
@dataclass
class Summary:
    p10: np.ndarray
    p50: np.ndarray
    p90: np.ndarray
    gap_probability: dict[int, float]        # по горизонтам: P(min_{t≤h} B < 0)
    possible_gap_day: int | None             # первый день (1..H) с P(B<0) ≥ 0,2
    shortfall_p50: float


def summarize(paths: np.ndarray, horizons: tuple[int, ...] = HORIZONS) -> Summary:
    """Перцентили и вероятности разрыва по матрице сценариев [N, H] (для детерминированных путей N = 1)."""
    below = paths < 0
    p10, p50, p90 = np.percentile(paths, [10, 50, 90], axis=0)
    prob_below = below.mean(axis=0)
    hit = np.nonzero(prob_below >= POSSIBLE_PROB)[0]
    shortfall = np.maximum(-paths, 0).max(axis=1)
    has_gap = shortfall > 0
    return Summary(
        p10, p50, p90, {h: float(below[:, :h].any(axis=1).mean()) for h in horizons if h <= paths.shape[1]},
        int(hit[0]) + 1 if len(hit) else None, float(np.median(shortfall[has_gap])) if has_gap.any() else 0.0,
    )


# ---------- метрики ----------
def rel_mae(p50: np.ndarray, b_op: np.ndarray, scale: float, h: int) -> float:
    return float(np.mean(np.abs(p50[:h] - b_op[:h])) / scale)


def coverage(p10: np.ndarray, p90: np.ndarray, b_op: np.ndarray, h: int) -> float:
    return float(np.mean((b_op[:h] >= p10[:h]) & (b_op[:h] <= p90[:h])))


def detection_metrics(y: np.ndarray, score: np.ndarray, threshold: float = YELLOW_PROB) -> dict:
    """ROC-AUC, PR-AUC, precision и recall при пороге «жёлтого». Если в выборке нет одного из классов — AUC = None."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    y, score = np.asarray(y, dtype=int), np.asarray(score, dtype=float)
    pred = score >= threshold
    tp = int((pred & (y == 1)).sum())
    out = {
        "n": len(y), "positives": int(y.sum()),
        "precision": tp / pred.sum() if pred.any() else None,
        "recall": tp / y.sum() if y.sum() else None,
        "roc_auc": None, "pr_auc": None,
    }
    if 0 < y.sum() < len(y):
        out["roc_auc"] = float(roc_auc_score(y, score))
        out["pr_auc"] = float(average_precision_score(y, score))
    return out


def reliability(score: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    """Диаграмма надёжности: «если мы говорим 30%, так и бывает примерно в 30% случаев»."""
    score, y = np.asarray(score, dtype=float), np.asarray(y, dtype=float)
    idx = np.minimum((score * bins).astype(int), bins - 1)
    return [{"bin": b, "predicted": float(score[idx == b].mean()), "observed": float(y[idx == b].mean()),
             "n": int((idx == b).sum())} for b in range(bins) if (idx == b).any()]


# ---------- потоки по группам (WAPE) ----------
FLOW_GROUPS = ("retail_revenue", "client_in", "platform_in", "other_in",
               "salary", "tax_enp", "rent", "debt", "suppliers", "other_out")


def flow_group(category: str, direction: int) -> str:
    if category in ("revenue_card", "revenue_sbp", "revenue_cash"):
        return "retail_revenue"
    if category in ("client_payment", "client_prepayment"):
        return "client_in"
    if category in ("revenue_marketplace", "revenue_agent"):
        return "platform_in"
    if category in ("salary", "tax_enp", "rent"):
        return category
    if category in ("loan_repayment", "leasing"):
        return "debt"
    if category in ("suppliers", "contractors", "logistics", "marketing"):
        return "suppliers"
    return "other_in" if direction > 0 else "other_out"


def actual_flows(full: pd.DataFrame, cutoff: date, horizon: int = 30) -> dict[str, float]:
    """Фактические суммы за горизонт по группам (без исключённых из модели потоков), положительные числа."""
    lo, hi = pd.Timestamp(cutoff), pd.Timestamp(cutoff + timedelta(days=horizon))
    ops = full[(full["date"] > lo) & (full["date"] <= hi) & ~full["category"].isin(EXCLUDED_FROM_MODEL)]
    if "reversal_pair_id" in ops:
        ops = ops[ops["reversal_pair_id"].isna()]
    out = dict.fromkeys(FLOW_GROUPS, 0.0)
    for (cat, direction), amount in ops.groupby(["category", "direction"])["amount"].sum().items():
        out[flow_group(cat, direction)] += float(amount)
    return out


def predicted_flows(model) -> dict[str, float]:
    """Ожидаемые суммы за горизонт по тем же группам из событий, выручки и прочих потоков модели."""
    out = dict.fromkeys(FLOW_GROUPS, 0.0)
    for e in model.events:
        out[flow_group(e.category, e.direction)] += e.amount * e.p_within_horizon
    if model.revenue is not None:
        out["retail_revenue"] += float(model.revenue.expected().sum())
    for f in model.future:                                          # выставление × задержка
        out["client_in" if f.direction > 0 else "suppliers"] += abs(float(f.expected().sum()))
    for r in model.rates:                                           # прочие потоки — по долям категорий истории
        total = abs(float(r.expected().sum()))
        for category, share in r.category_share.items():
            out[flow_group(category, r.direction)] += total * share
    return out


# ---------- прогон по компании ----------
def _approach_row(summary: Summary, truth: Truth) -> dict:
    return {
        "rel_mae": {h: rel_mae(summary.p50, truth.b_op, truth.scale, h) for h in HORIZONS},
        "coverage": {h: coverage(summary.p10, summary.p90, truth.b_op, h) for h in HORIZONS},
        "gap": summary.gap_probability, "possible_day": summary.possible_gap_day,
        "shortfall_p50": summary.shortfall_p50,
    }


def eligible(ops_full: pd.DataFrame, cutoff: date, horizon: int = 30) -> bool:
    """≥90 дней истории до отсечки и полные 30 дней после (счёт не закрыт, в выгрузке нет дыры)."""
    first, last = ops_full["date"].min().date(), ops_full["date"].max().date()
    return (first <= cutoff - timedelta(days=MIN_HISTORY_DAYS) and last >= cutoff + timedelta(days=horizon)
            and not has_data_gap(ops_full, cutoff, cutoff + timedelta(days=horizon)))


def run_company(ops_full: pd.DataFrame, cutoffs: list[date], contexts: dict, n_paths: int = 300,
                seed: int = 0, keep_paths_for: set[tuple[str, date]] | None = None) -> list[dict]:
    """Бэктест одной компании по отсечкам: четыре подхода против операционного остатка. Без утечек:
    модели получают только операции ≤ отсечки, контекст (сезонность, индекс ЕНП) посчитан на данных ≤ отсечки."""
    from app.engine.categorize import categorize
    from app.engine.enrich import enrich
    from app.engine.pipeline import build_model, evaluate, prepare
    from app.engine.types import Settings

    company = str(ops_full["company_id"].iloc[0])
    full = enrich(categorize(ops_full))                              # только для цели (категории исключённых потоков)
    points = []
    for cutoff in cutoffs:
        if not eligible(ops_full, cutoff):
            continue
        truth = build_truth(full, cutoff)
        if truth is None:
            continue
        prepared = prepare(ops_full[ops_full["date"] <= pd.Timestamp(cutoff)], cutoff)
        context = contexts.get(cutoff)
        rng = np.random.default_rng([seed, cutoff.toordinal()])
        rows, revenue_pred = {}, {}
        for name, method in ((STRUCTURAL, "m1"), (STRUCTURAL_M0, "m0"), (STRUCTURAL_M2, "m2")):
            model = build_model(None, cutoff, Settings(include_owner_withdrawals=False), prepared=prepared,
                                context=context, revenue_method=method)
            result = evaluate(model, n_paths, with_recommendations=False)
            rows[name] = _approach_row(summarize(result.simulation.balance), truth)
            revenue_pred[name] = predicted_flows(model)["retail_revenue"]
            if name == STRUCTURAL:
                start, structural = model.start_balance, result
                flows = {"predicted": predicted_flows(model), "actual": actual_flows(full, cutoff)}
        from app.engine.receivables import restore_open_documents

        documents = restore_open_documents(full, cutoff)
        external = build_model(None, cutoff, Settings(include_owner_withdrawals=False), prepared=prepared,
                               context=context, invoices=documents)
        rows[STRUCTURAL_EXT] = _approach_row(summarize(evaluate(external, n_paths, with_recommendations=False)
                                                       .simulation.balance), truth)
        rows[NAIVE] = _approach_row(summarize(naive_paths(start, 30)), truth)
        rows[SEASONAL_NAIVE] = _approach_row(
            summarize(seasonal_naive_paths(prepared.ops, cutoff, start, 30, n_paths, rng)), truth)
        point = {
            "company": company, "cutoff": cutoff.isoformat(), "scale": truth.scale,
            "first_gap": truth.first_gap, "max_shortfall": truth.max_shortfall,
            "y": {h: bool((truth.b_op[:h] < 0).any()) for h in HORIZONS},
            "rows": rows, "level": structural.risk.level, "flows": flows, "revenue_pred": revenue_pred,
            "has_docs": any(i.direction == "in" for i in documents),
            "flows_external": predicted_flows(external),
            "predicted_date": (structural.risk.possible_gap_date or structural.risk.likely_gap_date),
        }
        if keep_paths_for and (company, cutoff) in keep_paths_for:
            point["paths"] = {"p10": structural.p10, "p50": structural.p50, "p90": structural.p90,
                              "b_op": truth.b_op, "b_fact": truth.b_fact,
                              "dates": [d.isoformat() for d in structural.dates]}
        points.append(point)
    return points
