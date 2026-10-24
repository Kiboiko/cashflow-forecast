"""План-факт, снимки прогнозов, предупреждения, машина времени и отчёт бэктеста (§8)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select

from app.db.models import Alert
from app.db.session import DbSession
from app.schemas import (
    Alert as AlertOut,
)
from app.schemas import (
    BacktestReport,
    PlanFactOut,
    ReplayAdvanceRequest,
    ReplayResetRequest,
    ReplayResult,
    SnapshotBrief,
)
from app.security import CurrentUser, OwnedBusiness
from app.services import alert_service, replay_service

router = APIRouter(tags=["plan-fact"])


def _demo_only(request: Request) -> None:
    if not request.app.state.settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Не найдено")


@router.get("/businesses/{bid}/plan-fact", response_model=PlanFactOut)
def plan_fact(request: Request, business: OwnedBusiness, db: DbSession,
              snapshot_id: Annotated[int | None, Query()] = None) -> PlanFactOut:
    try:
        return replay_service.plan_fact_for(db, business, request.app.state.forecast_service, snapshot_id)
    except replay_service.ReplayError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/businesses/{bid}/snapshots", response_model=list[SnapshotBrief])
def snapshots(request: Request, business: OwnedBusiness, db: DbSession) -> list[SnapshotBrief]:
    request.app.state.forecast_service.ensure_snapshot(db, business)
    return replay_service.list_snapshots(db, business)


@router.get("/businesses/{bid}/alerts", response_model=list[AlertOut])
def alerts(business: OwnedBusiness, db: DbSession,
           status_filter: Annotated[str, Query(alias="status", pattern="^(open|all)$")] = "open") -> list[AlertOut]:
    return alert_service.list_alerts(db, business, status_filter)


@router.post("/businesses/{bid}/alerts/{aid}/ack", response_model=AlertOut)
def acknowledge_alert(aid: int, business: OwnedBusiness, db: DbSession) -> AlertOut:
    alert = db.scalar(select(Alert).where(Alert.id == aid, Alert.business_id == business.id))
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Предупреждение не найдено")
    return alert_service.acknowledge(db, business, alert)


@router.post("/demo/businesses/{bid}/replay/advance", response_model=ReplayResult, dependencies=[Depends(_demo_only)])
def replay_advance(body: ReplayAdvanceRequest, request: Request, business: OwnedBusiness, db: DbSession) -> ReplayResult:
    try:
        return replay_service.advance(db, business, request.app.state.forecast_service, body.days)
    except replay_service.ReplayError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/demo/businesses/{bid}/replay/reset", response_model=ReplayResult, dependencies=[Depends(_demo_only)])
def replay_reset(body: ReplayResetRequest, request: Request, business: OwnedBusiness, db: DbSession) -> ReplayResult:
    try:
        return replay_service.reset(db, business, request.app.state.forecast_service, body.as_of)
    except replay_service.ReplayError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/demo/warm-up", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(_demo_only)])
def warm_up(request: Request) -> dict:
    """После `make seed`: пусть API заранее посчитает прогнозы демо-бизнесов, чтобы первый вход жюри был быстрым."""
    threading.Thread(target=request.app.state.forecast_service.warm_up, args=(request.app.state.session_factory,),
                     daemon=True, name="warm-up").start()
    return {"status": "started"}


@router.get("/quality/backtest", response_model=BacktestReport)
def backtest(request: Request, _: CurrentUser) -> BacktestReport:
    """Свежий отчёт бэктеста человека A (`reports/backtest_<дата>.json`) для страницы «Как мы считаем»."""
    reports = sorted(Path(request.app.state.settings.reports_dir).glob("backtest_*.json"))
    if not reports:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Отчёт о качестве прогноза ещё не готов.")
    return BacktestReport(file=reports[-1].name, report=json.loads(reports[-1].read_text(encoding="utf-8")))
