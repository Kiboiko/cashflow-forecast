"""Операции: список, исправление категории, справочник категорий, сводка «куда уходят деньги» (§8)."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.db.models import Transaction
from app.db.session import DbSession
from app.schemas import (
    CategoryInfo,
    CategoryPatch,
    CategoryPatchResult,
    CategorySummary,
    TransactionPage,
)
from app.security import CurrentUser, OwnedBusiness
from app.services import transaction_service as service

router = APIRouter(tags=["transactions"])


@router.get("/categories", response_model=list[CategoryInfo])
def list_categories(_: CurrentUser) -> list[CategoryInfo]:
    return service.categories()


@router.get("/businesses/{bid}/transactions", response_model=TransactionPage)
def list_transactions(
    business: OwnedBusiness, db: DbSession,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    category: str | None = None, q: str | None = None, review: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100, offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionPage:
    return service.list_transactions(db, business, date_from=date_from, date_to=date_to, category=category,
                                     query=q, review=review, limit=limit, offset=offset)


@router.patch("/businesses/{bid}/transactions/{tid}", response_model=CategoryPatchResult)
def patch_transaction(tid: int, body: CategoryPatch, request: Request, business: OwnedBusiness,
                      db: DbSession) -> CategoryPatchResult:
    tx = db.scalar(select(Transaction).where(Transaction.id == tid, *service.visible_conditions(business)))
    if tx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Операция не найдена")
    problem = service.category_mismatch(tx, body.category)
    if problem:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, problem)
    return service.apply_category(db, business, request.app.state.forecast_service, tx, body)


@router.get("/businesses/{bid}/categories/summary", response_model=CategorySummary)
def categories_summary(business: OwnedBusiness, db: DbSession,
                       months: Annotated[int, Query(ge=1, le=36)] = 12) -> CategorySummary:
    return service.category_summary(db, business, months)
