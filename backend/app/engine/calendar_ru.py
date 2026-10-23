"""Производственный календарь РФ и правила сдвига дат (§6.5, Приложение B)."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Literal

# Нерабочие будни (праздники и переносы).
# Октябрь 2024 – сентябрь 2026 сверены с датасетом; IV кв. 2026 — по официальному календарю.
# Календарь 2027 подгрузить из официального источника, когда понадобится.
_HOLIDAYS = [
    # 2024
    "2024-11-04", "2024-12-30", "2024-12-31",
    # 2025
    "2025-01-01", "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08",
    "2025-05-01", "2025-05-02", "2025-05-08", "2025-05-09", "2025-06-12", "2025-06-13",
    "2025-11-03", "2025-11-04", "2025-12-31",
    # 2026
    "2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
    "2026-02-23", "2026-03-09", "2026-05-01", "2026-05-11", "2026-06-12",
    "2026-11-04", "2026-12-31",
]
_WORKING_SATURDAYS = ["2024-11-02", "2024-12-28", "2025-11-01"]

HOLIDAYS = frozenset(date.fromisoformat(s) for s in _HOLIDAYS)
WORKING_SATURDAYS = frozenset(date.fromisoformat(s) for s in _WORKING_SATURDAYS)

ShiftRule = Literal["next", "prev"]


def is_workday(d: date) -> bool:
    """Будни минус праздники плюс рабочие субботы."""
    if d in WORKING_SATURDAYS:
        return True
    return d.weekday() < 5 and d not in HOLIDAYS


def shift(d: date, rule: ShiftRule) -> date:
    """`next` — вперёд до рабочего дня, `prev` — назад. Рабочий день возвращается как есть."""
    step = timedelta(days=1 if rule == "next" else -1)
    while not is_workday(d):
        d += step
    return d


def nominal_date(year: int, month: int, day: int) -> date:
    """Номинальная дата платежа: если в месяце нет такого числа — последний день месяца."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def workdays_between(start: date, end: date) -> int:
    """Число рабочих дней в интервале (start, end]; 0, если end <= start."""
    n = 0
    d = start + timedelta(days=1)
    while d <= end:
        n += is_workday(d)
        d += timedelta(days=1)
    return n
