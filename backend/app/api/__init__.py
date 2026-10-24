"""Роутеры REST API (префикс /api/v1)."""
from fastapi import APIRouter

from app.api import (
    auth,
    businesses,
    events,
    forecast,
    health,
    imports,
    me,
    plan,
    planfact,
    reference,
    transactions,
)

api_router = APIRouter()
for module in (health, auth, me, businesses, forecast, transactions, imports, events, plan, planfact, reference):
    api_router.include_router(module.router)
