"""GET /businesses/{bid}, PATCH /businesses/{bid}/settings, POST /businesses/{bid}/telegram/test."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.db.session import DbSession
from app.schemas import BusinessOut, SettingsPatch, TelegramTestResult
from app.security import OwnedBusiness
from app.services import notify
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


@router.post("/businesses/{bid}/telegram/test", response_model=TelegramTestResult)
def telegram_test(request: Request, business: OwnedBusiness, db: DbSession) -> TelegramTestResult:
    """Присылает владельцу тестовое сообщение: так видно, что бот подключён, а id чата указан верно."""
    try:
        notify.send_test(db, business, request.app.state.forecast_service.messenger)
    except notify.NotifyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return TelegramTestResult(sent=True)
