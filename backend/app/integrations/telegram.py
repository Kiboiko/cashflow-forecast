"""Telegram: доставка предупреждений (только отправка, Bot API). Токен бота — в `.env` (`TELEGRAM_BOT_TOKEN`).

Приватность (§10): в Telegram уходит ОБЕЗЛИЧЕННЫЙ текст — без сумм, имён контрагентов и ИНН; подробности — в сервисе.
В журнал пишем только результат отправки (успех/код), но не текст и не id чата.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("kassa.telegram")
API = "https://api.telegram.org"
TIMEOUT_SECONDS = 5.0

_ICONS = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
# Что можно сказать в чужом мессенджере: тип предупреждения → безопасная формулировка (без денег и имён)
_SAFE_TEXT = {
    "mandatory_at_risk": "Может не хватить денег на обязательный платёж.",
    "missed_inflow": "Не пришла ожидаемая оплата.",
    "overdue_invoice": "Не пришла ожидаемая оплата.",
    "data_gap": "Нет свежих данных по счёту — прогноз может быть неточным.",
    "needs_review": "Есть нераспознанные операции — уточните категории.",
}


def telegram_text(alert_type: str, severity: str, title: str) -> str:
    """Текст сообщения. `gap_risk` содержит только дату («Может не хватить денег около 21 августа»); остальные типы
    заменяются общей фразой: их заголовки несут суммы и названия контрагентов."""
    body = title if alert_type == "gap_risk" else _SAFE_TEXT.get(alert_type, "Есть новое предупреждение.")
    return f"{_ICONS.get(severity, '🔵')} Кассовый радар: {body}\nПодробности — в сервисе."


class TelegramClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._token = token
        self._transport = transport

    def send(self, chat_id: str, text: str) -> bool:
        """Отправляет сообщение. Любая сетевая ошибка — не повод ронять сервис: возвращаем False."""
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, transport=self._transport) as client:
                response = client.post(f"{API}/bot{self._token}/sendMessage",
                                       json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
        except httpx.HTTPError:
            log.warning("telegram send failed", extra={"fields": {"reason": "network"}})
            return False
        ok = response.status_code == 200
        log.info("telegram sent" if ok else "telegram rejected", extra={"fields": {"status": response.status_code}})
        return ok
