"""«Вам должны» / «Вы должны», регулярные платежи, «что если» (§8)."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from app.db.session import DbSession
from app.schemas import (
    ReceivablesResponse,
    RecurringOut,
    ScenarioPresets,
    ScenarioRequest,
    ScenarioResponse,
)
from app.security import OwnedBusiness

router = APIRouter(tags=["plan"])


@router.get("/businesses/{bid}/receivables", response_model=ReceivablesResponse)
def receivables(request: Request, business: OwnedBusiness, db: DbSession,
                direction: Annotated[Literal["in", "out"], Query()] = "in") -> ReceivablesResponse:
    return request.app.state.forecast_service.receivables(db, business, direction)


@router.get("/businesses/{bid}/recurring", response_model=list[RecurringOut])
def recurring(request: Request, business: OwnedBusiness, db: DbSession) -> list[RecurringOut]:
    return request.app.state.forecast_service.recurring(db, business)


@router.get("/businesses/{bid}/scenarios/presets", response_model=ScenarioPresets)
def scenario_presets(request: Request, business: OwnedBusiness, db: DbSession) -> ScenarioPresets:
    return request.app.state.forecast_service.scenario_presets(db, business)


@router.post("/businesses/{bid}/scenarios/run", response_model=ScenarioResponse)
def run_scenario(body: ScenarioRequest, request: Request, business: OwnedBusiness, db: DbSession) -> ScenarioResponse:
    return request.app.state.forecast_service.scenario(db, business, body)
