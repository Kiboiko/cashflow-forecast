"""Регистрация: новый пользователь с пустым бизнесом, вход по паролю, защита от дублей и слабого пароля."""
from __future__ import annotations

from app.db.models import Business, User

URL = "/api/v1/auth/register"
BODY = {"email": "New.Owner@Example.com ", "password": "long-enough-1", "display_name": "Пётр",
        "business_name": "ООО «Ромашка»", "legal_form": "ООО", "inn": "7707083893"}


def test_register_creates_user_and_empty_business(client, db):
    response = client.post(URL, json=BODY)
    assert response.status_code == 201
    token = response.json()["access_token"]
    assert response.json()["user"]["email"] == "new.owner@example.com"

    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
    [business] = me["businesses"]
    assert business["name"] == "ООО «Ромашка»" and business["id"].startswith("U")

    user = db.query(User).filter_by(email="new.owner@example.com").one()
    assert not user.is_demo and user.password_hash != BODY["password"]          # хэш, не пароль
    assert db.get(Business, business["id"]).owner_user_id == user.id


def test_new_account_can_login_with_password(client):
    client.post(URL, json=BODY)
    login = client.post("/api/v1/auth/login", json={"email": "new.owner@example.com", "password": BODY["password"]})
    assert login.status_code == 200
    assert client.post("/api/v1/auth/login",
                       json={"email": "new.owner@example.com", "password": "wrong-password"}).status_code == 401


def test_duplicate_email_is_409_even_in_other_case(client, irina):
    assert client.post(URL, json=BODY).status_code == 201
    again = client.post(URL, json={**BODY, "email": "NEW.OWNER@example.com"})
    assert again.status_code == 409
    assert client.post(URL, json={**BODY, "email": "irina@example.com"}).status_code == 409   # демо-аккаунт занят


def test_weak_or_malformed_input_is_422(client):
    assert client.post(URL, json={**BODY, "password": "short"}).status_code == 422
    assert client.post(URL, json={**BODY, "email": "not-an-email"}).status_code == 422
    assert client.post(URL, json={**BODY, "inn": "123"}).status_code == 422
    assert client.post(URL, json={**BODY, "legal_form": "АО"}).status_code == 422
    assert client.post(URL, json={**BODY, "business_name": ""}).status_code == 422


def test_inn_and_legal_form_are_optional(client):
    response = client.post(URL, json={"email": "a@b.ru", "password": "long-enough-1", "display_name": "А",
                                      "business_name": "ИП Иванов"})
    assert response.status_code == 201


def test_new_business_has_no_data_until_statement_is_uploaded(client):
    token = client.post(URL, json=BODY).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    bid = client.get("/api/v1/me", headers=headers).json()["businesses"][0]["id"]
    forecast = client.get(f"/api/v1/businesses/{bid}/forecast", headers=headers)
    assert forecast.status_code in (200, 409)              # fixture-режим отдаёт пример, engine — 409 «загрузите выписку»


def test_registered_user_cannot_see_other_business(client, irina):
    token = client.post(URL, json=BODY).json()["access_token"]
    assert client.get("/api/v1/businesses/C064", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_register_is_rate_limited(client):
    codes = [client.post(URL, json={**BODY, "email": f"user{i}@example.com"}).status_code for i in range(7)]
    assert codes[:5] == [201] * 5
    assert codes[5:] == [429, 429]


def test_registered_user_is_not_a_demo_persona(client):
    client.post(URL, json=BODY)
    assert client.get("/api/v1/auth/demo-personas").json() == []


def test_registered_user_uploads_statement_and_gets_forecast_on_real_engine():
    """Путь для жюри: регистрация → «загрузите выписку» (409) → загрузка CSV → прогноз на настоящем движке."""
    from fastapi.testclient import TestClient

    from app.db.session import init_db, make_engine, make_session_factory
    from app.main import create_app
    from tests.api.conftest import api_settings
    from tests.api.synthetic import synthetic_statement, to_csv_bytes

    settings = api_settings("engine")
    engine = make_engine(settings.database_url)
    init_db(engine)
    app = create_app(settings, engine)
    app.state.session_factory = make_session_factory(engine)
    with TestClient(app) as client:
        token = client.post(URL, json=BODY).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        bid = client.get("/api/v1/me", headers=headers).json()["businesses"][0]["id"]

        empty = client.get(f"/api/v1/businesses/{bid}/forecast", headers=headers)
        assert empty.status_code == 409 and empty.json()["code"] == "no_data"

        upload = client.post(f"/api/v1/businesses/{bid}/imports", headers=headers,
                             files={"file": ("statement.csv", to_csv_bytes(synthetic_statement()), "text/csv")})
        assert upload.status_code == 200, upload.text

        forecast = client.get(f"/api/v1/businesses/{bid}/forecast", headers=headers)
        assert forecast.status_code == 200 and forecast.json()["risk"]["level"] in ("green", "yellow", "red")
