"""Прочие потоки (§6.10): поставщики, подрядчики, логистика, реклама, оплаты клиентов без учётных данных,
нераспознанное. Недельный бутстрэп: на сценарий берём недельную сумму из последних 12 недель и раскладываем
по дням недели по профилю компании.

Недели — скользящие блоки по 7 дней, выровненные по as_of (блок j горизонта = (as_of + 7j, as_of + 7(j+1)]),
поэтому неполных недель на границе нет. В расчёт не входят потоки из найденных регулярных платежей
(они уже в recurring), налоги (taxes), продажи по картам и наличные (revenue), деньги владельца и разовые операции.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.calendar_ru import is_workday
from app.engine.categorize.taxonomy import EXCLUDED_FROM_MODEL
from app.engine.recurring import stream_keys
from app.engine.types import RecurringStream

WEEKS = 12
MIN_WEEKS = 4
HANDLED_ELSEWHERE = {"tax_enp", "revenue_card", "revenue_sbp", "revenue_cash"}
SEASON_CLIP = (0.1, 10.0)


@dataclass
class RatesModel:
    direction: int
    dates: list[date]
    weekly_sums: np.ndarray          # исторические недельные суммы (≥0)
    share: np.ndarray                # доля недельной суммы на каждый день горизонта
    weeks_used: int
    category_share: dict[str, float] = field(default_factory=dict)   # доля категории в потоке (для шоков)

    @property
    def key(self) -> str:
        return "rates:in" if self.direction > 0 else "rates:out"

    def expected(self) -> np.ndarray:
        return self.direction * self.weekly_sums.mean() * self.share

    def sample(self, dates: list[date], n_paths: int, rng: np.random.Generator) -> np.ndarray:
        if list(dates) != self.dates:
            raise ValueError("Модель потоков построена на другом горизонте")
        n_blocks = -(-len(dates) // 7)
        draw = rng.choice(self.weekly_sums, size=(n_paths, n_blocks))
        block = np.arange(len(dates)) // 7
        return self.direction * draw[:, block] * self.share


def _block_edges(as_of: date, k: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Блок k истории: (as_of − 7(k+1), as_of − 7k]."""
    return pd.Timestamp(as_of - timedelta(days=7 * (k + 1))), pd.Timestamp(as_of - timedelta(days=7 * k))


def fit_rates(ops: pd.DataFrame, as_of: date, horizon: int, direction: int,
              streams: list[RecurringStream] | None = None, gaps=None,
              season: dict[int, float] | None = None) -> RatesModel | None:
    """Модель нерегулярных потоков одного направления (+1 приходы, −1 расходы); None, если истории мало.

    `streams` — все найденные регулярные потоки (активные и нет): их операции не учитываются.
    `season` — отраслевой индекс месяцев; множитель дня = season[month(d)] / season[month(as_of)].
    """
    as_of_ts = pd.Timestamp(as_of)
    hist = ops[(ops["date"] <= as_of_ts) & (ops["direction"] == direction)]
    hist = hist[~hist["category"].isin(EXCLUDED_FROM_MODEL | HANDLED_ELSEWHERE)]
    if "reversal_pair_id" in hist:
        hist = hist[hist["reversal_pair_id"].isna()]
    if streams:
        hist = hist[~stream_keys(hist).isin({s.key for s in streams})]
    first_op = ops["date"].min()
    bad = [(pd.Timestamp(g.start_date - timedelta(days=3)), pd.Timestamp(g.end_date + timedelta(days=3)))
           for g in gaps or []]

    sums, profile, by_category = [], np.zeros(7), {}
    for k in range(WEEKS):
        lo, hi = _block_edges(as_of, k)
        if lo < first_op - pd.Timedelta(days=1) or any(lo < b_hi and hi > b_lo for b_lo, b_hi in bad):
            continue                                         # блок до начала истории или задет дырой
        block = hist[(hist["date"] > lo) & (hist["date"] <= hi)]
        sums.append(float(block["amount"].sum()))
        np.add.at(profile, block["date"].dt.day_of_week.values, block["amount"].values)
        for cat, amount in block.groupby("category")["amount"].sum().items():
            by_category[cat] = by_category.get(cat, 0.0) + float(amount)
    weekly = np.array(sums)                                  # нулевые недели — тоже исход, не отбрасываем
    if len(weekly) < MIN_WEEKS or weekly.sum() <= 0 or profile.sum() <= 0:
        return None
    profile = profile / profile.sum()

    dates = [as_of + timedelta(days=i) for i in range(1, horizon + 1)]
    share = np.zeros(len(dates))
    for b in range(-(-horizon // 7)):
        week = [as_of + timedelta(days=7 * b + k) for k in range(1, 8)]
        raw = np.array([profile[d.weekday()] if is_workday(d) else 0.0 for d in week])
        if raw.sum() > 0:
            raw = raw / raw.sum()
        for k, d in enumerate(week):
            if 7 * b + k < horizon:
                share[7 * b + k] = raw[k]
    if season and season.get(as_of.month, 0) > 0:
        share *= np.array([np.clip(season.get(d.month, season[as_of.month]) / season[as_of.month], *SEASON_CLIP)
                           for d in dates])
    total = sum(by_category.values())
    return RatesModel(direction, dates, weekly, share, len(sums), {c: v / total for c, v in by_category.items()})
