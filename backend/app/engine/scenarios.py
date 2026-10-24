"""Сценарии «что если» (§6.16): шоки применяются к компонентам ДО симуляции.

Ключи компонентов и порядок случайных чисел не меняются, поэтому сравнение базы и сценария идёт на общих
случайных числах: разница — эффект шока, а не шум (§4.6 п. 5).
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.categorize.taxonomy import REVENUE_CATEGORIES
from app.engine.explain import dm, plural, rub
from app.engine.model import Model
from app.engine.rates import WEEKS
from app.engine.recurring import stream_keys
from app.engine.simulate import FixedEvent, TimedEvent, lognormal_factor
from app.engine.types import ForecastResult, PlannedEvent
from app.schemas import (
    ClientDelayShock,
    FixedCostIncreaseShock,
    OneOffExpenseShock,
    RevenueDropShock,
    ScenarioDelta,
)

CLIENT_CATEGORIES = ("client_payment", "client_prepayment")
INVOICE_SOURCES = {"invoice", "csv", "1c", "demo_accounting", "contract_detected"}
INVOICE_LEVEL_CV = 0.2                    # общий риск оплат по счетам (настроен по покрытию интервалов на tune)
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
class SharedFactor:
    """Общий для сценария множитель на группу компонентов (платёжная дисциплина клиентов, сдвиг цен поставщиков).

    Все обёрнутые компоненты получают один и тот же множитель в сценарии: независимые шумы счетов в сумме
    сглаживаются, а общий риск остаётся. Ключ — как у внутреннего компонента (общие случайные числа).
    """
    inner: object
    seed: int
    name: str
    cv: float

    @property
    def key(self) -> str:
        return self.inner.key

    def __getattr__(self, name):
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)

    def sample(self, dates, n_paths, rng):
        factor = lognormal_factor(np.random.default_rng([self.seed, zlib.crc32(self.name.encode())]), self.cv,
                                  (n_paths, 1))
        return self.inner.sample(dates, n_paths, rng) * factor


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
    """Ожидаемые оплаты клиента, которые модель ведёт не отдельными событиями.

    Без учётных данных — недельное среднее клиента × дневная раскладка потока прочих приходов (rates). Если подключены
    счета, оплаты клиентов ведут явные счета (их сдвигаем как события) и «выставление × задержка» — здесь берём долю
    клиента в будущих выставлениях.
    """
    ops = model.ops
    paid = ops[(ops["direction"] == 1) & ops["category"].isin(CLIENT_CATEGORIES)
               & (ops["date"] > pd.Timestamp(model.as_of - timedelta(days=7 * WEEKS)))
               & (ops["date"] <= pd.Timestamp(model.as_of))]
    paid = paid[~stream_keys(paid).isin({s.key for s in model.streams})]
    client = float(paid.loc[paid["cp_key"] == key, "amount"].sum())
    future_in = next((f for f in model.future if f.direction == 1), None)
    if future_in is not None:
        total = float(paid["amount"].sum())
        return client / total * future_in.expected() if total > 0 else None
    rates_in = next((r for r in model.rates if r.direction == 1), None)
    if rates_in is None:
        return None
    return client / WEEKS * rates_in.share


def _delay(flows: np.ndarray, days: int) -> np.ndarray:
    out = np.zeros_like(flows)
    if days < len(flows):
        out[days:] = flows[: len(flows) - days]
    return out


def _invoice_factor(direction: int) -> str:
    """Общий множитель — свой у оплат клиентов и у счетов поставщиков: иначе приходы и расходы ходили бы вместе
    и гасили друг другу разброс."""
    return "invoices:in" if direction > 0 else "invoices:out"


def apply_shocks(model: Model, shocks: list) -> tuple[list[PlannedEvent], list]:
    """События и компоненты модели с учётом шоков. Без шоков — база."""
    events = [replace(e) for e in model.events]
    revenue, rates, future = model.revenue, list(model.rates), list(model.future)
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
            future = [Scaled(f, factors) if f.direction == 1 else f for f in future]
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
            if "suppliers" in shock.categories:
                future = [Scaled(f, np.full(H, k)) if f.direction == -1 else f for f in future]
    events.sort(key=lambda e: (e.date or model.as_of, e.id))
    components = []
    for e in events:
        c = TimedEvent(e) if e.date_pmf else FixedEvent(e)
        components.append(SharedFactor(c, model.seed, _invoice_factor(e.direction), INVOICE_LEVEL_CV)
                          if e.source in INVOICE_SOURCES else c)
    future = [SharedFactor(f, model.seed, _invoice_factor(f.direction), INVOICE_LEVEL_CV)
              if hasattr(f, "per_workday") else f for f in future]
    components += ([revenue] if revenue is not None else []) + rates + future + extra
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
    if s and not b:
        return f"{cause}, {dm(s)} может не хватить около {rub(scenario.risk.shortfall_p50)}."
    if b and not s:
        return f"{cause}, нехватки денег не ожидается."
    parts = []
    if delta.gap_date_shift_days:
        d = abs(delta.gap_date_shift_days)
        parts.append(f"денег может не хватить на {d} {plural(d, 'день', 'дня', 'дней')} "
                     f"{'раньше' if delta.gap_date_shift_days < 0 else 'позже'}")
    # сумма — та же, что в `delta.shortfall_p50` и заголовках риска («может не хватить около …»). Это медиана по
    # сценариям с разрывом: при плохом шоке в неё попадают и новые мягкие разрывы, и она может даже уменьшиться,
    # поэтому «меньше» говорим, только если и шанс разрыва не вырос
    if b and delta.shortfall_p50 > 1000:
        parts.append(f"на {rub(delta.shortfall_p50)} больше")
    elif b and delta.shortfall_p50 < -1000 and delta.gap_probability <= 0:
        parts.append(f"на {rub(-delta.shortfall_p50)} меньше")
    if not parts:
        chance = round(delta.gap_probability * 100)
        return f"{cause}, это почти не изменит прогноз." if abs(chance) < 2 else \
            f"{cause}, шанс нехватки денег {'вырастет' if chance > 0 else 'снизится'} на {abs(chance)} п. п."
    return f"{cause}, " + " и ".join(parts) + "."
