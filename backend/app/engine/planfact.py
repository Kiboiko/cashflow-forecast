"""Снимки прогнозов и план-факт (§6.17).

Каждый прогноз сохраняется как снимок (P10/P50/P90 по дням и плановые события). Когда приходят новые данные,
план сопоставляется с фактом: событие ↔ операция по направлению, контрагенту или категории, сумме и окну дат.
Несопоставленные события с истёкшим допуском — `overdue`: из них растут предупреждения «не пришла оплата».
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.calendar_ru import is_workday
from app.engine.categorize.taxonomy import EXCLUDED_FROM_MODEL
from app.engine.types import ForecastResult
from app.schemas import PlanFactDay, PlanFactEvent, PlanFactResponse, PlanFactSummary

MATCH_BEFORE_DAYS = 5               # платёж мог прийти чуть раньше срока
GRACE_WORKDAYS = 3                  # допуск для регулярных платежей
AMOUNT_TOL_PCT = 0.10
AMOUNT_TOL_RUB = 1_000.0
PARTIAL_MIN_SHARE = 0.30            # меньше 30% суммы — это не частичная оплата
DEVIATION_PCT = 0.20                # отклонение недели/дня, которое подсвечиваем
DEVIATION_RUB = 10_000.0
SNAPSHOT_VERSION = 1


def _grace_days(event) -> int:
    """Допуск в календарных днях: для счетов клиентов — до p75 распределения даты, для остальных 3 рабочих дня."""
    if event.date_pmf:
        pmf = sorted(event.date_pmf)
        total, acc = sum(p for _, p in pmf), 0.0
        for d, p in pmf:
            acc += p
            if acc >= 0.75 * total:
                return max((d - event.date).days, 0) if event.date else 0
    d = event.date
    for _ in range(GRACE_WORKDAYS):
        d += timedelta(days=1)
        while not is_workday(d):
            d += timedelta(days=1)
    return (d - event.date).days


def make_snapshot(result: ForecastResult, business_id: str = "") -> dict:
    """JSON-совместимый снимок прогноза для `ForecastSnapshot.payload`."""
    return {
        "version": SNAPSHOT_VERSION, "business_id": business_id, "as_of": result.as_of.isoformat(),
        "horizon": result.horizon, "start_balance": result.start_balance, "threshold": result.threshold,
        "dates": [d.isoformat() for d in result.dates],
        "p10": [round(float(v), 2) for v in result.p10], "p50": [round(float(v), 2) for v in result.p50],
        "p90": [round(float(v), 2) for v in result.p90],
        "inflow_p50": [round(float(v), 2) for v in result.inflow_p50],
        "outflow_p50": [round(float(v), 2) for v in result.outflow_p50],
        "events": [{
            "id": e.id, "date": e.date.isoformat() if e.date else None, "direction": e.direction,
            "amount": e.amount, "category": e.category, "title": e.title, "source": e.source,
            "mandatory": e.mandatory, "counterparty_inn": e.counterparty_inn, "counterparty": e.counterparty,
            "probability": e.p_within_horizon, "grace_days": _grace_days(e),
        } for e in result.events if e.date],
        "risk": {"level": result.risk.level, "gap_probability": result.risk.gap_probability,
                 "possible_gap_date": result.risk.possible_gap_date.isoformat() if result.risk.possible_gap_date else None,
                 "coverage": {c.event_id: c.covered_probability for c in result.risk.mandatory_coverage}},
    }


@dataclass
class Match:
    event: dict
    status: str                              # fulfilled | partially | overdue | planned
    operation_id: str | None = None
    amount: float | None = None
    days_late: int | None = None


def match_events(snapshot: dict, ops: pd.DataFrame, fact_until: date) -> list[Match]:
    """Жадное сопоставление: события по дате, для каждого — ближайшая по дате подходящая операция."""
    snap_as_of = date.fromisoformat(snapshot["as_of"])
    fact = ops[(ops["date"] > pd.Timestamp(snap_as_of)) & (ops["date"] <= pd.Timestamp(fact_until))
               & ~ops["category"].isin(EXCLUDED_FROM_MODEL | {"tax_enforced"})]
    used: set[str] = set()
    matches = []
    for e in sorted(snapshot["events"], key=lambda e: e["date"]):
        due = date.fromisoformat(e["date"])
        if due > fact_until:
            matches.append(Match(e, "planned"))
            continue
        lo = pd.Timestamp(due - timedelta(days=MATCH_BEFORE_DAYS))
        hi = pd.Timestamp(due + timedelta(days=e["grace_days"]))
        cand = fact[(fact["direction"] == e["direction"]) & (fact["date"] >= lo) & (fact["date"] <= hi)
                    & ~fact["operation_id"].isin(used)]
        same_party = (cand["cp_key"] == e["counterparty_inn"]) if e["counterparty_inn"] else cand["category"] == e["category"]
        cand = cand[same_party | (cand["category"] == e["category"])]
        tol = max(AMOUNT_TOL_PCT * e["amount"], AMOUNT_TOL_RUB)
        best, status = None, None
        for _, f in cand.iloc[np.argsort(np.abs((cand["date"] - pd.Timestamp(due)).dt.days.values), kind="stable")].iterrows():
            if abs(f["amount"] - e["amount"]) <= tol:
                best, status = f, "fulfilled"
                break
            if PARTIAL_MIN_SHARE * e["amount"] <= f["amount"] < e["amount"] - tol and best is None:
                best, status = f, "partially"
        if best is not None:
            used.add(best["operation_id"])
            matches.append(Match(e, status, best["operation_id"], float(best["amount"]),
                                 max((best["date"].date() - due).days, 0)))
        elif fact_until > due + timedelta(days=e["grace_days"]):
            matches.append(Match(e, "overdue", days_late=(fact_until - due).days))
        else:
            matches.append(Match(e, "planned"))
    return matches


def _deviation(plan: float, fact: float) -> float:
    return (fact - plan) / plan * 100 if plan > 0 else 0.0


def plan_fact(snapshot: dict, ops: pd.DataFrame, fact_until: date, snapshot_id: int = 0) -> tuple[PlanFactResponse, list[Match]]:
    """Сравнение снимка с фактом до `fact_until`. Возвращает ответ контракта и сопоставления (для предупреждений)."""
    dates = [date.fromisoformat(d) for d in snapshot["dates"]]
    snap_as_of = date.fromisoformat(snapshot["as_of"])
    window = ops[(ops["date"] > pd.Timestamp(snap_as_of)) & (ops["date"] <= pd.Timestamp(fact_until))]
    flows = window[~window["category"].isin(EXCLUDED_FROM_MODEL)]
    fact_in = flows[flows["direction"] == 1].groupby("date")["amount"].sum()
    fact_out = flows[flows["direction"] == -1].groupby("date")["amount"].sum()
    balance = ops.groupby("date")["balance_eod"].last()
    balance = balance.reindex(pd.date_range(balance.index.min(), max(balance.index.max(), pd.Timestamp(fact_until)))).ffill()
    daily = []
    for i, d in enumerate(dates):
        known = d <= fact_until
        ts = pd.Timestamp(d)
        daily.append(PlanFactDay(
            date=d, plan_in=snapshot["inflow_p50"][i], plan_out=snapshot["outflow_p50"][i],
            fact_in=float(fact_in.get(ts, 0.0)) if known else None,
            fact_out=float(fact_out.get(ts, 0.0)) if known else None,
            plan_balance_p50=snapshot["p50"][i], fact_balance=float(balance.get(ts, np.nan)) if known else None))
    matches = match_events(snapshot, ops, fact_until)
    events = [PlanFactEvent(
        event_id=m.event["id"], title=m.event["title"], expected_date=date.fromisoformat(m.event["date"]),
        amount=m.event["amount"], status=m.status, matched_transaction_id=m.operation_id, days_late=m.days_late)
        for m in matches if m.status != "planned"]
    known_days = [i for i, d in enumerate(dates) if d <= fact_until]
    plan_in = sum(snapshot["inflow_p50"][i] for i in known_days)
    plan_out = sum(snapshot["outflow_p50"][i] for i in known_days)
    response = PlanFactResponse(
        snapshot_id=snapshot_id, snapshot_as_of=snap_as_of, fact_until=fact_until, daily=daily, events=events,
        summary=PlanFactSummary(
            inflow_deviation_pct=round(_deviation(plan_in, float(fact_in.sum())), 1),
            outflow_deviation_pct=round(_deviation(plan_out, float(fact_out.sum())), 1),
            missed_events=sum(m.status == "overdue" for m in matches)),
    )
    return response, matches
