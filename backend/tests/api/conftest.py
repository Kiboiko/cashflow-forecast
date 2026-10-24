"""Фикстуры API-тестов: приложение с SQLite в памяти, пользователи и бизнесы. Корневой conftest не трогаем."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.db.models import Business, User
from app.db.session import init_db, make_engine, make_session_factory
from app.main import create_app
from app.security import hash_password
from tests.api.synthetic import synthetic_statement, to_csv_bytes

PASSWORD = "correct-horse-1"
SECRET = "test-secret-test-secret-test-secret-0123"


def api_settings(mode: str = "fixture") -> AppSettings:
    return AppSettings(database_url="sqlite://", forecast_mode=mode, jwt_secret=SECRET, demo_mode=True,
                       login_attempts_per_minute=5, warm_up=False)


@pytest.fixture
def settings() -> AppSettings:
    return api_settings()


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


# ---------- настоящий движок на синтетической выписке ----------
@dataclass
class Env:
    """Приложение в режиме `engine` с одним бизнесом, в который загружена синтетическая выписка."""
    app: object
    client: TestClient
    headers: dict[str, str]
    bid: str
    db: Session
    statement_csv: bytes
    import_result: dict

    def url(self, path: str) -> str:
        return f"/api/v1/businesses/{self.bid}{path}"


def build_env(crunch: bool = False, data_as_of: str | None = None) -> Iterator[Env]:
    settings = api_settings("engine")
    engine = make_engine(settings.database_url)
    init_db(engine)
    app = create_app(settings, engine)
    app.state.session_factory = make_session_factory(engine)
    db = app.state.session_factory()
    user = make_user(db, "irina@example.com", "Ирина", demo=True)
    make_business(db, user, "C064", "Образовательный центр")
    csv = to_csv_bytes(synthetic_statement(crunch=crunch))
    with TestClient(app) as client:
        headers = auth_headers(client, "irina@example.com")
        response = client.post("/api/v1/businesses/C064/imports", headers=headers,
                               files={"file": ("statement.csv", csv, "text/csv")})
        assert response.status_code == 200, response.text
        env = Env(app, client, headers, "C064", db, csv, response.json())
        if data_as_of:                                    # как у демо-персон: выписка целиком, видна до даты (штатный сброс)
            reset = client.post("/api/v1/demo/businesses/C064/replay/reset", headers=headers, json={"as_of": data_as_of})
            assert reset.status_code == 200, reset.text
            db.expire_all()
        yield env
    db.close()


@pytest.fixture(scope="module")
def env() -> Iterator[Env]:
    """Один импорт на модуль — для тестов, которые только читают."""
    yield from build_env()


@pytest.fixture
def fresh_env() -> Iterator[Env]:
    """Свой импорт на тест — для тестов, которые меняют данные."""
    yield from build_env()
