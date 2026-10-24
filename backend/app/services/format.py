"""Форматирование текстов для сообщений сервиса (даты по-русски)."""
from __future__ import annotations

from datetime import date

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]


def dmy_long(day: date) -> str:
    """31 июля 2026"""
    return f"{day.day} {MONTHS_GEN[day.month - 1]} {day.year}"
