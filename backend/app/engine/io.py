"""Чтение CSV датасета (§6.1). На выходе — «сырой» DataFrame, нормализация — в normalize.py."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_OPS_DTYPE = {
    "company_id": str,
    "operation_id": str,
    "counterparty_inn": str,  # вымышленные ИНН начинаются с 00 — только строкой
    "counterparty_name": str,
    "payment_purpose": str,
    "direction": str,
}
_COMPANIES_DTYPE = {"company_id": str, "company_inn": str}


def _read(path: str | Path, dtype: dict, parse_dates: list[str]) -> pd.DataFrame:
    # UTF-8 с BOM, разделитель «;»
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=dtype, parse_dates=parse_dates)


def load_operations(path: str | Path) -> pd.DataFrame:
    """Выписка(и) в формате датасета: all_operations.csv или statements/Cxxx.csv."""
    return _read(path, _OPS_DTYPE, ["operation_date"])


def load_companies(path: str | Path) -> pd.DataFrame:
    return _read(path, _COMPANIES_DTYPE, ["first_operation_date", "last_operation_date"])


def load_statement(raw_dir: str | Path, company_id: str) -> pd.DataFrame:
    return load_operations(Path(raw_dir) / "statements" / f"{company_id}.csv")
