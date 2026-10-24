"""GET /businesses/{bid}/forecast — прогноз, риск, рекомендации (§8)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.db.session import DbSession
from app.schemas import ForecastResponse
from app.security import OwnedBusiness

router = APIRouter(tags=["forecast"])


@router.get("/businesses/{bid}/forecast", response_model=ForecastResponse)
def get_forecast(request: Request, business: OwnedBusiness, db: DbSession,
                 horizon: Annotated[int, Query(ge=7, le=90)] = 30) -> ForecastResponse:
    return request.app.state.forecast_service.forecast(db, business, horizon)
