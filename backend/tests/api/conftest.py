"""Фикстуры API-тестов: приложение с SQLite в памяти, пользователи и бизнесы. Корневой conftest не трогаем."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.db.models import Business, User
from app.db.session import init_db, make_engine, make_session_factory
from app.main import create_app
from app.security import hash_password

PASSWORD = "correct-horse-1"


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings(database_url="sqlite://", forecast_mode="fixture", jwt_secret="test-secret-test-secret-test-secret-0123",
                       demo_mode=True, login_attempts_per_minute=5)


@pytest.fixture
def app(settings):
    engine = make_engine(settings.database_url)
    init_db(engine)
    application = create_app(settings, engine)
    application.state.session_factory = make_session_factory(engine)
    return application


@pytest.fixture
def db(app) -> Iterator[Session]:
    session = app.state.session_factory()
    yield session
    session.close()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def make_user(db: Session, email: str, name: str = "Тест", demo: bool = False) -> User:
    user = User(email=email, password_hash=hash_password(PASSWORD), display_name=name, is_demo=demo)
    db.add(user)
    db.commit()
    return user


def make_business(db: Session, owner: User, bid: str, name: str = "ООО Ромашка") -> Business:
    business = Business(id=bid, owner_user_id=owner.id, name=name, legal_form="ООО", industry="Торговля")
    db.add(business)
    db.commit()
    return business


@pytest.fixture
def irina(db) -> User:
    user = make_user(db, "irina@example.com", "Ирина", demo=True)
    make_business(db, user, "C064", "Образовательный центр")
    return user


@pytest.fixture
def oleg(db) -> User:
    user = make_user(db, "oleg@example.com", "Олег", demo=True)
    make_business(db, user, "C109", "Грузоперевозки")
    return user


def auth_headers(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def irina_headers(client, irina) -> dict[str, str]:
    return auth_headers(client, "irina@example.com")
