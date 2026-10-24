"""Справочные данные: ключевая ставка ЦБ и проверка контрагентов по ИНН (оба — внешние источники, P2)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.schemas import CounterpartyCheck, CounterpartyChecks, KeyRateOut
from app.security import CurrentUser, OwnedBusiness

router = APIRouter(tags=["reference"])
MAX_INNS = 200


@router.get("/reference/key-rate", response_model=KeyRateOut)
def key_rate(request: Request, _: CurrentUser) -> KeyRateOut:
    value = request.app.state.key_rate.get()
    return KeyRateOut(rate=value.rate, as_of=value.as_of, source=value.source)


@router.get("/businesses/{bid}/counterparty-checks", response_model=CounterpartyChecks)
def counterparty_checks(request: Request, business: OwnedBusiness,
                        inn: Annotated[list[str] | None, Query(max_length=MAX_INNS)] = None) -> CounterpartyChecks:
    """Статусы контрагентов по ИНН: в ответе только те, у кого статус не «действует». Сейчас — демо-заглушка."""
    flagged = request.app.state.registry.flagged(inn or [])
    return CounterpartyChecks(source="demo", flagged=[
        CounterpartyCheck(inn=c.inn, status=c.status, title=c.title, note=c.note) for c in flagged])
