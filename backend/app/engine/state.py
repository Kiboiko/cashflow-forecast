"""Состояние бизнеса и инкрементальное обновление (§6.17): новые данные без полного пересчёта истории.

`EngineState.update(new_ops, as_of)`: сверка остатков по расширенной выписке, категоризация и обогащение
ТОЛЬКО новых операций (правила + память контрагентов, без переобучения), затем быстрая пересимуляция с нового as_of.
Полная пересборка — `EngineState.build` (при смене правил категорий, новой дыре в данных, раз в 30 дней).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import pandas as pd

from app.engine.categorize import categorize, learn_memory
from app.engine.context import Context
from app.engine.enrich import enrich
from app.engine.model import Model
from app.engine.pipeline import Prepared, build_model, evaluate
from app.engine.quality import QualityReport, run_quality
from app.engine.receivables import Invoice, build_profiles, receivables_summary
from app.engine.types import ForecastResult, PlannedEvent, Settings


@dataclass
class EngineState:
    raw: pd.DataFrame                    # нормализованные операции как пришли (до чистки дублей)
    ops: pd.DataFrame                    # после quality + категоризации + обогащения
    quality: QualityReport
    memory: pd.DataFrame                 # память контрагентов
    as_of: date
    settings: Settings
    context: Context
    user_rules: dict[str, str] | None = None
    ml_model: object | None = None       # ML-добивка категоризации (ml_fallback.train), необязательна

    @classmethod
    def build(cls, ops: pd.DataFrame, as_of: date, settings: Settings | None = None,
              user_rules: dict[str, str] | None = None, context: Context | None = None,
              ml_model: object | None = None) -> EngineState:
        """Полная сборка: качество → категоризация → обогащение всей истории ≤ as_of."""
        raw = ops[ops["date"] <= pd.Timestamp(as_of)].reset_index(drop=True)
        quality = run_quality(raw, pd.Timestamp(as_of))
        categorized = categorize(quality.ops, user_rules, ml_model=ml_model)
        return cls(raw, enrich(categorized), quality, learn_memory(categorized), as_of, settings or Settings(),
                   context or Context(), user_rules, ml_model)

    def update(self, new_ops: pd.DataFrame, as_of: date) -> EngineState:
        """Дозагрузка новых операций: категоризируются только они, история остаётся как есть."""
        fresh = new_ops[~new_ops["operation_id"].isin(self.raw["operation_id"])]
        raw = pd.concat([self.raw, fresh], ignore_index=True)
        raw = raw[raw["date"] <= pd.Timestamp(as_of)].sort_values(["date", "operation_id"], kind="stable")
        quality = run_quality(raw, pd.Timestamp(as_of))           # сверка и дыры — по расширенной выписке
        known = set(self.ops["operation_id"])
        new_rows = quality.ops[~quality.ops["operation_id"].isin(known)]
        added = (enrich(categorize(new_rows, self.user_rules, memory=self.memory, ml_model=self.ml_model))
                 if len(new_rows) else new_rows)
        old = self.ops.set_index("operation_id")
        keep = quality.ops[quality.ops["operation_id"].isin(known)].copy()   # свежие флаги качества, старые категории
        for col in old.columns.difference(keep.columns):
            keep[col] = keep["operation_id"].map(old[col])
        ops = pd.concat([keep, added], ignore_index=True).sort_values(["date", "operation_id"], kind="stable")
        ops = ops.reset_index(drop=True)
        return replace(self, raw=raw.reset_index(drop=True), ops=ops, quality=quality,
                       memory=learn_memory(ops), as_of=as_of)

    def model(self, planned: list[PlannedEvent] | None = None, horizon: int | None = None,
              seed: int | None = None, invoices: list[Invoice] | None = None) -> Model:
        """Модель для симуляций. `invoices` — открытые счета из учётной системы/CSV (None — источник не подключён)."""
        return build_model(None, self.as_of, self.settings, planned, horizon, seed, self.user_rules, self.context,
                           Prepared(self.ops, self.quality), invoices=invoices)

    def forecast(self, planned: list[PlannedEvent] | None = None, horizon: int | None = None, n_paths: int = 1000,
                 seed: int | None = None, with_recommendations: bool = True,
                 invoices: list[Invoice] | None = None) -> ForecastResult:
        return evaluate(self.model(planned, horizon, seed, invoices), n_paths,
                        with_recommendations=with_recommendations)

    def receivables(self, invoices: list[Invoice], direction: str = "in") -> list[dict]:
        """«Вам должны» (in) и «Вы должны» (out): открытые счета по контрагентам с обычной задержкой."""
        profiles = build_profiles(self.ops, self.as_of, self.context.industry_delays)
        return receivables_summary(invoices, profiles, self.as_of, direction)
