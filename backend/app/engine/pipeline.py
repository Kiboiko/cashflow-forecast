"""Конвейер прогноза (§4.2, §6.0): `run_forecast(...)` → `ForecastResult`.

Операции → качество → категоризация → обогащение → регулярные платежи, налоги, выручка, прочие потоки,
план предпринимателя → Монте-Карло → риск → объяснения. Чистая функция: без БД и FastAPI, детерминизм по seed.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine import explain
from app.engine.categorize import categorize
from app.engine.context import Context
from app.engine.enrich import enrich
from app.engine.quality import QualityReport, run_quality
from app.engine.rates import fit_rates
from app.engine.recurring import detect_streams, expand_streams
from app.engine.revenue import fit_revenue
from app.engine.risk import assess_risk
from app.engine.simulate import FixedEvent, TimedEvent, simulate
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


def prepare(ops: pd.DataFrame, as_of: date, user_rules: dict[str, str] | None = None) -> Prepared:
    """Качество → категоризация (если категорий ещё нет) → обогащение. Только операции с датой ≤ as_of."""
    ops = ops[ops["date"] <= pd.Timestamp(as_of)]
    quality = run_quality(ops, pd.Timestamp(as_of))
    cat = quality.ops if "category" in quality.ops.columns else categorize(quality.ops, user_rules)
    return Prepared(enrich(cat), quality)


def _flags(prepared: Prepared, settings: Settings, as_of: date, revenue, inactive_streams: bool) -> list[str]:
    ops, q = prepared.ops, prepared.quality
    flags = []
    owner = ops[(ops["date"] > pd.Timestamp(as_of - timedelta(days=365)))
                & ops["category"].isin(explain.OWNER_MONEY)]["date"].nunique()
    if owner >= 2:
        flags.append("owner_injections_history")
    if tax_debt_flag(ops, as_of):
        flags.append("tax_debt")
    overdraft = ops[(ops["category"] == "loan_repayment") & ops["purpose_norm"].str.contains("овердрафт")
                    & (ops["date"] > pd.Timestamp(as_of - timedelta(days=365)))]
    if settings.overdraft_limit == 0 and not overdraft.empty:
        flags.append("overdraft_hint")
    if q.history != "full":
        flags.append(q.history)
    if q.inactive:
        flags.append("inactive")
    if revenue is not None:
        flags.extend(sorted(revenue.flags - {"short_history"}))
    if any((as_of - g.end_date).days <= RECENT_GAP_DAYS for g in q.gaps):
        flags.append("data_gap")
    if inactive_streams:
        flags.append("inactive_streams")
    return list(dict.fromkeys(flags))


def run_forecast(ops: pd.DataFrame, as_of: date, settings: Settings | None = None,
                 planned: list[PlannedEvent] | None = None, horizon: int | None = None, n_paths: int = 1000,
                 seed: int | None = None, shocks: list | None = None, user_rules: dict[str, str] | None = None,
                 context: Context | None = None, prepared: Prepared | None = None) -> ForecastResult:
    """Прогноз остатка и риска разрыва на `horizon` дней после `as_of`.

    `ops` — нормализованные операции ОДНОГО бизнеса (normalize.py); все модели учатся только на дате ≤ as_of.
    `planned` — события предпринимателя и внешних источников (счета, договоры, ручные).
    `seed` по умолчанию — стабильный хэш (бизнес, as_of).
    """
    if shocks:
        raise NotImplementedError("Шоки «что если» подключаются в scenarios.py")
    settings = settings or Settings()
    horizon = horizon or settings.horizon_days
    context = context or Context()
    planned = planned or []
    prepared = prepared or prepare(ops, as_of, user_rules)
    ops_p, quality = prepared.ops, prepared.quality
    seed = stable_seed(ops_p, as_of) if seed is None else seed
    dates = [as_of + timedelta(days=i) for i in range(1, horizon + 1)]

    last_day = ops_p["date"].max()
    start_balance = float(ops_p.loc[ops_p["date"] == last_day, "balance_eod"].iloc[-1])
    if settings.include_other_accounts:
        start_balance += sum(float(a.get("balance", 0.0)) for a in settings.other_accounts)

    streams, revenue, rates, rec_events, tax_events = [], None, [], [], []
    if not quality.inactive:
        streams = detect_streams(ops_p, as_of)
        if not settings.include_owner_withdrawals:
            streams = [s for s in streams if s.category != "owner_withdrawal"]
        rec_events = expand_streams(streams, as_of, horizon)
        tax_events = forecast_enp(ops_p, as_of, horizon, context.enp_index)
        revenue = fit_revenue(ops_p, as_of, horizon, quality.gaps, context.industry_season)
        rates = [m for m in (fit_rates(ops_p, as_of, horizon, direction, streams, quality.gaps, context.industry_season)
                             for direction in (1, -1)) if m is not None]
    events = sorted(rec_events + tax_events + planned, key=lambda e: (e.date or as_of, e.id))
    components = [TimedEvent(e) if e.date_pmf else FixedEvent(e) for e in events]
    components += ([revenue] if revenue is not None else []) + rates

    sim = simulate(start_balance, dates, components, n_paths, seed, keep_components=True)
    stats = assess_risk(sim, as_of, settings, events)
    flags = _flags(prepared, settings, as_of, revenue, any(not s.active for s in streams))
    risk = explain.build_risk(stats, events, revenue, as_of, horizon, flags)
    if quality.inactive:
        risk.headline = "Операций по счёту давно не было — прогноз не строим, пока не появятся новые данные."

    insights = explain.owner_money_insight(ops_p, as_of)
    insights += [Insight(kind="stream_inactive", text=s.explain) for s in streams if not s.active][:MAX_INACTIVE_NOTES]
    if "overdraft_hint" in flags:
        insights.append(Insight(kind="overdraft_hint", text="Похоже, у вас есть овердрафт — укажите лимит в настройках"))
    if quality.history in HISTORY_NOTES:
        insights.append(Insight(kind="history", text=HISTORY_NOTES[quality.history]))

    p10, p50, p90 = np.percentile(sim.balance, [10, 50, 90], axis=0)
    unknown = ops_p[(ops_p["category_source"] == "unknown")
                    & (ops_p["date"] > pd.Timestamp(as_of - timedelta(days=365)))]
    return ForecastResult(
        as_of=as_of, horizon=horizon, start_balance=start_balance, threshold=settings.effective_threshold,
        dates=dates, p10=p10, p50=p50, p90=p90, inflow_p50=np.median(sim.inflow, axis=0),
        outflow_p50=np.median(sim.outflow, axis=0), prob_below=stats.prob_below, events=events, risk=risk,
        recommendations=[], insights=insights,
        data_quality=DataQuality(
            data_as_of=last_day.date(),
            gaps=[GapOut(start=g.start_date, end=g.end_date, unexplained_amount=g.unexplained_amount)
                  for g in quality.gaps],
            unknown_ops=len(unknown), history=quality.history, inactive=quality.inactive),
        model_info={"version": MODEL_VERSION, "n_paths": n_paths, "seed": seed,
                    "components": [name for name, used in (("recurring", rec_events), ("taxes", tax_events),
                                                           ("revenue", revenue), ("rates", rates),
                                                           ("plan", planned)) if used]},
        simulation=sim,
    )
