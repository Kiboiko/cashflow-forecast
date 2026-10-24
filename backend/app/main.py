"""Фабрика FastAPI: CORS, журнал запросов без чувствительных данных, роутеры `/api/v1`.

Запуск: `uvicorn app.main:create_app --factory`. Фабрика (а не глобальный `app`) нужна тестам: у каждого теста
своя БД в памяти и свои настройки.
"""
from __future__ import annotations

import logging
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from app.api import api_router, health
from app.config import AppSettings, get_settings
from app.db.session import init_db, make_engine, make_session_factory
from app.logging_setup import setup_logging
from app.security import LoginRateLimiter
from app.services.forecast_service import ForecastService

log = logging.getLogger("kassa.http")
DB_WAIT_ATTEMPTS = 20


def _wait_for_db(engine: Engine) -> None:
    for attempt in range(DB_WAIT_ATTEMPTS):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if attempt == DB_WAIT_ATTEMPTS - 1:
                raise
            time.sleep(1)


def create_app(settings: AppSettings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging()
        _wait_for_db(engine)
        init_db(engine)
        yield

    app = FastAPI(title="Кассовый радар", version="1.0", lifespan=lifespan,
                  description="Прогноз остатка и кассовых разрывов для малого бизнеса")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.login_limiter = LoginRateLimiter(settings.login_attempts_per_minute)
    app.state.forecast_service = ForecastService(settings)

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        log.info("request", extra={"fields": {
            "method": request.method, "path": request.url.path, "status": response.status_code,
            "ms": round((time.perf_counter() - started) * 1000)}})
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # В журнал — тип ошибки и место, без текста: в нём бывают значения из выписки.
        frame = traceback.extract_tb(exc.__traceback__)[-1] if exc.__traceback__ else None
        log.error("unhandled", extra={"fields": {
            "method": request.method, "path": request.url.path, "error": type(exc).__name__,
            "where": f"{Path(frame.filename).name}:{frame.lineno}" if frame else ""}})
        return JSONResponse({"detail": "Что-то пошло не так. Попробуйте ещё раз."}, status_code=500)

    app.include_router(health.router)                          # /health — для docker compose
    app.include_router(api_router, prefix="/api/v1")
    return app
