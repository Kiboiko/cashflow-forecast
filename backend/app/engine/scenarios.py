"""Сценарии «что если» (§6.16): шоки применяются к компонентам ДО симуляции.

Ключи компонентов и порядок случайных чисел не меняются, поэтому сравнение базы и сценария идёт на общих
случайных числах: разница — эффект шока, а не шум (§4.6 п. 5).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.categorize.taxonomy import REVENUE_CATEGORIES
from app.engine.explain import dm, plural, rub
from app.engine.model import Model
from app.engine.rates import WEEKS
from app.engine.recurring import stream_keys
from app.engine.simulate import FixedEvent, TimedEvent
from app.engine.types import ForecastResult, PlannedEvent
from app.schemas import (
    ClientDelayShock,
    FixedCostIncreaseShock,
    OneOffExpenseShock,
    RevenueDropShock,
    ScenarioDelta,
)

CLIENT_CATEGORIES = ("client_payment", "client_prepayment")
CATEGORY_NAMES = {"rent": "аренда", "suppliers": "закупки", "salary": "зарплата", "utilities": "коммунальные услуги",
                  "services": "услуги", "marketing": "реклама", "contractors": "подрядчики"}


@dataclass
class Scaled:
    """Компонент с множителем по дням. Ключ как у внутреннего → те же случайные числа."""
    inner: object
    factors: np.ndarray

    @property
    def key(self) -> str:
        return self.inner.key

    def __getattr__(self, name):                 # direction, category_share и т. п. — от внутренней модели
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def sample(self, dates, n_paths, rng):
        return self.inner.sample(dates, n_paths, rng) * self.factors


@dataclass
class DeterministicFlow:
    """Детерминированная поправка к потоку (перенос оплат клиента)."""
    key: str
    flows: np.ndarray

    def sample(self, dates, n_paths, rng):
        return np.tile(self.flows, (n_paths, 1))


def largest_client(model: Model) -> str | None:
    """Ключ крупнейшего клиента по оплатам за последний год."""
    ops = model.ops
    paid = ops[(ops["direction"] == 1) & ops["category"].isin(CLIENT_CATEGORIES)
               & (ops["date"] > pd.Timestamp(model.as_of - timedelta(days=365)))]
    return str(paid.groupby("cp_key")["amount"].sum().idxmax()) if not paid.empty else None


def _client_flows(model: Model, key: str) -> np.ndarray | None:
    """Ожидаемые оплаты клиента вне регулярных потоков: недельное среднее × дневная раскладка потока прочих приходов."""
    rates_in = next((r for r in model.rates if r.direction == 1), None)
    if rates_in is None:
        return None
    ops = model.ops
    paid = ops[(ops["direction"] == 1) & ops["category"].isin(CLIENT_CATEGORIES) & (ops["cp_key"] == key)
               & (ops["date"] > pd.Timestamp(model.as_of - timedelta(days=7 * WEEKS)))
               & (ops["date"] <= pd.Timestamp(model.as_of))]
    paid = paid[~stream_keys(paid).isin({s.key for s in model.streams})]
    return float(paid["amount"].sum()) / WEEKS * rates_in.share


def _delay(flows: np.ndarray, days: int) -> np.ndarray:
    out = np.zeros_like(flows)
    if days < len(flows):
        out[days:] = flows[: len(flows) - days]
    return out


def apply_shocks(model: Model, shocks: list) -> tuple[list[PlannedEvent], list]:
    """События и компоненты модели с учётом шоков. Без шоков — база."""
    events = [replace(e) for e in model.events]
    revenue, rates = model.revenue, list(model.rates)
    extra: list = []
    H = len(model.dates)
    for n, shock in enumerate(shocks):
        if isinstance(shock, ClientDelayShock):
            key = shock.counterparty_inn or largest_client(model)
            if key is None:
                continue
            for e in events:
                if e.direction == 1 and e.counterparty_inn == key and e.date:
                    e.date += timedelta(days=shock.delay_days)
                    if e.date_pmf:
                        e.date_pmf = [(d + timedelta(days=shock.delay_days), p) for d, p in e.date_pmf]
            flows = _client_flows(model, key)
            if flows is not None and flows.any():
                extra.append(DeterministicFlow(f"shock:client_delay:{n}", _delay(flows, shock.delay_days) - flows))
        elif isinstance(shock, RevenueDropShock):
            start = shock.from_date or model.dates[0]
            factors = np.array([1 - shock.pct / 100 if d >= start else 1.0 for d in model.dates])
            if revenue is not None:
                revenue = Scaled(revenue, factors)
            rates = [Scaled(r, factors) if r.direction == 1 else r for r in rates]
            for e in events:
                if e.direction == 1 and e.category in REVENUE_CATEGORIES and e.date and e.date >= start:
                    e.amount *= 1 - shock.pct / 100
        elif isinstance(shock, OneOffExpenseShock):
            events.append(PlannedEvent(
                id=f"shock:one_off:{n}:{shock.date.isoformat()}", direction=-1, amount=shock.amount, date=shock.date,
                category="one_off", title=shock.title, source="manual", mandatory=shock.mandatory,
                explain="Внеплановый расход из сценария «что если»"))
        elif isinstance(shock, FixedCostIncreaseShock):
            k = 1 + shock.pct / 100
            for e in events:
                if e.direction == -1 and e.category in shock.categories:
                    e.amount *= k
            rates = [Scaled(r, np.full(H, 1 + shock.pct / 100 * sum(r.category_share.get(c, 0.0)
                                                                    for c in shock.categories)))
                     if r.direction == -1 else r for r in rates]
    events.sort(key=lambda e: (e.date or model.as_of, e.id))
    components = [TimedEvent(e) if e.date_pmf else FixedEvent(e) for e in events]
    components += ([revenue] if revenue is not None else []) + rates + extra
    return events, components


# ---------- сравнение и текст ----------
def _gap_date(r: ForecastResult) -> date | None:
    return r.risk.possible_gap_date or r.risk.likely_gap_date


def scenario_delta(base: ForecastResult, scenario: ForecastResult) -> ScenarioDelta:
    b, s = _gap_date(base), _gap_date(scenario)
    return ScenarioDelta(
        gap_probability=round(scenario.risk.gap_probability - base.risk.gap_probability, 2),
        shortfall_p50=round(scenario.risk.shortfall_p50 - base.risk.shortfall_p50, 2),
        gap_date_shift_days=(s - b).days if b and s else None,
    )


def _span(days: int) -> str:
    if days % 7 == 0:
        return f"{days // 7} {plural(days // 7, 'неделю', 'недели', 'недель')}"
    return f"{days} {plural(days, 'день', 'дня', 'дней')}"


def _shock_phrase(shock) -> str:
    if isinstance(shock, ClientDelayShock):
        return f"клиент заплатит на {_span(shock.delay_days)} позже"
    if isinstance(shock, RevenueDropShock):
        return f"выручка упадёт на {shock.pct:g}%"
    if isinstance(shock, OneOffExpenseShock):
        return f"появится внеплановый расход {rub(shock.amount)} {dm(shock.date)}"
    names = " и ".join(CATEGORY_NAMES.get(c, c) for c in shock.categories)
    return f"{names} подорожают на {shock.pct:g}%"


def scenario_summary(shocks: list, base: ForecastResult, scenario: ForecastResult) -> str:
    """«Если клиент заплатит на 3 недели позже, денег может не хватить на 4 дня раньше и на 180 000 ₽ больше.»"""
    cause = "Если " + " и ".join(_shock_phrase(s) for s in shocks)
    delta = scenario_delta(base, scenario)
    b, s = _gap_date(base), _gap_date(scenario)
    parts = []
    if s and not b:
        parts.append(f"денег может не хватить около {dm(s)}")
    elif b and not s:
        return f"{cause}, нехватки денег не ожидается."
    elif delta.gap_date_shift_days:
        d = abs(delta.gap_date_shift_days)
        parts.append(f"денег может не хватить на {d} {plural(d, 'день', 'дня', 'дней')} "
                     f"{'раньше' if delta.gap_date_shift_days < 0 else 'позже'}")
    # «на X больше»: 90-й перцентиль нехватки по ВСЕМ сценариям монотонен при плохих шоках, в отличие от
    # медианы только по сценариям с разрывом (при шоке в неё попадают более мягкие разрывы)
    buffer = scenario.risk.safe_buffer - base.risk.safe_buffer
    if buffer > 1000:
        parts.append(f"на {rub(buffer)} больше" if b else f"не хватит около {rub(scenario.risk.safe_buffer)}")
    elif buffer < -1000:
        parts.append(f"на {rub(-buffer)} меньше")
    if not parts:
        chance = round(delta.gap_probability * 100)
        return f"{cause}, это почти не изменит прогноз." if abs(chance) < 2 else \
            f"{cause}, шанс нехватки денег {'вырастет' if chance > 0 else 'снизится'} на {abs(chance)} п. п."
    return f"{cause}, " + " и ".join(parts) + "."
