"""Роутеры REST API (префикс /api/v1)."""
from fastapi import APIRouter

from app.api import auth, businesses, forecast, health, imports, me, transactions

api_router = APIRouter()
for module in (health, auth, me, businesses, forecast, transactions, imports):
    api_router.include_router(module.router)
