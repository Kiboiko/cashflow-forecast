"""Налоги: единый налоговый платёж, ЕНП (§6.7).

Дата — 28-е, при выходном → следующий рабочий день. Сумма месяца — по истории компании:
тот же месяц год назад с поправкой на рост либо «обычные» месяцы с индексом сезонности `Q`.
Режим налогообложения не утверждаем: в данных его нет.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.calendar_ru import nominal_date, shift
from app.engine.categorize.taxonomy import REVENUE_CATEGORIES
from app.engine.types import PlannedEvent

ENP_DAY = 28
# Индекс сезонности ЕНП по календарным месяцам: средняя доля месяца в среднемесячном ЕНП компании,
# усреднённая по компаниям (данные ≤ 31.07.2026). Пики в марте, апреле, июле, октябре — авансы УСН.
# Это запасной вариант: в бэктесте индекс считается `compute_enp_index` на данных ≤ as_of.
# (В §3.1 индекс посчитан иначе, по суммам всех компаний, поэтому цифры различаются.)
DEFAULT_ENP_INDEX = {1: 0.57, 2: 0.66, 3: 1.39, 4: 1.79, 5: 0.71, 6: 0.96, 7: 1.46, 8: 0.73, 9: 0.59,
                     10: 1.34, 11: 0.69, 12: 0.60}
REGULAR_MAX = 1.2                        # «обычные» месяцы: индекс не выше (без пиков УСН)
ADVANCE_MONTHS = (4, 7, 10)              # кварталы УСН: авансовые платежи
ADVANCE_RATIO = 1.3
GROWTH_CLIP = (0.5, 2.0)
CV_CLIP = (0.1, 0.4)
REVENUE_RATIO_MONTHS = 3                 # налог ~ доля от выручки за 3 завершённых месяца
RATIO_HISTORY = 12
RATIO_WEIGHT = 0.5                       # вес оценки «доля от выручки» в смеси с оценкой по году назад/индексу
EVENT_PROBABILITY = 0.97
ENFORCED = ["tax_enforced", "enforcement"]
ENFORCED_LOOKBACK_DAYS = 90
_IN_MONTH = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре",
             "октябре", "ноябре", "декабре"]


def _enp_ops(ops: pd.DataFrame, as_of: date) -> pd.DataFrame:
    enp = ops[(ops["category"] == "tax_enp") & (ops["direction"] == -1) & (ops["date"] <= pd.Timestamp(as_of))]
    if "reversal_pair_id" in enp:
        enp = enp[enp["reversal_pair_id"].isna()]
    return enp


def _monthly(enp: pd.DataFrame, as_of: date) -> tuple[pd.Series, pd.Series]:
    """(суммы по всем месяцам, суммы по завершённым месяцам с нулями внутри истории)."""
    by_month = enp.groupby(enp["date"].dt.to_period("M"))["amount"].sum()
    if by_month.empty:
        return by_month, by_month
    complete_end = pd.Period(as_of, "M") if (as_of + timedelta(days=1)).month != as_of.month \
        else pd.Period(as_of, "M") - 1
    complete = by_month[by_month.index <= complete_end]
    if complete.empty:
        return by_month, complete
    full_range = pd.period_range(complete.index.min(), complete_end, freq="M")
    return by_month, complete.reindex(full_range, fill_value=0.0)


def _monthly_revenue(ops: pd.DataFrame, as_of: date, last_complete: pd.Period) -> pd.Series:
    """Выручка по завершённым месяцам (поступления от продаж и клиентов)."""
    rev = ops[(ops["direction"] == 1) & ops["category"].isin(REVENUE_CATEGORIES) & (ops["date"] <= pd.Timestamp(as_of))]
    monthly = rev.groupby(rev["date"].dt.to_period("M"))["amount"].sum()
    return monthly[monthly.index <= last_complete]


def compute_enp_index(ops: pd.DataFrame, as_of: date) -> dict[int, float]:
    """Индекс месяцев по всем компаниям на данных ≤ as_of: средняя доля месяца в среднемесячном ЕНП.

    Компании с историей ЕНП меньше 6 месяцев не учитываются. Месяцы без данных получают 1,0.
    """
    frames = []
    for _, g in ops.groupby("company_id"):
        _, cm = _monthly(_enp_ops(g, as_of), as_of)
        cm = cm[cm > 0]
        if len(cm) >= 6:
            frames.append(pd.DataFrame({"month": [p.month for p in cm.index], "ratio": (cm / cm.mean()).values}))
    if not frames:
        return dict(DEFAULT_ENP_INDEX)
    by_month = pd.concat(frames).groupby("month")["ratio"].mean()
    return {m: float(by_month.get(m, 1.0)) for m in range(1, 13)}


def _is_regular(index: dict[int, float], month: int) -> bool:
    return index[month] <= REGULAR_MAX


def _revenue_ratio_estimate(cm: pd.Series, inflow: pd.Series | None, target: pd.Period,
                            index: dict[int, float]) -> float | None:
    """Налог ≈ медианная доля ЕНП от выручки за 3 предыдущих месяца × выручка за 3 последних завершённых месяца.

    Доли берутся из месяцев того же типа (авансовые пики УСН или обычные) — так налог следует за ростом и сезоном выручки.
    """
    if inflow is None or inflow.empty:
        return None
    inflow = inflow.reindex(pd.period_range(inflow.index.min(), cm.index.max(), freq="M"), fill_value=0.0)
    ratios = []
    for p, tax in cm.items():
        if tax <= 0 or _is_regular(index, p.month) != _is_regular(index, target.month):
            continue
        base = sum(float(inflow.get(p - k, 0.0)) for k in range(1, REVENUE_RATIO_MONTHS + 1))
        if base > 0:
            ratios.append(tax / base)
    ratios = ratios[-RATIO_HISTORY:]
    recent = float(inflow.tail(REVENUE_RATIO_MONTHS).sum())
    return float(np.median(ratios)) * recent if len(ratios) >= 2 and recent > 0 else None


def _estimate(cm: pd.Series, target: pd.Period, index: dict[int, float],
              inflow: pd.Series | None = None) -> tuple[float, float, float]:
    """(оценка суммы месяца, CV, «типичный» обычный месяц) по завершённым месяцам компании."""
    positive = cm[cm > 0]
    regular = positive[[_is_regular(index, p.month) for p in positive.index]]
    typical = float((regular if len(regular) else positive).tail(3).median())
    regular_q = [v for v in index.values() if v <= REGULAR_MAX]
    scale = float(np.median(regular_q)) if regular_q else 1.0

    last3 = cm.tail(3)
    year_ago = cm.get(target - 12)
    if year_ago is not None and year_ago > 0:
        base = [cm.get(p - 12) for p in last3.index]
        growth = 1.0
        if all(b is not None and b > 0 for b in base):
            growth = float(np.clip(last3.sum() / sum(base), *GROWTH_CLIP))
        estimate = float(year_ago) * growth
    else:
        estimate = typical * index[target.month] / scale

    ratio = _revenue_ratio_estimate(cm, inflow, target, index)
    if ratio is not None:
        estimate = (1 - RATIO_WEIGHT) * estimate + RATIO_WEIGHT * ratio

    series = positive.tail(12)
    normalized = series.values / np.array([index[p.month] for p in series.index])
    cv = float(np.clip(normalized.std() / normalized.mean(), *CV_CLIP)) if len(series) >= 3 else 0.3
    return estimate, cv, typical


def forecast_enp(ops: pd.DataFrame, as_of: date, horizon: int = 30,
                 index: dict[int, float] | None = None) -> list[PlannedEvent]:
    """Плановые платежи ЕНП в (as_of, as_of + horizon]. Несколько платежей месяца суммируются на 28-е."""
    index = index or DEFAULT_ENP_INDEX
    enp = _enp_ops(ops, as_of)
    by_month, cm = _monthly(enp, as_of)
    if cm.empty or (cm > 0).sum() < 2:
        return []
    inflow = _monthly_revenue(ops, as_of, cm.index.max())
    end = as_of + timedelta(days=horizon)
    events = []
    for p in pd.period_range(pd.Period(as_of, "M"), pd.Period(end, "M"), freq="M"):
        d = shift(nominal_date(p.year, p.month, ENP_DAY), "next")
        if not as_of < d <= end:
            continue
        estimate, cv, typical = _estimate(cm, p, index, inflow)
        remaining = estimate - float(by_month.get(p, 0.0))        # часть месяца могла быть уплачена
        if remaining < 0.1 * estimate:
            continue
        if p.month in ADVANCE_MONTHS and estimate > ADVANCE_RATIO * typical:
            explain = (f"В {_IN_MONTH[p.month - 1]} налог обычно выше — похоже на авансовый платёж по УСН. "
                       "Оценка по вашей истории")
        else:
            explain = "ЕНП платите около 28-го; сумма оценена по вашей истории"
        events.append(PlannedEvent(
            id=f"tax:enp:{d.isoformat()}", direction=-1, amount=round(remaining, 2), date=d,
            p_within_horizon=EVENT_PROBABILITY, amount_cv=cv, category="tax_enp", title="Налоги (ЕНП)",
            source="tax_calendar", mandatory=True, explain=explain,
        ))
    return events


def tax_debt_flag(ops: pd.DataFrame, as_of: date) -> bool:
    """Были взыскания налоговой или исполнительные листы за 90 дней — возможны списания без вашего участия."""
    since = pd.Timestamp(as_of - timedelta(days=ENFORCED_LOOKBACK_DAYS))
    recent = ops[ops["category"].isin(ENFORCED) & (ops["date"] > since) & (ops["date"] <= pd.Timestamp(as_of))]
    return not recent.empty
