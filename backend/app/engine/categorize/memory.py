"""Память контрагента (§6.3, шаг 4): категория пары (бизнес, контрагент, направление) из размеченных правилами операций."""
from __future__ import annotations

import pandas as pd

MIN_LABELED = 3        # ≥3 размеченных операций у контрагента
MIN_SHARE = 0.95       # и ≥95% из них — одна категория

_KEY = ["company_id", "cp_key", "direction"]


def build_memory(ops: pd.DataFrame, labeled: pd.Series) -> pd.DataFrame:
    """Таблица (company_id, cp_key, direction) → (category, share, n) для однозначных контрагентов.

    `labeled` — маска операций, размеченных по назначению (в память идут только они).
    """
    lab = ops.loc[labeled & ops["category"].notna(), _KEY + ["category"]]
    if lab.empty:
        return pd.DataFrame(columns=_KEY + ["category", "share", "n"])
    counts = lab.groupby(_KEY + ["category"]).size().rename("cnt").reset_index()
    total = counts.groupby(_KEY)["cnt"].transform("sum")
    counts["share"] = counts["cnt"] / total
    counts["n"] = total
    best = counts.sort_values("cnt", ascending=False).drop_duplicates(_KEY)
    return best[(best["n"] >= MIN_LABELED) & (best["share"] >= MIN_SHARE)][_KEY + ["category", "share", "n"]]


def apply_memory(ops: pd.DataFrame, memory: pd.DataFrame, todo: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Категория и уверенность из памяти для операций из `todo`; остальные — None/NaN."""
    category = pd.Series(None, index=ops.index, dtype="object")
    conf = pd.Series(float("nan"), index=ops.index)
    if memory.empty or not todo.any():
        return category, conf
    merged = ops.loc[todo, _KEY].merge(memory, on=_KEY, how="left")
    merged.index = ops.index[todo]
    category[todo] = merged["category"]
    conf[todo] = merged["share"]
    return category, conf
