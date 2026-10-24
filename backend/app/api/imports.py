"""POST /businesses/{bid}/imports — загрузка выписки: CSV датасета или файл 1С «Клиент-Банк» (§8, §13)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, UploadFile, status

from app.db.session import DbSession
from app.integrations import client_bank_1c
from app.schemas import ImportResult
from app.security import OwnedBusiness
from app.services import import_service

router = APIRouter(tags=["imports"])


@router.post("/businesses/{bid}/imports", response_model=ImportResult)
def upload_statement(file: UploadFile, request: Request, business: OwnedBusiness, db: DbSession) -> ImportResult:
    content = file.file.read(import_service.MAX_BYTES + 1)
    if len(content) > import_service.MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Файл слишком большой (больше 25 МБ).")
    source = import_service.format_of(content)
    try:
        frame = client_bank_1c.parse_1c(content) if source == "1c" else import_service.parse_dataset_csv(content)
    except (import_service.StatementError, client_bank_1c.OneCError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return import_service.import_statement(db, business, request.app.state.forecast_service, frame, file.filename, source=source)
