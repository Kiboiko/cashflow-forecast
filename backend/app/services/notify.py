"""Доставка новых предупреждений владельцу: сейчас — Telegram (P2). Остальные каналы добавляются здесь же."""
from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session

from app.db.models import Alert, Business, User
from app.integrations.telegram import telegram_text

SEND_SEVERITIES = {"warning", "critical"}          # «уточните» (info) в мессенджер не шлём: это шум
TEST_TEXT = "🔵 Кассовый радар: проверка связи. Сюда будут приходить важные предупреждения."


class NotifyError(ValueError):
    """Сообщение отправить нельзя: текст показываем пользователю как есть (HTTP 409)."""


class Messenger(Protocol):
    def send(self, chat_id: str, text: str) -> bool: ...


def send_test(db: Session, business: Business, messenger: Messenger | None) -> None:
    """Тестовое сообщение владельцу: единственный способ убедиться, что id чата указан верно. Данных в тексте нет."""
    if messenger is None:
        raise NotifyError("Telegram не подключён на сервере: администратор ещё не указал токен бота.")
    owner = db.get(User, business.owner_user_id)
    if owner is None or not owner.telegram_chat_id:
        raise NotifyError("Сначала укажите id чата и сохраните настройки.")
    if not messenger.send(owner.telegram_chat_id, TEST_TEXT):
        raise NotifyError("Telegram не принял сообщение. Проверьте id чата и напишите боту любое сообщение: "
                          "бот не может писать первым.")


def deliver(db: Session, business: Business, alerts: list[Alert], messenger: Messenger | None) -> int:
    """Шлёт новые предупреждения в Telegram, если подключён бот и у владельца указан чат. Возвращает число отправленных."""
    if messenger is None or not alerts:
        return 0
    owner = db.get(User, business.owner_user_id)
    if owner is None or not owner.telegram_chat_id:
        return 0
    sent = 0
    for alert in alerts:
        if alert.severity not in SEND_SEVERITIES:
            continue
        if messenger.send(owner.telegram_chat_id, telegram_text(alert.type, alert.severity, alert.title)):
            alert.sent_channels = [*alert.sent_channels, "telegram"]
            sent += 1
    db.commit()
    return sent
