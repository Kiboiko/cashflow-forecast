"""План предпринимателя: события, правка и отмена, CSV счетов и договоров (§8, §13)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app.db.models import Business, ExpectedEvent
from app.db.session import DbSession
from app.schemas import EventCreate, EventOut, EventPatch, EventsImportResult
from app.security import OwnedBusiness
from app.services import event_service as service

router = APIRouter(tags=["events"])
MAX_CSV_BYTES = 5 * 1024 * 1024


def _event(db: DbSession, business: Business, eid: int) -> ExpectedEvent:
    event = db.scalar(select(ExpectedEvent).where(ExpectedEvent.id == eid, ExpectedEvent.business_id == business.id))
    if event is None or event.status == "cancelled":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Событие не найдено")
    return event


def _invalid(exc: service.EventError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


@router.get("/businesses/{bid}/events", response_model=list[EventOut])
def list_events(business: OwnedBusiness, db: DbSession, include_cancelled: bool = False) -> list[EventOut]:
    return service.list_events(db, business, include_cancelled)


@router.post("/businesses/{bid}/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(body: EventCreate, business: OwnedBusiness, db: DbSession) -> EventOut:
    try:
        return service.to_out(service.create_manual(db, business, body))
    except service.EventError as exc:
        raise _invalid(exc) from exc


@router.post("/businesses/{bid}/events/import", response_model=EventsImportResult)
def import_events(file: UploadFile, business: OwnedBusiness, db: DbSession) -> EventsImportResult:
    content = file.file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Файл слишком большой (больше 5 МБ).")
    try:
        return service.import_events_csv(db, business, content)
    except service.EventError as exc:
        raise _invalid(exc) from exc


@router.patch("/businesses/{bid}/events/{eid}", response_model=EventOut)
def patch_event(eid: int, body: EventPatch, business: OwnedBusiness, db: DbSession) -> EventOut:
    event = _event(db, business, eid)
    try:
        return service.to_out(service.update_event(db, business, event, body))
    except service.EventError as exc:
        raise _invalid(exc) from exc


@router.delete("/businesses/{bid}/events/{eid}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_event(eid: int, business: OwnedBusiness, db: DbSession) -> Response:
    service.cancel_event(db, business, _event(db, business, eid))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
