"""GET /businesses/{bid}, PATCH /businesses/{bid}/settings."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.db.session import DbSession
from app.schemas import BusinessOut, SettingsPatch
from app.security import OwnedBusiness
from app.services.business_service import apply_settings_patch, business_out

router = APIRouter(tags=["businesses"])


@router.get("/businesses/{bid}", response_model=BusinessOut)
def get_business(request: Request, business: OwnedBusiness, db: DbSession) -> BusinessOut:
    quality = request.app.state.forecast_service.data_quality(db, business)
    return business_out(db, business, quality)


@router.patch("/businesses/{bid}/settings", response_model=BusinessOut)
def patch_settings(patch: SettingsPatch, request: Request, business: OwnedBusiness, db: DbSession) -> BusinessOut:
    apply_settings_patch(db, business, patch)
    quality = request.app.state.forecast_service.data_quality(db, business)
    return business_out(db, business, quality)
