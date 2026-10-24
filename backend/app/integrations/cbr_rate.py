"""Ключевая ставка Банка России (P2): ориентир для разговоров про кредит. Запрос не содержит данных пользователя.

Берём таблицу со страницы ЦБ, кэшируем на несколько часов. Нет сети или страница изменилась — отдаём запасное значение
из настроек и помечаем это в ответе, чтобы в интерфейсе не выдавать его за свежие данные ЦБ.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import httpx

log = logging.getLogger("kassa.cbr")
URL = "https://www.cbr.ru/hd_base/KeyRate/"
TIMEOUT_SECONDS = 4.0
CACHE_SECONDS = 6 * 3600
RETRY_SECONDS = 60                                   # после неудачи не дёргаем ЦБ на каждый запрос (без сети это 4 с ожидания)
LOOKBACK_DAYS = 45                                   # ставка меняется раз в 1–2 месяца, но выходные и праздники без записей
_ROW = re.compile(r"<td>\s*(\d{2})\.(\d{2})\.(\d{4})\s*</td>\s*<td>\s*(\d{1,2}(?:,\d{1,2})?)\s*</td>")


@dataclass(frozen=True)
class KeyRate:
    rate: float                                      # доля: 0.14 = 14% годовых
    as_of: date | None                               # дата записи на сайте ЦБ; у запасного значения её нет
    source: Literal["cbr", "fallback"]


def parse_key_rate(html: str) -> tuple[date, float] | None:
    """Самая свежая запись «дата — ставка» из таблицы ЦБ или None, если таблицы в ответе нет."""
    rows = []
    for day, month, year, value in _ROW.findall(html):
        try:
            rows.append((date(int(year), int(month), int(day)), float(value.replace(",", ".")) / 100))
        except ValueError:
            continue
    return max(rows) if rows else None


class KeyRateClient:
    def __init__(self, fallback: float, transport: httpx.BaseTransport | None = None, ttl: float = CACHE_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._fallback = fallback
        self._transport = transport
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: KeyRate | None = None
        self._fetched_at = float("-inf")
        self._next_try = float("-inf")

    def get(self, today: date | None = None) -> KeyRate:
        with self._lock:
            now = self._clock()
            if self._cached is not None and now - self._fetched_at < self._ttl:
                return self._cached
            if now >= self._next_try:
                fresh = self._fetch(today or date.today())
                if fresh is not None:
                    self._cached, self._fetched_at = fresh, now
                    return fresh
                self._next_try = now + RETRY_SECONDS
            # не получилось: показываем прежнее значение, а если его не было — запасное
            return self._cached or KeyRate(self._fallback, None, "fallback")

    def _fetch(self, today: date) -> KeyRate | None:
        params = {"UniDbQuery.Posted": "True", "UniDbQuery.From": (today - timedelta(days=LOOKBACK_DAYS)).strftime("%d.%m.%Y"),
                  "UniDbQuery.To": today.strftime("%d.%m.%Y")}
        try:
            with httpx.Client(timeout=TIMEOUT_SECONDS, transport=self._transport, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0 (compatible; kassa-radar)"}) as client:
                response = client.get(URL, params=params)
        except httpx.HTTPError:
            log.warning("cbr key rate unavailable", extra={"fields": {"reason": "network"}})
            return None
        parsed = parse_key_rate(response.text) if response.status_code == 200 else None
        if parsed is None:
            log.warning("cbr key rate unreadable", extra={"fields": {"status": response.status_code}})
            return None
        return KeyRate(rate=parsed[1], as_of=parsed[0], source="cbr")
