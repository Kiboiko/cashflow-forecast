"""Собранная модель бизнеса на дату as_of: всё, что нужно, чтобы запускать симуляции (базу, шоки, рекомендации)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from app.engine.context import Context
from app.engine.quality import QualityReport
from app.engine.types import PlannedEvent, RecurringStream, Settings

if TYPE_CHECKING:
    from app.engine.rates import RatesModel
    from app.engine.receivables import FutureInvoices, Invoice, Profiles
    from app.engine.revenue import RevenueModel


@dataclass
class Model:
    ops: pd.DataFrame                    # категоризированные и обогащённые операции ≤ as_of
    quality: QualityReport
    as_of: date
    horizon: int
    settings: Settings
    seed: int
    context: Context
    dates: list[date]
    start_balance: float
    streams: list[RecurringStream] = field(default_factory=list)
    events: list[PlannedEvent] = field(default_factory=list)       # регулярные + налоги + план предпринимателя
    planned: list[PlannedEvent] = field(default_factory=list)
    revenue: RevenueModel | None = None
    rates: list[RatesModel] = field(default_factory=list)
    invoices: list[Invoice] | None = None          # открытые счета из учётной системы; None — источник не подключён
    profiles: Profiles | None = None               # профили задержек клиентов и поставщиков
    future: list[FutureInvoices] = field(default_factory=list)   # выставление × задержка (если подключены счета)
