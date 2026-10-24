"""Вход, изоляция бизнесов (401/404), демо-персоны, настройки и заглушка прогноза (§10, §11)."""
from __future__ import annotations

import jwt

from app.schemas import ForecastResponse
from app.security import create_access_token
from tests.api.conftest import PASSWORD, auth_headers


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_login_ok_and_me(client, irina):
    response = client.post("/api/v1/auth/login", json={"email": "Irina@Example.com ", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer" and body["user"]["display_name"] == "Ирина"
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert [b["id"] for b in me.json()["businesses"]] == ["C064"]


def test_login_wrong_password_and_unknown_user(client, irina):
    assert client.post("/api/v1/auth/login", json={"email": "irina@example.com", "password": "nope"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "x@example.com", "password": PASSWORD}).status_code == 401


def test_login_rate_limit_is_five_per_minute(client, irina):
    codes = [client.post("/api/v1/auth/login", json={"email": "irina@example.com", "password": "bad"}).status_code
             for _ in range(7)]
    assert codes[:5] == [401] * 5
    assert codes[5:] == [429, 429]


def test_no_token_is_401(client, irina):
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/businesses/C064").status_code == 401
    assert client.get("/api/v1/businesses/C064/forecast").status_code == 401


def test_garbage_and_expired_tokens_are_401(client, irina, settings):
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    expired = jwt.encode({"sub": str(irina.id), "exp": 1}, settings.jwt_secret, algorithm="HS256")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    forged = jwt.encode({"sub": str(irina.id), "exp": 9999999999}, "other-secret-other-secret-other-secret", algorithm="HS256")
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_token_of_deleted_user_is_401(client, db, irina, settings):
    token = create_access_token(irina.id + 100, settings)
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_foreign_business_is_404_on_every_business_route(client, irina, oleg):
    headers = auth_headers(client, "oleg@example.com")          # Олег идёт в бизнес Ирины
    assert client.get("/api/v1/businesses/C064", headers=headers).status_code == 404
    assert client.get("/api/v1/businesses/C064/forecast", headers=headers).status_code == 404
    assert client.patch("/api/v1/businesses/C064/settings", json={"threshold": 1}, headers=headers).status_code == 404
    assert client.get("/api/v1/businesses/C064", headers=auth_headers(client, "irina@example.com")).status_code == 200


def test_unknown_business_is_404_same_as_foreign(client, irina_headers):
    assert client.get("/api/v1/businesses/NOPE", headers=irina_headers).status_code == 404


def test_forecast_stub_returns_valid_contract(client, irina_headers):
    response = client.get("/api/v1/businesses/C064/forecast?horizon=30", headers=irina_headers)
    assert response.status_code == 200
    forecast = ForecastResponse.model_validate(response.json())
    assert forecast.business_id == "C064" and len(forecast.daily) == 30
    assert forecast.risk.level in {"green", "yellow", "red"}


def test_forecast_horizon_is_validated(client, irina_headers):
    assert client.get("/api/v1/businesses/C064/forecast?horizon=3", headers=irina_headers).status_code == 422
    assert client.get("/api/v1/businesses/C064/forecast?horizon=500", headers=irina_headers).status_code == 422


def test_demo_personas_and_one_tap_login(client, irina, oleg, db):
    personas = client.get("/api/v1/auth/demo-personas").json()
    assert [p["email"] for p in personas] == ["irina@example.com", "oleg@example.com"]
    assert personas[0]["business_id"] == "C064" and "(ООО)" in personas[1]["title"]
    token = client.post("/api/v1/auth/demo-login", json={"email": "irina@example.com"})
    assert token.status_code == 200
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token.json()['access_token']}"}).status_code == 200


def test_demo_login_refuses_regular_users_and_disabled_demo(client, app, db):
    from tests.api.conftest import make_user
    make_user(db, "real@example.com", "Настоящий", demo=False)
    assert client.post("/api/v1/auth/demo-login", json={"email": "real@example.com"}).status_code == 404
    app.state.settings.demo_mode = False
    assert client.get("/api/v1/auth/demo-personas").status_code == 404
    assert client.post("/api/v1/auth/demo-login", json={"email": "real@example.com"}).status_code == 404


def test_settings_patch_merges_and_validates(client, irina_headers):
    url = "/api/v1/businesses/C064/settings"
    ok = client.patch(url, json={"threshold": 50000, "other_accounts": [{"bank": "Т-Банк", "balance": 120000}],
                                 "include_other_accounts": True, "telegram_chat_id": "12345"}, headers=irina_headers)
    assert ok.status_code == 200
    body = ok.json()
    assert body["settings"]["threshold"] == 50000 and body["settings"]["horizon_days"] == 30
    assert body["settings"]["other_accounts"] == [{"bank": "Т-Банк", "balance": 120000}]
    assert body["telegram_chat_id"] == "12345"
    assert client.patch(url, json={"horizon_days": 45}, headers=irina_headers).status_code == 422
    assert client.patch(url, json={"threshold": -1}, headers=irina_headers).status_code == 422
    again = client.patch(url, json={"horizon_days": 60}, headers=irina_headers).json()
    assert again["settings"]["threshold"] == 50000 and again["settings"]["horizon_days"] == 60


def test_business_settings_match_engine_settings():
    """Поля настроек сервиса и `engine.types.Settings` не должны разойтись (лишний ключ роняет движок)."""
    import dataclasses

    from app.engine.types import Settings
    from app.schemas import BusinessSettings
    assert set(BusinessSettings.model_fields) == {f.name for f in dataclasses.fields(Settings)}
