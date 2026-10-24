"""Роутеры REST API (префикс /api/v1)."""
from fastapi import APIRouter

from app.api import auth, businesses, forecast, health, me

api_router = APIRouter()
for module in (health, auth, me, businesses, forecast):
    api_router.include_router(module.router)
