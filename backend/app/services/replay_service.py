"""Снимки, план-факт и «машина времени» (§6.17, §15.3).

Выписка демо-бизнеса загружена целиком, но видна только до `data_as_of`. Шаг «+N дней» сдвигает эту дату, дособирает
состояние движка (`EngineState.update`: категоризируются только новые операции), сравнивает прежний прогноз с фактом
и рождает предупреждения. Сброс возвращает дату назад и убирает снимки и предупреждения «из будущего».
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Alert, Business, ForecastSnapshot, Transaction
from app.engine.alerts import build_alerts
from app.engine.planfact import plan_fact
from app.schemas import PlanFactOut, ReplayResult, SnapshotBrief
from app.services import alert_service
from app.services.business_service import business_settings, last_operation_date
from app.services.engine_adapter import load_ops, persist_derived
from app.services.forecast_service import ForecastService
from app.services.personas import DEMO_AS_OF

log = logging.getLogger("kassa.replay")
MIN_HISTORY_DAYS = 30


class ReplayError(ValueError):
    """Машину времени нельзя сдвинуть так: сообщение показываем пользователю (HTTP 409/422)."""


# ---------- снимки и план-факт ----------
def snapshot_brief(snapshot: ForecastSnapshot) -> SnapshotBrief:
    risk = snapshot.payload.get("risk", {})
    return SnapshotBrief(id=snapshot.id, as_of=snapshot.as_of, horizon=snapshot.horizon, level=risk.get("level", "green"),
                         gap_probability=float(risk.get("gap_probability", 0.0)), created_at=snapshot.created_at)


def list_snapshots(db: Session, business: Business) -> list[SnapshotBrief]:
    rows = db.scalars(select(ForecastSnapshot).where(ForecastSnapshot.business_id == business.id)
                      .order_by(ForecastSnapshot.as_of, ForecastSnapshot.id)).all()
    latest: dict[date, ForecastSnapshot] = {}
    for row in rows:                                   # на одну дату — последний снимок (после правок плана)
        latest[row.as_of] = row
    return [snapshot_brief(s) for s in latest.values()]


def default_snapshot(db: Session, business: Business) -> ForecastSnapshot | None:
    """Самый ранний снимок, который ещё «видит» текущую дату: накопленное расхождение плана и факта с начала демо."""
    rows = db.scalars(select(ForecastSnapshot).where(ForecastSnapshot.business_id == business.id)
                      .order_by(ForecastSnapshot.as_of, ForecastSnapshot.id)).all()
    if not rows:
        return None
    by_date: dict[date, ForecastSnapshot] = {}
    for row in rows:
        by_date[row.as_of] = row
    as_of = business.data_as_of
    for snapshot in by_date.values():
        if snapshot.as_of < as_of <= snapshot.as_of + timedelta(days=snapshot.horizon):
            return snapshot
    return list(by_date.values())[-1]


def plan_fact_for(db: Session, business: Business, service: ForecastService, snapshot_id: int | None) -> PlanFactOut:
    if snapshot_id is not None:
        snapshot = db.get(ForecastSnapshot, snapshot_id)
        if snapshot is None or snapshot.business_id != business.id:
            raise ReplayError("Такого прогноза нет.")
    else:
        service.ensure_snapshot(db, business)
        snapshot = default_snapshot(db, business)
    state = service.state(db, business)
    fact_until = max(state.as_of, snapshot.as_of)
    response, _ = plan_fact(snapshot.payload, state.ops, fact_until, snapshot_id=snapshot.id)
    meta = {e["id"]: e for e in snapshot.payload.get("events", [])}      # направление и источник — из снимка
    events = []
    for event in response.events:
        known = meta.get(event.event_id, {})
        events.append({**event.model_dump(), "source": known.get("source"), "mandatory": bool(known.get("mandatory", False)),
                       "direction": None if "direction" not in known else ("in" if known["direction"] > 0 else "out")})
    return PlanFactOut.model_validate({**response.model_dump(), "events": events})


# ---------- машина времени ----------
def _result(db: Session, business: Business, service: ForecastService, previous: date, new_ops: int,
            risk_before: float | None, alerts: list[Alert]) -> ReplayResult:
    result = service.result(db, business, business_settings(business).horizon_days)
    snapshot = service.ensure_snapshot(db, business)
    until = last_operation_date(db, business)
    return ReplayResult(
        as_of=business.data_as_of, previous_as_of=previous, new_operations=new_ops, level=result.risk.level,
        gap_probability=result.risk.gap_probability, gap_probability_before=risk_before, headline=result.risk.headline,
        alerts=[alert_service.to_out(a) for a in alerts], snapshot_id=snapshot.id, data_until=until,
        can_advance=until is not None and business.data_as_of < until)


def advance(db: Session, business: Business, service: ForecastService, days: int) -> ReplayResult:
    started = time.perf_counter()
    current, until = business.data_as_of, last_operation_date(db, business)
    if current is None or until is None or current >= until:
        raise ReplayError("Данные закончились: дальше шагать некуда. Верните демо в начало.")
    target = min(current + timedelta(days=days), until)

    previous = service.ensure_snapshot(db, business)                    # прогноз «до шага» — база план-факта
    state = service.state(db, business)
    updated = state.update(load_ops(db, business, up_to=target), target)  # дозагрузка без полной пересборки
    business.data_as_of = target
    business.data_version += 1
    db.commit()
    service.adopt_state(business, updated)
    persist_derived(db, business, updated, only_new_after=current)

    result = service.result(db, business, business_settings(business).horizon_days)
    _, matches = plan_fact(previous.payload, updated.ops, target, snapshot_id=previous.id)
    drafts = build_alerts(result, updated.ops, previous.payload.get("risk"), matches)
    alerts = alert_service.store_alerts(db, business, drafts, alert_service.virtual_now(target))
    risk_before = float(previous.payload.get("risk", {}).get("gap_probability", 0.0))
    new_ops = int((updated.ops["date"] > pd.Timestamp(current)).sum())
    log.info("advance", extra={"fields": {"business": business.id, "days": days, "new_operations": new_ops,
                                          "alerts": len(alerts), "ms": round((time.perf_counter() - started) * 1000)}})
    return _result(db, business, service, current, new_ops, risk_before, alerts)


def reset(db: Session, business: Business, service: ForecastService, as_of: date | None) -> ReplayResult:
    target = as_of or date.fromisoformat(DEMO_AS_OF)
    first, until = _first_operation_date(db, business), last_operation_date(db, business)
    if first is None or until is None:
        raise ReplayError("У этого бизнеса нет операций.")
    if target > until or target < first + timedelta(days=MIN_HISTORY_DAYS):
        raise ReplayError(f"Дата должна быть между {first + timedelta(days=MIN_HISTORY_DAYS)} и {until}.")
    previous = business.data_as_of or target
    db.execute(delete(ForecastSnapshot).where(ForecastSnapshot.business_id == business.id, ForecastSnapshot.as_of > target))
    db.execute(delete(Alert).where(Alert.business_id == business.id,
                                   Alert.created_at > alert_service.virtual_now(target) + timedelta(hours=1)))
    business.data_as_of = target
    business.data_version += 1
    db.commit()
    service.clear()                                                    # состояния после даты больше не нужны
    return _result(db, business, service, previous, 0, None, [])


def _first_operation_date(db: Session, business: Business) -> date | None:
    return db.scalar(select(func.min(Transaction.date)).where(Transaction.business_id == business.id))
