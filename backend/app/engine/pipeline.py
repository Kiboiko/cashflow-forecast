"""Конвейер прогноза (§4.2, §6.0): `run_forecast(...)` → `ForecastResult`.

Операции → качество → категоризация → обогащение → регулярные платежи, налоги, выручка, прочие потоки,
план предпринимателя → Монте-Карло → риск → объяснения → рекомендации. Чистые функции: без БД и FastAPI,
детерминизм по seed.

Две стадии: `build_model` (обучение, дорого) и `evaluate` (симуляция, ~0,1 с). Базу, шоки «что если» и
рекомендации считают на одной модели и одних случайных числах.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine import explain
from app.engine.bank import bank_events, merge_bank
from app.engine.categorize import categorize
from app.engine.context import Context
from app.engine.enrich import enrich
from app.engine.model import Model
from app.engine.quality import QualityReport, run_quality
from app.engine.rates import fit_rates
from app.engine.receivables import (
    CLIENT_CATEGORIES,
    SUPPLIER_CATEGORIES,
    Invoice,
    build_profiles,
    contract_events,
    fit_future_invoices,
    invoice_events,
)
from app.engine.recommend import recommend
from app.engine.recurring import detect_streams, expand_streams
from app.engine.revenue import fit_revenue
from app.engine.risk import assess_risk
from app.engine.scenarios import apply_shocks
from app.engine.simulate import simulate
from app.engine.taxes import forecast_enp, tax_debt_flag
from app.engine.types import ForecastResult, PlannedEvent, Settings
from app.schemas import DataQuality, GapOut, Insight

MODEL_VERSION = "structural-mc-1.0"
RECENT_GAP_DAYS = 30
MAX_INACTIVE_NOTES = 3
HISTORY_NOTES = {"short_history": "Истории мало — прогноз ориентировочный",
                 "no_full_year": "Нет полного года истории — сезонность оценена по отрасли"}


@dataclass
class Prepared:
    """Операции, прошедшие конвейер до прогноза: можно кэшировать по версии данных."""
    ops: pd.DataFrame
    quality: QualityReport


def stable_seed(ops: pd.DataFrame, as_of: date) -> int:
    company = str(ops["company_id"].iloc[0]) if len(ops) else ""
    return zlib.crc32(f"{company}|{as_of.isoformat()}".encode())


def prepare(ops: pd.DataFrame, as_of: date, user_rules: dict[str, str] | None = None, ml_model=None) -> Prepared:
    """Качество → категоризация (если категорий ещё нет) → обогащение. Только операции с датой ≤ as_of."""
    ops = ops[ops["date"] <= pd.Timestamp(as_of)]
    quality = run_quality(ops, pd.Timestamp(as_of))
    cat = quality.ops if "category" in quality.ops.columns else categorize(quality.ops, user_rules, ml_model=ml_model)
    return Prepared(enrich(cat), quality)


def build_model(ops: pd.DataFrame, as_of: date, settings: Settings | None = None,
                planned: list[PlannedEvent] | None = None, horizon: int | None = None, seed: int | None = None,
                user_rules: dict[str, str] | None = None, context: Context | None = None,
                prepared: Prepared | None = None, revenue_method: str = "m1",
                invoices: list[Invoice] | None = None, bank: dict | None = None) -> Model:
    """Обучение моделей на операциях ≤ as_of. `ops` — нормализованные операции ОДНОГО бизнеса.

    `revenue_method`: "m1" — декомпозиция (основная), "m0" — сезонно-наивная, "m2" — бустинг (нужен `context.revenue_m2`).
    `invoices`: открытые счета из учётной системы, CSV, 1С или ручного ввода (None — источник не подключён).
    Тогда оплаты клиентов и поставщиков ведут явные счета и модель «выставление × задержка», а не недельный бутстрэп.
    `bank`: ответ банка (графики кредитов, реестр зарплатного проекта, неисполненные платёжки, см. bank.py) — заменяет
    регулярные платежи тех же категорий, выведенные из истории.
    """
    settings = settings or Settings()
    horizon = horizon or settings.horizon_days
    context = context or Context()
    planned = planned or []
    prepared = prepared or prepare(ops, as_of, user_rules)
    ops_p, quality = prepared.ops, prepared.quality
    last_day = ops_p["date"].max()
    start_balance = float(ops_p.loc[ops_p["date"] == last_day, "balance_eod"].iloc[-1])
    if settings.include_other_accounts:
        start_balance += sum(float(a.get("balance", 0.0)) for a in settings.other_accounts)

    streams, revenue, rates, rec_events, tax_events = [], None, [], [], []
    profiles, future, plan_events = None, [], []
    if invoices is not None and not quality.inactive:
        profiles = build_profiles(ops_p, as_of, context.industry_delays)
        future = [f for f in (fit_future_invoices(ops_p, as_of, horizon, profiles, d) for d in (1, -1)) if f]
        plan_events = invoice_events(invoices, profiles, as_of, horizon) + contract_events(ops_p, as_of, horizon)
    handled = {f.direction for f in future}                    # потоки, которые теперь ведут счета
    exclude = {1: frozenset(CLIENT_CATEGORIES[:1]) if 1 in handled else frozenset(),
               -1: frozenset(SUPPLIER_CATEGORIES) if -1 in handled else frozenset()}
    if not quality.inactive:
        streams = detect_streams(ops_p, as_of)
        if not settings.include_owner_withdrawals:
            streams = [s for s in streams if s.category != "owner_withdrawal"]
        rec_events = expand_streams(streams, as_of, horizon)
        tax_events = forecast_enp(ops_p, as_of, horizon, context.enp_index)
        revenue = fit_revenue(ops_p, as_of, horizon, quality.gaps, context.industry_season, revenue_method,
                              context.revenue_m2, context.industry_code)
        rates = [m for m in (fit_rates(ops_p, as_of, horizon, direction, streams, quality.gaps,
                                       context.industry_season, exclude[direction])
                             for direction in (1, -1)) if m is not None]
    events = merge_bank(rec_events + tax_events + planned + plan_events, bank_events(bank, as_of, horizon))
    events = sorted(events, key=lambda e: (e.date or as_of, e.id))
    return Model(
        ops=ops_p, quality=quality, as_of=as_of, horizon=horizon, settings=settings,
        seed=stable_seed(ops_p, as_of) if seed is None else seed, context=context,
        dates=[as_of + timedelta(days=i) for i in range(1, horizon + 1)], start_balance=start_balance,
        streams=streams, events=events, planned=planned, revenue=revenue, rates=rates, invoices=invoices,
        profiles=profiles, future=future,
    )


def _flags(model: Model, inactive_streams: bool) -> list[str]:
    ops, q, as_of = model.ops, model.quality, model.as_of
    year_ago = pd.Timestamp(as_of - timedelta(days=365))
    flags = []
    if ops[(ops["date"] > year_ago) & ops["category"].isin(explain.OWNER_MONEY)]["date"].nunique() >= 2:
        flags.append("owner_injections_history")
    if tax_debt_flag(ops, as_of):
        flags.append("tax_debt")
    overdraft = ops[(ops["category"] == "loan_repayment") & ops["purpose_norm"].str.contains("овердрафт")
                    & (ops["date"] > year_ago)]
    if model.settings.overdraft_limit == 0 and not overdraft.empty:
        flags.append("overdraft_hint")
    if q.history != "full":
        flags.append(q.history)
    if q.inactive:
        flags.append("inactive")
    if model.revenue is not None:
        flags.extend(sorted(model.revenue.flags - {"short_history"}))
    if any((as_of - g.end_date).days <= RECENT_GAP_DAYS for g in q.gaps):
        flags.append("data_gap")
    if inactive_streams:
        flags.append("inactive_streams")
    return list(dict.fromkeys(flags))


def evaluate(model: Model, n_paths: int = 1000, shocks: list | None = None,
             with_recommendations: bool = True) -> ForecastResult:
    """Симуляция модели (с шоками «что если» или без) → риск, объяснения, рекомендации."""
    as_of, ops_p, quality = model.as_of, model.ops, model.quality
    events, components = apply_shocks(model, shocks or [])
    sim = simulate(model.start_balance, model.dates, components, n_paths, model.seed,
                   keep_components=with_recommendations and not shocks)
    stats = assess_risk(sim, as_of, model.settings, events)
    flags = _flags(model, any(not s.active for s in model.streams))
    risk = explain.build_risk(stats, events, model.revenue, as_of, model.horizon, flags)
    if quality.inactive:
        risk.headline = "Операций по счёту давно не было — прогноз не строим, пока не появятся новые данные."

    insights = explain.owner_money_insight(ops_p, as_of)
    insights += [Insight(kind="stream_inactive", text=s.explain) for s in model.streams
                 if not s.active][:MAX_INACTIVE_NOTES]
    if "overdraft_hint" in flags:
        insights.append(Insight(kind="overdraft_hint", text="Похоже, у вас есть овердрафт — укажите лимит в настройках"))
    if quality.history in HISTORY_NOTES:
        insights.append(Insight(kind="history", text=HISTORY_NOTES[quality.history]))

    p10, p50, p90 = np.percentile(sim.balance, [10, 50, 90], axis=0)
    unknown = ops_p[(ops_p["category_source"] == "unknown")
                    & (ops_p["date"] > pd.Timestamp(as_of - timedelta(days=365)))]
    used = [name for name, on in (("recurring", [e for e in events if e.source == "recurring"]),
                                  ("taxes", [e for e in events if e.source == "tax_calendar"]),
                                  ("revenue", model.revenue), ("rates", model.rates),
                                  ("plan", model.planned), ("invoices", model.future or model.invoices),
                                  ("bank", [e for e in events if e.source == "bank_mock"])) if on]
    return ForecastResult(
        as_of=as_of, horizon=model.horizon, start_balance=model.start_balance,
        threshold=model.settings.effective_threshold, dates=model.dates, p10=p10, p50=p50, p90=p90,
        inflow_p50=np.median(sim.inflow, axis=0), outflow_p50=np.median(sim.outflow, axis=0),
        prob_below=stats.prob_below, events=events, risk=risk,
        recommendations=recommend(model, sim, stats) if with_recommendations and not shocks else [],
        insights=insights,
        data_quality=DataQuality(
            data_as_of=ops_p["date"].max().date(),
            gaps=[GapOut(start=g.start_date, end=g.end_date, unexplained_amount=g.unexplained_amount)
                  for g in quality.gaps],
            unknown_ops=len(unknown), history=quality.history, inactive=quality.inactive),
        model_info={"version": MODEL_VERSION, "n_paths": n_paths, "seed": model.seed, "components": used},
        simulation=sim,
    )


def run_forecast(ops: pd.DataFrame, as_of: date, settings: Settings | None = None,
                 planned: list[PlannedEvent] | None = None, horizon: int | None = None, n_paths: int = 1000,
                 seed: int | None = None, shocks: list | None = None, user_rules: dict[str, str] | None = None,
                 context: Context | None = None, prepared: Prepared | None = None,
                 with_recommendations: bool = True) -> ForecastResult:
    """Прогноз остатка и риска разрыва на `horizon` дней после `as_of`.

    `ops` — нормализованные операции ОДНОГО бизнеса (normalize.py); все модели учатся только на дате ≤ as_of.
    `planned` — события предпринимателя и внешних источников (счета, договоры, ручные).
    `seed` по умолчанию — стабильный хэш (бизнес, as_of).
    """
    model = build_model(ops, as_of, settings, planned, horizon, seed, user_rules, context, prepared)
    return evaluate(model, n_paths, shocks, with_recommendations)


def run_scenario(model: Model, shocks: list, n_paths: int = 1000) -> tuple[ForecastResult, ForecastResult]:
    """База и сценарий «что если» на одной модели и общих случайных числах: (база, сценарий)."""
    return evaluate(model, n_paths, with_recommendations=False), evaluate(model, n_paths, shocks, False)
