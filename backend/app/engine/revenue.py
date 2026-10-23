"""Выручка с карт, СБП и наличных (§6.9): продажи по ДАТЕ ПРОДАЖИ → зачисление по лагу.

Каналы: `card` (эквайринг + СБП, зачисление на следующий рабочий день после продажи) и `cash` (взнос в день
операции). Маркетплейсы и сервисы бронирования — потоки в recurring.py, оплаты клиентов по счетам — receivables/rates.

Модели: M0 — сезонно-наивная (среднее того же дня недели за 4 недели), M1 — декомпозиция
`ŝ(d) = level × weekday[w(d)] × season(month(d)) / season(month(as_of))`.
Неопределённость: множитель уровня на сценарий (ошибки 30-дневной суммы на прошлых отсечках) × дневной шум.
"""
from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd

from app.engine.calendar_ru import is_workday, shift

LEVEL_DAYS = 28
WEEKDAY_WEEKS = 8
M0_WEEKS = 4
PSEUDO_HORIZON = 30                    # на сколько дней вперёд меряем ошибку уровня на прошлых отсечках
PSEUDO_STEP_DAYS = 7
PSEUDO_MIN_HISTORY = 56
MAX_LEVEL_RATIOS = 52
MIN_LEVEL_RATIOS = 8
RATIO_CLIP = (0.3, 2.5)
RESID_CLIP = (0.2, 4.0)
SEASON_CLIP = (0.1, 10.0)
DEFAULT_LEVEL_SIGMA = 0.25
SHORT_HISTORY_SIGMA = 0.40
SHORT_HISTORY_DAYS = 90
ZERO_REVENUE_DAYS = 14
MONTH_COVERAGE = 0.8                   # месяц годится для сезонного индекса, если ≥80% дней без дыр
GAP_MARGIN_BEFORE, GAP_MARGIN_AFTER = 4, 3

# канал → (категории, лаг зачисления)
CHANNELS = {"card": (("revenue_card", "revenue_sbp"), "next"), "cash": (("revenue_cash",), "zero")}


def credit_date(sales_day: date, lag: str) -> date:
    """Дата зачисления продажи: следующий рабочий день после продажи (карты/СБП) или день взноса (наличные)."""
    return shift(sales_day + timedelta(days=1), "next") if lag == "next" else shift(sales_day, "next")


def effective_weekday(d: date) -> int:
    """День недели для профиля: праздник в будни = профиль воскресенья."""
    return 6 if (d.weekday() < 5 and not is_workday(d)) else d.weekday()


def _cutoff(as_of: date, lag: str) -> date:
    """Последний день продаж, зачисление которого уже произошло к концу as_of."""
    d = as_of
    while credit_date(d, lag) > as_of:
        d -= timedelta(days=1)
    return d


def _bad_days(gaps) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    return [(pd.Timestamp(g.start_date - timedelta(days=GAP_MARGIN_BEFORE)),
             pd.Timestamp(g.end_date + timedelta(days=GAP_MARGIN_AFTER))) for g in gaps or []]


def sales_series(ops: pd.DataFrame, as_of: date, channel: str, gaps=None) -> pd.Series | None:
    """Дневные продажи канала по дате продажи до cutoff; дни дыр — NaN; None, если продаж не было."""
    cats, lag = CHANNELS[channel]
    rows = ops[(ops["date"] <= pd.Timestamp(as_of)) & ops["category"].isin(cats) & (ops["direction"] == 1)]
    if "reversal_pair_id" in rows:
        rows = rows[rows["reversal_pair_id"].isna()]
    if rows.empty:
        return None
    if lag == "next":
        fallback = rows["date"] - pd.Timedelta(days=1)
        day = rows["sales_date"].fillna(fallback) if "sales_date" in rows else fallback
    else:
        day = rows["date"]
    cutoff = pd.Timestamp(_cutoff(as_of, lag))
    daily = rows.groupby(day)["amount"].sum()
    daily = daily[daily.index <= cutoff]
    if daily.empty:
        return None
    series = daily.reindex(pd.date_range(daily.index.min(), cutoff, freq="D"), fill_value=0.0).astype(float)
    for lo, hi in _bad_days(gaps):
        series[(series.index >= lo) & (series.index <= hi)] = np.nan
    return series


def _weekday_factors(values: np.ndarray, weekdays: np.ndarray) -> np.ndarray:
    """Медиана отношения «день / среднее блока из 7 дней» за последние 8 блоков, нормировка к среднему 1."""
    n = min(WEEKDAY_WEEKS, len(values) // 7)
    if n == 0:
        return np.ones(7)
    blocks, bw = values[-n * 7:].reshape(n, 7), weekdays[-n * 7:].reshape(n, 7)
    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore", RuntimeWarning)       # блок целиком из дыр
        mean = np.nanmean(blocks, axis=1, keepdims=True)
        ratio = np.where(mean > 0, blocks / mean, np.nan)
    factor = np.ones(7)
    for w in range(7):
        r = ratio[bw == w]
        r = r[np.isfinite(r)]
        if len(r):
            factor[w] = np.median(r)
    return factor / factor.mean() if factor.mean() > 0 else np.ones(7)


def _level(series: pd.Series) -> float:
    tail = series.iloc[-LEVEL_DAYS:].values
    return float(np.nanmean(tail)) if np.isfinite(tail).any() else 0.0


def _predict(series: pd.Series, days: list[date], method: str,
             season: Callable[[date], float] = lambda d: 1.0) -> np.ndarray:
    """Прогноз продаж на `days` по ряду, заканчивающемуся в cutoff."""
    if method == "m0":
        tail = series.iloc[-M0_WEEKS * 7:]
        weekdays = tail.index.day_of_week.values
        by_wd = {}
        for w in range(7):
            v = tail.values[weekdays == w]
            by_wd[w] = float(np.nanmean(v)) if np.isfinite(v).any() else 0.0
        return np.array([by_wd[effective_weekday(d)] for d in days])
    level = _level(series)
    factor = _weekday_factors(series.values, series.index.day_of_week.values)
    return np.array([level * factor[effective_weekday(d)] * season(d) for d in days])


def _month_means(series: pd.Series) -> dict[pd.Period, float]:
    """Средние дневные продажи по полностью покрытым месяцам."""
    out = {}
    for p, g in series.groupby(series.index.to_period("M")):
        last_day = p.end_time.normalize()
        if series.index[0] > p.start_time or series.index[-1] < last_day:
            continue                                         # месяц неполный
        valid = g.dropna()
        if len(valid) >= MONTH_COVERAGE * p.days_in_month:
            out[p] = float(valid.mean())
    return out


def season_ratio_fn(series: pd.Series, industry: dict[int, float] | None) -> Callable[[date], float]:
    """d → season(month(d)) / season(month(cutoff)): свой индекс (тот же месяц год назад), иначе отраслевой."""
    means = _month_means(series)
    base_p = pd.Period(series.index[-1], "M")
    base_ly = means.get(base_p - 12)

    def ratio(d: date) -> float:
        p = pd.Period(d, "M")
        if p == base_p:
            return 1.0
        target_ly = means.get(p - 12)
        if base_ly and base_ly > 0 and target_ly is not None:
            return float(np.clip(target_ly / base_ly, *SEASON_CLIP))
        if industry and industry.get(base_p.month, 0) > 0 and p.month in industry:
            return float(np.clip(industry[p.month] / industry[base_p.month], *SEASON_CLIP))
        return 1.0

    return ratio


def _level_ratios(series: pd.Series, method: str) -> np.ndarray:
    """Отношения факт/прогноз 30-дневной суммы на прошлых отсечках; при нехватке истории — логнормальная сетка."""
    ratios, t = [], len(series) - 1 - PSEUDO_HORIZON
    while t >= PSEUDO_MIN_HISTORY and len(ratios) < MAX_LEVEL_RATIOS:
        hist, actual = series.iloc[: t + 1], series.iloc[t + 1: t + 1 + PSEUDO_HORIZON]
        if not actual.isna().any() and np.isfinite(hist.iloc[-LEVEL_DAYS:].values).any():
            pred = _predict(hist, [d.date() for d in actual.index], method).sum()
            if pred > 0:
                ratios.append(float(np.clip(actual.sum() / pred, *RATIO_CLIP)))
        t -= PSEUDO_STEP_DAYS
    if len(ratios) >= MIN_LEVEL_RATIOS:
        return np.array(ratios)
    sigma = SHORT_HISTORY_SIGMA if len(series) < SHORT_HISTORY_DAYS else DEFAULT_LEVEL_SIGMA
    z = [NormalDist().inv_cdf(q) for q in np.linspace(0.02, 0.98, 49)]
    return np.exp(sigma * np.array(z) - sigma ** 2 / 2)


def _residuals(series: pd.Series) -> dict[int, np.ndarray]:
    """Дневной шум: отношение факта к модели за последние 8 недель, по дням недели."""
    tail = series.iloc[-WEEKDAY_WEEKS * 7:]
    level, factor = _level(series), _weekday_factors(series.values, series.index.day_of_week.values)
    pred = np.array([level * factor[effective_weekday(d.date())] for d in tail.index])
    with np.errstate(all="ignore"):
        ratio = np.where(pred > 0, tail.values / pred, np.nan)
    wd = np.array([effective_weekday(d.date()) for d in tail.index])
    fallback = ratio[np.isfinite(ratio)]
    fallback = np.clip(fallback, *RESID_CLIP) if len(fallback) else np.array([1.0])
    out = {}
    for w in range(7):
        r = ratio[(wd == w) & np.isfinite(ratio)]
        out[w] = np.clip(r, *RESID_CLIP) if len(r) >= 2 else fallback
    return out


@dataclass
class SalesChannel:
    name: str
    days: list[date]                   # будущие дни продаж
    s_hat: np.ndarray                  # прогноз продаж на эти дни
    credit_idx: np.ndarray             # индекс дня зачисления в горизонте или −1
    weekday: np.ndarray                # профильный день недели
    resid: dict[int, np.ndarray]
    level_ratios: np.ndarray


@dataclass
class RevenueModel:
    dates: list[date]
    channels: list[SalesChannel]
    flags: set[str] = field(default_factory=set)       # zero_revenue_14d, short_history
    method: str = "m1"

    def expected(self) -> np.ndarray:
        """Ожидаемые зачисления по дням горизонта без случайности (для объяснений и тестов)."""
        out = np.zeros(len(self.dates))
        for ch in self.channels:
            for s, ci in zip(ch.s_hat, ch.credit_idx, strict=True):
                if ci >= 0:
                    out[ci] += s
        return out

    def sample(self, dates: list[date], n_paths: int, rng: np.random.Generator) -> np.ndarray:
        """Зачисления [n_paths, H]. Множитель уровня общий для каналов (одна квантиль на сценарий)."""
        if list(dates) != self.dates:
            raise ValueError("Модель выручки построена на другом горизонте")
        flows = np.zeros((n_paths, len(dates)))
        u = rng.random(n_paths)
        for ch in self.channels:
            mult = np.quantile(ch.level_ratios, u)
            for s, ci, w in zip(ch.s_hat, ch.credit_idx, ch.weekday, strict=True):
                if ci >= 0 and s > 0:
                    flows[:, ci] += s * mult * rng.choice(ch.resid[int(w)], size=n_paths)
        return flows


def fit_revenue(ops: pd.DataFrame, as_of: date, horizon: int = 30, gaps=None,
                industry_season: dict[int, float] | None = None, method: str = "m1") -> RevenueModel | None:
    """Модель выручки на горизонт (as_of, as_of + horizon]; None, если у бизнеса нет карточных/наличных продаж.

    `method`: "m0" — сезонно-наивный бейзлайн, "m1" — декомпозиция. `industry_season` — отраслевой индекс месяцев
    (на данных ≤ as_of), используется, пока у бизнеса нет того же месяца год назад.
    """
    dates = [as_of + timedelta(days=i) for i in range(1, horizon + 1)]
    channels, flags = [], set()
    for name, (_, lag) in CHANNELS.items():
        series = sales_series(ops, as_of, name, gaps)
        if series is None or not np.nansum(series.values) > 0:
            continue
        cutoff = series.index[-1].date()
        days = [cutoff + timedelta(days=k) for k in range(1, (dates[-1] - cutoff).days + 1)]
        season = season_ratio_fn(series, industry_season) if method == "m1" else (lambda d: 1.0)
        credits = [credit_date(d, lag) for d in days]
        index = {d: i for i, d in enumerate(dates)}
        channels.append(SalesChannel(
            name=name, days=days, s_hat=_predict(series, days, method, season),
            credit_idx=np.array([index.get(c, -1) for c in credits]),
            weekday=np.array([effective_weekday(d) for d in days]),
            resid=_residuals(series), level_ratios=_level_ratios(series, method),
        ))
        tail = series.iloc[-ZERO_REVENUE_DAYS:].dropna()
        if len(tail) and tail.sum() == 0 and series.sum() > 0:
            flags.add("zero_revenue_14d")
        if len(series) < SHORT_HISTORY_DAYS:
            flags.add("short_history")
    return RevenueModel(dates, channels, flags, method) if channels else None
