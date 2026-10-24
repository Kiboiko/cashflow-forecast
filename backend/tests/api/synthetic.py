"""Синтетическая выписка в формате датасета для тестов API (настоящего датасета в репозитории нет).

Есть всё, что нужно движку: карты по дням (дата реестра в назначении), зарплата 10-го и 25-го с переносом на
предыдущий рабочий день, аренда 8-го, ЕНП 28-го, поставщики по счетам и три B2B-клиента с задержками оплаты.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.calendar_ru import is_workday, nominal_date, shift

BANK = ("0000000001", "ПАО «Банк»")
LANDLORD = ("0010000001", "ООО «Арендодатель»")
SUPPLIERS = [("0020000001", "ООО «Поставщик-1»"), ("0020000002", "ООО «Поставщик-2»"), ("0020000003", "ООО «Поставщик-3»")]
CLIENTS = [("0030000001", "ООО «Клиент-1»"), ("0030000002", "ООО «Клиент-2»"), ("0030000003", "ООО «Клиент-3»")]
OWNER = ("0040000001", "Иванов Иван Иванович")
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
WEEKDAY_FACTOR = [0.9, 0.9, 1.0, 1.05, 1.3, 1.7, 1.4]


def _dmy(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def synthetic_statement(company: str = "T001", start: date = date(2025, 1, 1), end: date = date(2026, 8, 31),
                        seed: int = 7, opening: float = 900_000.0, crunch: bool = False) -> pd.DataFrame:
    """Сырая выписка (колонки датасета), остаток по дням согласован с операциями.

    `crunch=True` — в конце июля 2026 крупная покупка оборудования: остаток падает, и в августе есть риск нехватки.
    """
    rng = np.random.default_rng(seed)
    days: dict[date, list[tuple[int, float, str, str | None, str]]] = {}

    def add(day: date, sign: int, amount: float, purpose: str, party: tuple[str, str] | None) -> None:
        if start <= day <= end:
            days.setdefault(day, []).append((sign, round(float(amount), 2), purpose, party[0] if party else None,
                                             party[1] if party else ""))

    d = start
    while d <= end:                                                       # карты: продажа → зачисление в следующий рабочий день
        season = 0.55 if (d.year == 2026 and d.month in (7, 8)) else 1.0
        sales = 24_500 * WEEKDAY_FACTOR[d.weekday()] * season * float(rng.lognormal(0, 0.1))
        add(shift(d + timedelta(days=1), "next"), 1, sales, f"Выручка по эквайрингу, дата реестра {_dmy(d)}", BANK)
        d += timedelta(days=1)

    for period in pd.period_range(start, end, freq="M"):
        year, month = period.year, period.month
        add(shift(nominal_date(year, month, 10), "prev"), -1, 205_000 * float(rng.normal(1, 0.02)),
            f"Заработная плата за {MONTHS[month - 2]} {year}", None)
        add(shift(nominal_date(year, month, 25), "prev"), -1, 168_000 * float(rng.normal(1, 0.02)),
            f"Заработная плата, аванс за {MONTHS[month - 1]} {year}", None)
        add(shift(nominal_date(year, month, 8), "next"), -1, 150_000, "Аренда помещения по договору № 5", LANDLORD)
        peak = 1.6 if month in (1, 4, 7, 10) else 1.0
        add(shift(nominal_date(year, month, 28), "next"), -1, 85_000 * peak * float(rng.normal(1, 0.05)),
            "Единый налоговый платеж", (BANK[0], "Казначейство России (ФНС России)"))
        add(shift(nominal_date(year, month, 20), "next"), -1, 150_000, "Перевод на личную карту владельца", OWNER)
        add(shift(nominal_date(year, month, 12), "next"), -1, 18_000 * float(rng.normal(1, 0.03)),
            "Оплата услуг связи и интернет", ("0050000001", "ПАО «Связь»"))

    n = 0
    d = start
    while d <= end:                                                       # поставщики: вторник и пятница, документ за 9 дней
        if d.weekday() in (1, 4) and is_workday(d):
            n += 1
            party = SUPPLIERS[n % len(SUPPLIERS)]
            doc = d - timedelta(days=9)
            add(d, -1, 47_000 * float(rng.lognormal(0, 0.2)), f"Оплата по счету № {n + 1000} от {_dmy(doc)} за товар", party)
        d += timedelta(days=1)

    number = 200
    for period in pd.period_range(start - timedelta(days=60), end, freq="M"):
        for client in CLIENTS:                                            # клиент: счёт раз в месяц, оплата через ~21±5 дней
            number += 1
            doc = nominal_date(period.year, period.month, int(rng.integers(2, 25)))
            pay = shift(doc + timedelta(days=int(np.clip(rng.normal(21, 5), 7, 45))), "next")
            add(pay, 1, 125_000 * float(rng.lognormal(0, 0.15)), f"Оплата по счету № {number} от {_dmy(doc)} за услуги", client)

    if crunch:                                                            # покупка оборудования «съедает» почти весь остаток
        crunch_day = date(2026, 7, 24)
        before = opening + sum(s * a for day, items in days.items() if day < crunch_day for s, a, *_ in items)
        add(crunch_day, -1, max(before - 300_000, 100_000), "Оплата оборудования по договору купли-продажи", SUPPLIERS[0])

    rows, balance, op_n = [], opening, 0
    for day in sorted(days):
        balance += sum(s * a for s, a, *_ in days[day])
        for sign, amount, purpose, inn, name in days[day]:
            op_n += 1
            rows.append({
                "company_id": company, "operation_id": f"{company}-{op_n:06d}", "operation_date": day.isoformat(),
                "amount": amount, "direction": "Поступление" if sign > 0 else "Списание", "counterparty_inn": inn or "",
                "counterparty_name": name, "payment_purpose": purpose, "balance_eod": round(balance, 2),
            })
    return pd.DataFrame(rows)


def to_csv_bytes(frame: pd.DataFrame) -> bytes:
    """Файл так, как его отдаёт банк: `;`, UTF-8 с BOM."""
    return frame.to_csv(sep=";", index=False).encode("utf-8-sig")
