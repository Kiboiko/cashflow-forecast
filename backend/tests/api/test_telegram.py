"""Telegram: обезличенный текст, клиент (успех, отказ, сеть), доставка предупреждений и проверка связи с ботом."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.db.models import Alert, Business, User
from app.integrations.telegram import TelegramClient, telegram_text
from app.services import notify
from tests.api.conftest import auth_headers, build_env, make_user

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

TOKEN = "123456:SECRET-BOT-TOKEN"


class FakeMessenger:
    """Вместо Telegram: запоминает, что отправили, и отвечает успехом или отказом."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[str, str]] = []

    def send(self, chat_id: str, text: str) -> bool:
        self.sent.append((chat_id, text))
        return self.ok


def telegram_with(handler) -> TelegramClient:
    return TelegramClient(TOKEN, transport=httpx.MockTransport(handler))


def add_alert(db, bid: str, severity: str, kind: str, key: str, title: str = "Может не хватить денег около 21 августа") -> Alert:
    alert = Alert(business_id=bid, type=kind, severity=severity, key=key, title=title, body="Подробности", payload={})
    db.add(alert)
    db.commit()
    return alert


# ---------- текст ----------
def test_text_has_no_money_names_or_inn():
    titles = {
        "mandatory_at_risk": "Налог УСН 25 августа: может не хватить",
        "missed_inflow": "Не пришла оплата 300 000 ₽ от ООО «Секрет»",
        "overdue_invoice": "Не пришла оплата 150 000 ₽ от ИП Петров",
        "data_gap": "Нет свежих данных по счёту",
        "needs_review": "Нераспознанные операции (7) — уточните",
        "something_new": "Платёж 12 345 ₽ контрагенту «Секрет», ИНН 7701234567",
    }
    for kind, title in titles.items():
        text = telegram_text(kind, "warning", title)
        assert "Кассовый радар" in text and "Подробности — в сервисе" in text
        for secret in ("₽", "ООО", "ИП", "Секрет", "Петров", "7701234567", "300", "150", "12 345", "УСН"):
            assert secret not in text, (kind, secret)


def test_gap_risk_text_keeps_only_the_date():
    text = telegram_text("gap_risk", "critical", "Может не хватить денег около 21 августа")
    assert text.startswith("🔴") and "около 21 августа" in text


# ---------- клиент ----------
def test_client_posts_to_the_bot_api_and_logs_no_secrets(caplog):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with caplog.at_level(logging.DEBUG, logger="kassa.telegram"):
        assert telegram_with(handler).send("777", "Тайный текст") is True
    request = seen[0]
    assert request.url.host == "api.telegram.org" and request.url.path == f"/bot{TOKEN}/sendMessage"
    assert json.loads(request.content) == {"chat_id": "777", "text": "Тайный текст", "disable_web_page_preview": True}
    logged = " ".join(f"{r.getMessage()} {getattr(r, 'fields', '')}" for r in caplog.records)
    assert caplog.records and not any(secret in logged for secret in ("777", "Тайный", TOKEN, "SECRET"))


def test_client_does_not_raise_on_refusal_or_network_errors(caplog):
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"ok": False, "description": "bot was blocked by the user"})

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети", request=request)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("долго", request=request)

    with caplog.at_level(logging.DEBUG, logger="kassa.telegram"):
        for handler in (refused, offline, slow):
            assert telegram_with(handler).send("777", "текст") is False
    logged = " ".join(f"{r.getMessage()} {getattr(r, 'fields', '')}" for r in caplog.records)
    assert len(caplog.records) == 3 and TOKEN not in logged and "777" not in logged and "blocked" not in logged


# ---------- доставка ----------
def test_deliver_sends_only_important_alerts_to_the_owner(fresh_env):
    env = fresh_env
    business = env.db.get(Business, env.bid)
    owner = env.db.get(User, business.owner_user_id)
    alerts = [
        add_alert(env.db, env.bid, "critical", "gap_risk", "a"),
        add_alert(env.db, env.bid, "info", "needs_review", "b", "Нераспознанные операции (7) — уточните"),
        add_alert(env.db, env.bid, "warning", "mandatory_at_risk", "c", "Налог УСН 25 авг: может не хватить"),
    ]
    messenger = FakeMessenger()

    assert notify.deliver(env.db, business, alerts, messenger) == 0 and messenger.sent == []     # чат не указан
    owner.telegram_chat_id = "555"
    env.db.commit()
    assert notify.deliver(env.db, business, alerts, None) == 0                                   # бот не подключён
    assert notify.deliver(env.db, business, [], messenger) == 0

    assert notify.deliver(env.db, business, alerts, messenger) == 2
    assert [chat for chat, _ in messenger.sent] == ["555", "555"]
    assert all("₽" not in text and "УСН" not in text for _, text in messenger.sent)
    env.db.expire_all()
    assert [a.sent_channels for a in alerts] == [["telegram"], [], ["telegram"]]

    failing = FakeMessenger(ok=False)
    later = add_alert(env.db, env.bid, "critical", "gap_risk", "d")
    assert notify.deliver(env.db, business, [later], failing) == 0 and later.sent_channels == []  # отказ Telegram — не отправлено


def test_time_machine_alerts_go_to_telegram_once():
    for env in build_env(crunch=True, data_as_of="2026-07-31"):
        messenger = FakeMessenger()
        env.app.state.forecast_service.messenger = messenger
        saved = env.client.patch(env.url("/settings"), headers=env.headers, json={"telegram_chat_id": "4242"})
        assert saved.status_code == 200
        for _ in range(2):
            step = env.client.post(f"/api/v1/demo/businesses/{env.bid}/replay/advance", headers=env.headers, json={"days": 7})
            assert step.status_code == 200, step.text

        env.db.expire_all()
        stored = env.db.query(Alert).filter(Alert.business_id == env.bid).all()
        delivered = [a for a in stored if "telegram" in a.sent_channels]
        assert delivered and len(messenger.sent) == len(delivered)                  # каждое предупреждение — одно сообщение
        assert {a.severity for a in delivered} <= {"warning", "critical"}
        assert all(a.severity != "info" for a in delivered) and {chat for chat, _ in messenger.sent} == {"4242"}
        assert not any("₽" in text for _, text in messenger.sent)


# ---------- проверка связи ----------
def test_telegram_test_endpoint(fresh_env):
    env = fresh_env
    url = env.url("/telegram/test")
    service = env.app.state.forecast_service

    assert service.messenger is None                                               # в тестах токена нет
    no_bot = env.client.post(url, headers=env.headers)
    assert no_bot.status_code == 409 and "токен" in no_bot.json()["detail"]

    messenger = FakeMessenger()
    service.messenger = messenger
    no_chat = env.client.post(url, headers=env.headers)
    assert no_chat.status_code == 409 and "id чата" in no_chat.json()["detail"] and messenger.sent == []

    assert env.client.patch(env.url("/settings"), headers=env.headers, json={"telegram_chat_id": "4242"}).status_code == 200
    sent = env.client.post(url, headers=env.headers)
    assert sent.status_code == 200 and sent.json() == {"sent": True}
    assert messenger.sent[0][0] == "4242" and "проверка связи" in messenger.sent[0][1]

    messenger.ok = False
    refused = env.client.post(url, headers=env.headers)
    assert refused.status_code == 409 and "Telegram не принял" in refused.json()["detail"]

    make_user(env.db, "oleg@example.com", "Олег")
    assert env.client.post(url, headers=auth_headers(env.client, "oleg@example.com")).status_code == 404
    assert env.client.post(url).status_code == 401
