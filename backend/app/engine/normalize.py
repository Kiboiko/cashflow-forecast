"""Нормализация операций (§6.1): типы, ИНН строкой, ё→е, ключ контрагента.

На выходе любого импорта (CSV датасета, 1С) — один и тот же DataFrame со столбцами OPS_COLUMNS.
"""
from __future__ import annotations

import re

import pandas as pd

OPS_COLUMNS = [
    "company_id", "operation_id", "date", "amount", "direction",
    "counterparty_inn", "counterparty_name", "purpose", "purpose_norm", "cp_key", "balance_eod",
]

# Направление: как в датасете («Поступление»/«Списание») и частые синонимы из других выгрузок (регистр не важен).
_DIRECTION = {"поступление": 1, "приход": 1, "зачисление": 1, "in": 1, "income": 1, "credit": 1, "+": 1,
              "списание": -1, "расход": -1, "out": -1, "outcome": -1, "debit": -1, "-": -1}
_SPACES = re.compile(r"\s+")


def norm_text(s: str | None) -> str:
    """Нижний регистр, ё→е, схлопнутые пробелы. None/NaN → пустая строка."""
    if s is None or s is pd.NA or (isinstance(s, float) and pd.isna(s)):
        return ""
    return _SPACES.sub(" ", str(s).lower().replace("ё", "е")).strip()


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Сырой DataFrame датасета → нормализованные операции, отсортированные по (компания, дата, id).

    `direction`: +1 поступление, −1 списание. `cp_key` — ИНН, иначе нормализованное имя.
    """
    df = pd.DataFrame({
        "company_id": raw["company_id"].astype(str),
        "operation_id": raw["operation_id"].astype(str),
        "date": pd.to_datetime(raw["operation_date"]).dt.normalize(),
        "amount": raw["amount"].astype(float),
        "direction": raw["direction"].astype("string").str.strip().str.lower().map(_DIRECTION),
        "counterparty_inn": raw["counterparty_inn"].astype("string").str.strip().replace("", pd.NA),
        "counterparty_name": raw["counterparty_name"].astype("string"),
        "purpose": raw["payment_purpose"].astype("string"),
        "balance_eod": raw["balance_eod"].astype(float),
    })
    if df["direction"].isna().any():
        bad = raw.loc[df["direction"].isna(), "direction"].unique()[:5]
        raise ValueError(f"Неизвестное направление операции: {list(bad)}")
    df["direction"] = df["direction"].astype(int)
    df["purpose_norm"] = df["purpose"].map(norm_text)
    name_norm = df["counterparty_name"].map(norm_text)
    df["cp_key"] = df["counterparty_inn"].astype(object).where(df["counterparty_inn"].notna(), name_norm)
    return (df[OPS_COLUMNS]
            .sort_values(["company_id", "date", "operation_id"], kind="stable")
            .reset_index(drop=True))
