"""Сборка и Монте-Карло (§6.12): остаток каждого сценария = стартовый + накопленная сумма потоков компонентов.

Общие случайные числа. У каждого компонента свой генератор `default_rng([seed, crc32(ключ)])`: изменение
одного компонента (шок «что если», рекомендация) не сдвигает случайные числа остальных, поэтому разница
«до/после» — эффект действия, а не шум (§4.6 п. 5).
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from app.engine.types import PlannedEvent


def component_rng(seed: int, key: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(key.encode("utf-8"))])


def lognormal_factor(rng: np.random.Generator, cv: float, size) -> np.ndarray:
    """Логнормальный множитель со средним 1 и коэффициентом вариации cv."""
    if cv <= 0:
        return np.ones(size)
    sigma = np.sqrt(np.log1p(cv ** 2))
    return np.exp(rng.normal(-sigma ** 2 / 2, sigma, size))


@dataclass
class FixedEvent:
    """Платёж на фиксированную дату: сумма × логнормальный шум × Бернулли(p)."""
    event: PlannedEvent

    @property
    def key(self) -> str:
        return f"event:{self.event.id}"

    def sample(self, dates: list[date], n_paths: int, rng: np.random.Generator) -> np.ndarray:
        e = self.event
        out = np.zeros((n_paths, len(dates)))
        amount = e.amount * lognormal_factor(rng, e.amount_cv, n_paths)       # случайные числа берём всегда,
        happens = rng.random(n_paths) < e.p_within_horizon                    # даже если дата вне горизонта
        if e.date in dates:
            out[:, dates.index(e.date)] = e.direction * amount * happens
        return out


@dataclass
class TimedEvent:
    """Платёж с распределением даты (счета клиентов): дата из `date_pmf`, вне горизонта → 0."""
    event: PlannedEvent

    @property
    def key(self) -> str:
        return f"event:{self.event.id}"

    def sample(self, dates: list[date], n_paths: int, rng: np.random.Generator) -> np.ndarray:
        e = self.event
        out = np.zeros((n_paths, len(dates)))
        amount = e.amount * lognormal_factor(rng, e.amount_cv, n_paths)
        u = rng.random(n_paths)
        pmf = e.date_pmf or []
        cum = np.cumsum([p for _, p in pmf])
        slot = np.searchsorted(cum, u, side="right")                          # == len(pmf) → платежа не будет
        index = {d: i for i, d in enumerate(dates)}
        for k, (d, _) in enumerate(pmf):
            if d in index:
                out[slot == k, index[d]] = e.direction * amount[slot == k]
        return out


@dataclass
class Simulation:
    dates: list[date]
    start_balance: float
    flows: np.ndarray                   # [N, H] чистый поток дня
    inflow: np.ndarray                  # [N, H] поступления
    outflow: np.ndarray                 # [N, H] списания (положительные числа)
    balance: np.ndarray                 # [N, H] остаток на конец дня
    seed: int = 0
    components: dict[str, np.ndarray] | None = field(default=None, repr=False)

    def with_extra_flows(self, extra: np.ndarray) -> Simulation:
        """Те же случайные числа плюс детерминированный поток (рекомендация, ручной перевод)."""
        flows = self.flows + extra
        return Simulation(
            self.dates, self.start_balance, flows,
            self.inflow + np.maximum(extra, 0), self.outflow + np.maximum(-extra, 0),
            self.start_balance + flows.cumsum(axis=1), self.seed, self.components,
        )


def simulate(start_balance: float, dates: list[date], components: list, n_paths: int = 1000, seed: int = 0,
             keep_components: bool = False) -> Simulation:
    """Каждый компонент возвращает знаковую матрицу [N, H]; векторизовано, цель — <300 мс на 1000×90."""
    flows = np.zeros((n_paths, len(dates)))
    inflow = np.zeros_like(flows)
    outflow = np.zeros_like(flows)
    kept: dict[str, np.ndarray] = {}
    for i, c in enumerate(components):
        key = getattr(c, "key", f"{type(c).__name__}:{i}")
        m = c.sample(dates, n_paths, component_rng(seed, key))
        flows += m
        inflow += np.maximum(m, 0)
        outflow += np.maximum(-m, 0)
        if keep_components:
            kept[key] = m
    return Simulation(dates, start_balance, flows, inflow, outflow, start_balance + flows.cumsum(axis=1), seed,
                      kept or None)
