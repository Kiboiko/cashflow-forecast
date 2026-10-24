"""Подключение к БД. PostgreSQL в Docker; в тестах — SQLite в памяти (общее соединение, поэтому `StaticPool`)."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base

_MEMORY_URLS = {"sqlite://", "sqlite:///:memory:"}


def make_engine(url: str) -> Engine:
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True)
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if url in _MEMORY_URLS:
        kwargs["poolclass"] = StaticPool
    return create_engine(url, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    from app.db import models  # noqa: F401  (регистрирует таблицы в Base.metadata)

    Base.metadata.create_all(engine)


def get_db(request: Request) -> Iterator[Session]:
    """Сессия на запрос. Фабрика лежит в `app.state` — так тесты подставляют свою БД без глобальных переменных."""
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
