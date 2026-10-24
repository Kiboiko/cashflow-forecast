"""GET /businesses/{bid}/forecast — прогноз, риск, рекомендации (§8); /forecast/trace — как он посчитан."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.db.session import DbSession
from app.schemas import ForecastResponse, ForecastTrace
from app.security import OwnedBusiness

router = APIRouter(tags=["forecast"])


@router.get("/businesses/{bid}/forecast", response_model=ForecastResponse)
def get_forecast(request: Request, business: OwnedBusiness, db: DbSession,
                 horizon: Annotated[int, Query(ge=7, le=90)] = 30) -> ForecastResponse:
    return request.app.state.forecast_service.forecast(db, business, horizon)


@router.get("/businesses/{bid}/forecast/trace", response_model=ForecastTrace)
def get_forecast_trace(request: Request, business: OwnedBusiness, db: DbSession,
                       horizon: Annotated[int, Query(ge=7, le=90)] = 30) -> ForecastTrace:
    """Пошаговый журнал расчёта: данные → качество → категории → потоки → сценарии → риск → советы."""
    return ForecastTrace(steps=request.app.state.forecast_service.trace(db, business, horizon))
