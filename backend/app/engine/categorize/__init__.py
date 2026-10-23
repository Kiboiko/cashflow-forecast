"""Категоризация операций (§6.3). Порядок (побеждает первое совпадение):

1. правило предпринимателя по контрагенту (`user`);
2. правила по назначению платежа (`rule`);
3. правила по имени контрагента при пустом назначении (`name_rule`);
4. память контрагента (`memory`);
5. ML-добивка (`ml`, P2 — ml_fallback.py);
6. иначе `unknown_in` / `unknown_out` («уточните» в интерфейсе).
"""
from __future__ import annotations

import pandas as pd

from app.engine.categorize.memory import apply_memory, build_memory
from app.engine.categorize.rules import apply_name_rules, apply_purpose_rules

CATEGORY_COLUMNS = ["category", "category_source", "category_confidence"]


def learn_memory(categorized: pd.DataFrame) -> pd.DataFrame:
    """Память контрагентов из операций, размеченных правилами по назначению (для инкрементальной категоризации)."""
    return build_memory(categorized, categorized["category_source"] == "rule")


def categorize(ops: pd.DataFrame, user_rules: dict[str, str] | None = None,
               memory: pd.DataFrame | None = None, ml_model=None) -> pd.DataFrame:
    """Добавляет category, category_source (user|rule|name_rule|memory|unknown), category_confidence.

    `user_rules`: {cp_key: category} — исправления предпринимателя (CounterpartyRule).
    `memory`: готовая память контрагентов (из `learn_memory`); без неё память строится из самих `ops`.
    Так обновление данных категоризирует только новые операции, не трогая историю.
    `ml_model`: обученная добивка (`ml_fallback.train`) для операций, которые не разметили ни правила, ни память.
    """
    out = ops.copy()
    out["category"] = None
    out["category_source"] = "unknown"
    out["category_confidence"] = 0.0
    todo = pd.Series(True, index=out.index)

    def take(cat: pd.Series, source: str, conf: float | pd.Series) -> None:
        nonlocal todo
        hit = todo & cat.notna()
        out.loc[hit, "category"] = cat[hit]
        out.loc[hit, "category_source"] = source
        out.loc[hit, "category_confidence"] = conf[hit] if isinstance(conf, pd.Series) else conf
        todo &= ~hit

    if user_rules:
        take(out["cp_key"].map(user_rules), "user", 1.0)
    cat, _ = apply_purpose_rules(out, todo)
    take(cat, "rule", 1.0)
    cat, _ = apply_name_rules(out, todo)
    take(cat, "name_rule", 0.9)

    if memory is None:
        memory = learn_memory(out)
    cat, conf = apply_memory(out, memory, todo)
    take(cat, "memory", conf)

    if ml_model is not None and todo.any():
        from app.engine.categorize.ml_fallback import predict

        cat, conf = predict(ml_model, out[todo])
        take(cat.reindex(out.index), "ml", conf.reindex(out.index))

    unknown = todo
    out.loc[unknown, "category"] = out.loc[unknown, "direction"].map({1: "unknown_in", -1: "unknown_out"})
    return out
