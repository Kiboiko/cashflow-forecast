"""Объяснения шаблонами из структуры прогноза, без LLM (§6.14): детерминированно и тестируемо.

Словарь интерфейса — §9.3: «может не хватить денег», «шанс — высокий», «в плохом сценарии».
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.revenue import RevenueModel
from app.engine.risk import RiskStats
from app.engine.types import PlannedEvent
from app.schemas import Driver, Insight, Risk

_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
        "ноября", "декабря"]
_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре",
         "ноябре", "декабре"]
WINDOW_BEFORE_DAYS = 7           # причины ищем в окне 7 дней до даты разрыва
WINDOW_AFTER_DAYS = 1
MAX_DRIVERS = 3
SEASON_DROP = 0.9                # выручка месяца ниже 90% от текущего уровня — причина «сезон»
OWNER_MONEY = {"owner_injection", "own_transfer_in"}


def rub(x: float) -> str:
    """«240 000 ₽»: до тысячи рублей для крупных сумм, до сотни для мелких."""
    x = round(x, -3) if abs(x) >= 10_000 else round(x, -2)
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def dm(d: date) -> str:
    return f"{d.day} {_GEN[d.month - 1]}"


def plural_raz(n: int) -> str:
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "раза"
    return "раз"


# «не хватит на …»: винительный падеж по категории; для остальных — название платежа строчными
PAY_FOR = {
    "rent": "аренду", "tax_enp": "налоги", "loan_repayment": "платёж по кредиту", "telecom_it": "связь и интернет",
    "bank_fees": "комиссии банка", "services": "услуги", "contractors": "подрядчиков", "marketing": "рекламу",
    "suppliers": "поставщиков", "logistics": "перевозки", "owner_withdrawal": "вывод владельцу",
}


def pay_for(e: PlannedEvent) -> str:
    return PAY_FOR.get(e.category, e.title.lower())


def _gap_date(stats: RiskStats) -> date:
    return stats.possible_gap_date or stats.likely_gap_date or stats.peak_date


def window_outflows(events: list[PlannedEvent], around: date) -> list[PlannedEvent]:
    """Крупнейшие оттоки в окне до даты разрыва, сначала обязательные."""
    lo, hi = around - timedelta(days=WINDOW_BEFORE_DAYS), around + timedelta(days=WINDOW_AFTER_DAYS)
    window = [e for e in events if e.direction == -1 and e.date and lo <= e.date <= hi]
    return sorted(window, key=lambda e: (not e.mandatory, -e.amount))


def headline(stats: RiskStats, events: list[PlannedEvent], horizon: int) -> str:
    chance = round(stats.gap_probability * 100)
    if stats.level == "green":
        return (f"В ближайшие {horizon} дней денег хватает на все обязательные платежи. "
                f"Самый низкий остаток — около {rub(stats.min_p50)} ({dm(stats.min_p50_date)}).")
    gap = _gap_date(stats)
    if stats.level == "yellow":
        return (f"Есть риск нехватки денег около {dm(gap)}: в плохом сценарии до {rub(stats.shortfall_p90)}. "
                f"Шанс — {chance}%.")
    names = " и ".join(pay_for(e) for e in window_outflows(events, gap)[:2]) or "обязательные платежи"
    return f"{dm(gap)} может не хватить около {rub(stats.shortfall_p50)} на {names}. Шанс — высокий ({chance}%)."


def seasonality_driver(revenue: RevenueModel | None, gap: date, as_of: date) -> Driver | None:
    """«В августе выручка обычно на 25% ниже, чем в июле»: сезонный множитель месяца разрыва против месяца as_of."""
    if revenue is None or gap.month == as_of.month:
        return None
    ratios = [float(c.season[i]) for c in revenue.channels for i, d in enumerate(c.days)
              if (d.year, d.month) == (gap.year, gap.month)]
    if not ratios or (ratio := float(np.mean(ratios))) >= SEASON_DROP:
        return None
    pct = round((1 - ratio) * 100)
    return Driver(kind="seasonality",
                  text=f"В {_PREP[gap.month - 1]} выручка обычно на {pct}% ниже, чем в {_PREP[as_of.month - 1]}")


def build_drivers(stats: RiskStats, events: list[PlannedEvent], revenue: RevenueModel | None,
                  as_of: date) -> list[Driver]:
    if stats.level == "green":
        return []
    gap = _gap_date(stats)
    drivers = [Driver(kind="outflow", date=e.date, amount=e.amount, title=e.title, category=e.category)
               for e in window_outflows(events, gap)[:MAX_DRIVERS]]
    for e in events:                                           # «налог выше обычного» и подобные отличия
        if e.direction == -1 and e.date and abs((e.date - gap).days) <= WINDOW_BEFORE_DAYS and "авансов" in e.explain:
            drivers.append(Driver(kind="deviation", date=e.date, title=e.title, text=e.explain))
    season = seasonality_driver(revenue, gap, as_of)
    if season:
        drivers.append(season)
    return drivers


def build_risk(stats: RiskStats, events: list[PlannedEvent], revenue: RevenueModel | None, as_of: date,
               horizon: int, flags: list[str]) -> Risk:
    return Risk(
        level=stats.level, gap_probability=round(stats.gap_probability, 2),
        possible_gap_date=stats.possible_gap_date, likely_gap_date=stats.likely_gap_date,
        shortfall_p50=round(stats.shortfall_p50, 2), shortfall_p90=round(stats.shortfall_p90, 2),
        safe_buffer=round(stats.safe_buffer, 2), headline=headline(stats, events, horizon),
        drivers=build_drivers(stats, events, revenue, as_of), mandatory_coverage=stats.coverage, flags=flags,
    )


def owner_money_insight(ops: pd.DataFrame, as_of: date) -> list[Insight]:
    """«За год вы 4 раза докладывали свои деньги»: займы владельца и переводы со своих счетов в других банках."""
    recent = ops[(ops["date"] > pd.Timestamp(as_of - timedelta(days=365))) & (ops["date"] <= pd.Timestamp(as_of))
                 & ops["category"].isin(OWNER_MONEY)]
    n = int(recent["date"].nunique())
    if n == 0:
        return []
    return [Insight(kind="owner_injections",
                    text=f"За год вы {n} {plural_raz(n)} докладывали свои деньги, чтобы закрыть нехватку")]
