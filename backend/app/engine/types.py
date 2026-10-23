"""Типы движка (§6.0): регулярные потоки и плановые события. Чистые dataclasses, без FastAPI и БД."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

import numpy as np

Direction = Literal[1, -1]          # +1 поступление, −1 списание
Period = Literal["monthly", "weekly", "quarterly"]
ShiftRule = Literal["prev", "next"]


class Component(Protocol):
    """Источник денежных потоков в симуляции (§6.12): знаковая матрица [n_paths, len(dates)]."""

    def sample(self, dates: list[date], n_paths: int, rng: np.random.Generator) -> np.ndarray: ...


@dataclass
class RecurringStream:
    key: str                        # "salary:final" | "rent:0012345678" | ...
    category: str
    direction: Direction
    period: Period
    anchor_day: int                 # номинальный день месяца (для weekly — день недели 0–6)
    shift_rule: ShiftRule           # куда сдвигать с нерабочего дня
    amount: float
    amount_cv: float                # неопределённость суммы
    last_date: date
    active: bool
    confidence: float               # 0..1 → бейдж «точно / вероятно / возможно»
    mandatory: bool
    counterparty: str | None
    explain: str                    # «платите 10-го последние 6 мес., сумма ≈ медиана 3 выплат»
    seasonal_amounts: dict[int, float] | None = None   # месяц → сумма год назад (коммуналка)
    typical_delay_days: int = 0     # медианная задержка факта относительно плановой даты (реестр)

@dataclass
class PlannedEvent:                 # регулярный платёж, счёт, договор, налог, ручное событие
    id: str
    direction: Direction
    amount: float
    date: date | None               # фиксированная дата ИЛИ
    date_pmf: list[tuple[date, float]] | None = None   # распределение даты (счета клиентов)
    p_within_horizon: float = 1.0   # вероятность, что событие случится в горизонте
    amount_cv: float = 0.0
    category: str = ""
    title: str = ""
    source: str = "recurring"
    mandatory: bool = False
    counterparty: str | None = None
    explain: str = ""
