"""Правила категоризации по назначению платежа и имени контрагента (§6.3, шаги 2–3)."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from app.engine.normalize import norm_text

RULES_PATH = Path(__file__).with_name("rules.yaml")
_DIR = {"in": 1, "out": -1}


@lru_cache(maxsize=1)
def load_rules(path: str | None = None) -> dict:
    with open(path or RULES_PATH, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    for key in ("purpose_rules", "name_rules"):
        for r in raw[key]:
            r["re"] = re.compile(r["pattern"])
            r["sign"] = _DIR[r["dir"]]
    return raw


def _apply(rules: list[dict], text: pd.Series, direction: pd.Series, todo: pd.Series
           ) -> tuple[pd.Series, pd.Series]:
    """Первое совпавшее правило с учётом направления. Возвращает (категория, id правила)."""
    category = pd.Series(None, index=text.index, dtype="object")
    rule_id = pd.Series(None, index=text.index, dtype="object")
    left = todo.copy()
    for r in rules:
        hit = left & (direction == r["sign"]) & text.str.contains(r["re"], regex=True)
        category[hit] = r["category"]
        rule_id[hit] = r["id"]
        left &= ~hit
    return category, rule_id


def apply_purpose_rules(ops: pd.DataFrame, todo: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Правила по назначению — только для операций с непустым назначением."""
    todo = todo & (ops["purpose_norm"] != "")
    return _apply(load_rules()["purpose_rules"], ops["purpose_norm"], ops["direction"], todo)


def apply_name_rules(ops: pd.DataFrame, todo: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Правила по имени контрагента — только при пустом назначении."""
    todo = todo & (ops["purpose_norm"] == "")
    names = ops["counterparty_name"].map(norm_text)
    return _apply(load_rules()["name_rules"], names, ops["direction"], todo)
