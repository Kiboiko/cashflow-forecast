"""Данные «со стороны» для прогноза одного бизнеса: отраслевой индекс сезонности и индекс сезонности ЕНП.

Считаются по ВСЕМ компаниям на данных ≤ as_of (без утечек) и передаются в `run_forecast`. Нужны, пока у
бизнеса нет того же месяца год назад (короткая история, холодный старт).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from app.engine.categorize.taxonomy import REVENUE_CATEGORIES
from app.engine.taxes import compute_enp_index

MIN_MONTHS = 12


@dataclass
class Context:
    industry_season: dict[int, float] | None = None      # месяц → индекс (среднее по месяцам = 1)
    enp_index: dict[int, float] | None = None


def compute_industry_season(ops: pd.DataFrame, companies: pd.DataFrame, as_of: date) -> dict[str, dict[int, float]]:
    """Отрасль → {месяц: индекс}: средняя доля месяца в среднемесячной выручке компании по компаниям отрасли.

    `ops` — категоризированные операции всех компаний; `companies` — справочник (company_id, industry).
    В расчёт идут завершённые месяцы ≤ as_of и компании с историей выручки не меньше 12 месяцев.
    """
    as_of_ts = pd.Timestamp(as_of)
    rev = ops[(ops["date"] <= as_of_ts) & (ops["direction"] == 1) & ops["category"].isin(REVENUE_CATEGORIES)]
    complete_end = pd.Period(as_of, "M") if (as_of_ts + pd.Timedelta(days=1)).month != as_of.month \
        else pd.Period(as_of, "M") - 1
    industry_of = companies.set_index("company_id")["industry"]
    rows = []
    for cid, g in rev.groupby("company_id"):
        monthly = g.groupby(g["date"].dt.to_period("M"))["amount"].sum()
        monthly = monthly[monthly.index <= complete_end]
        monthly = monthly.reindex(pd.period_range(monthly.index.min(), complete_end, freq="M"), fill_value=0.0)
        monthly = monthly.iloc[1:]                                   # первый месяц истории обычно неполный
        if len(monthly) < MIN_MONTHS or monthly.mean() <= 0 or cid not in industry_of.index:
            continue
        ratio = monthly / monthly.mean()
        rows.append(pd.DataFrame({"industry": industry_of[cid], "month": [p.month for p in ratio.index],
                                  "ratio": ratio.values}))
    if not rows:
        return {}
    index = pd.concat(rows).groupby(["industry", "month"])["ratio"].mean()
    return {ind: {m: float(index[ind].get(m, 1.0)) for m in range(1, 13)}
            for ind in index.index.get_level_values("industry").unique()}


def build_context(ops_all: pd.DataFrame, companies: pd.DataFrame, company_id: str, as_of: date) -> Context:
    """Контекст для компании: отраслевая сезонность и индекс ЕНП по данным всех компаний ≤ as_of."""
    seasons = compute_industry_season(ops_all, companies, as_of)
    industry = companies.set_index("company_id")["industry"].get(company_id)
    return Context(industry_season=seasons.get(industry), enp_index=compute_enp_index(ops_all, as_of))
