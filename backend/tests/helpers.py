"""Помощники тестов: `make_statement` собирает выписку с согласованным остатком."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.engine.calendar_ru import nominal_date, shift
from app.engine.normalize import normalize

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
needs_dataset = pytest.mark.skipif(not (RAW_DIR / "all_operations.csv").exists(),
                                   reason="датасет не распакован в data/raw")


def op(sign: int, amount: float, purpose: str = "оплата по счету", inn: str | None = "0011223344",
       name: str = "ООО «Контрагент»", counted: bool = True) -> dict:
    """Одна операция. `counted=False` — строка есть в выписке, но остаток её не учитывает (дубль)."""
    return {"sign": sign, "amount": amount, "purpose": purpose, "inn": inn, "name": name, "counted": counted}


def make_statement(days: dict[date, list[dict]], start: float = 100_000.0, jumps: dict[date, float] | None = None,
                   company: str = "T001") -> pd.DataFrame:
    """Нормализованная выписка. `jumps[d]` — необъяснимый скачок остатка в день d (пропуск в выгрузке)."""
    jumps = jumps or {}
    rows, balance, n = [], start, 0
    for d in sorted(days):
        balance += sum(o["sign"] * o["amount"] for o in days[d] if o["counted"]) + jumps.get(d, 0.0)
        for o in days[d]:
            n += 1
            rows.append({
                "company_id": company, "operation_id": f"{company}-{n:06d}", "operation_date": d.isoformat(),
                "amount": o["amount"], "direction": "Поступление" if o["sign"] > 0 else "Списание",
                "counterparty_inn": o["inn"], "counterparty_name": o["name"],
                "payment_purpose": o["purpose"], "balance_eod": round(balance, 2),
            })
    return normalize(pd.DataFrame(rows))


def monthly(start: str, n: int, day: int, rule: str, amount, purpose, inn: str | None = "0099",
            name: str = "ООО «Контрагент»", sign: int = -1) -> dict[date, list[dict]]:
    """n ежемесячных платежей, начиная с месяца `start` ('2025-03'), с переносом выходных по правилу."""
    out: dict[date, list[dict]] = {}
    for k in range(n):
        p = pd.Period(start, "M") + k
        d = shift(nominal_date(p.year, p.month, day), rule)
        a = amount(p) if callable(amount) else amount
        text = purpose(p, d) if callable(purpose) else purpose
        out.setdefault(d, []).append(op(sign, a, text, inn=inn, name=name))
    return out


def merge(*series: dict) -> dict:
    out: dict[date, list[dict]] = {}
    for s in series:
        for d, ops in s.items():
            out.setdefault(d, []).extend(ops)
    return out
