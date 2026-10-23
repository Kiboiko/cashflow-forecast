"""ML-добивка категоризации (§6.3, шаг 5, P2): для операций, которые не разметили ни правила, ни память контрагента.

Это в основном пустые назначения платежа. Признаки: символьные n-граммы (2–4) имени контрагента + направление +
логарифм суммы + день месяца. Модель учится на операциях, размеченных правилами, по ВСЕМ компаниям; принимаем ответ
при уверенности ≥ 0,6, иначе остаётся «уточните». Обучение — офлайн (`train`), применение — `predict` в `categorize`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.engine.categorize.taxonomy import UNKNOWN

MIN_CONFIDENCE = 0.6
MAX_PER_CLASS = 4_000                       # ограничение выборки на класс: обучение за секунды
TRAIN_SOURCES = {"rule", "name_rule"}


def _features(ops: pd.DataFrame) -> pd.DataFrame:
    name = ops["counterparty_name"].fillna("").astype(str).str.lower().str.replace("ё", "е")
    return pd.DataFrame({
        "text": name + " dir" + ops["direction"].map({1: "in", -1: "out"}),
        "log_amount": np.log1p(ops["amount"].astype(float)),
        "dom": ops["date"].dt.day.astype(float),
        "direction": ops["direction"].astype(float),
    })


def train(categorized: pd.DataFrame, seed: int = 0) -> Pipeline:
    """Обучение на операциях, размеченных правилами (`category_source` ∈ rule, name_rule), всех компаний."""
    data = categorized[categorized["category_source"].isin(TRAIN_SOURCES) & ~categorized["category"].isin(UNKNOWN)]
    data = pd.concat([g.sample(min(len(g), MAX_PER_CLASS), random_state=seed) for _, g in data.groupby("category")])
    model = Pipeline([
        ("features", ColumnTransformer([
            ("text", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=2, sublinear_tf=True), "text"),
            ("num", StandardScaler(), ["log_amount", "dom", "direction"]),
        ])),
        ("clf", LogisticRegression(max_iter=300, C=5.0)),
    ])
    return model.fit(_features(data), data["category"])


def predict(model: Pipeline, ops: pd.DataFrame, min_confidence: float = MIN_CONFIDENCE) -> tuple[pd.Series, pd.Series]:
    """(категория, уверенность) для `ops`; где уверенность ниже порога, категория — None."""
    if ops.empty:
        return pd.Series(dtype=object), pd.Series(dtype=float)
    proba = model.predict_proba(_features(ops))
    best = proba.argmax(axis=1)
    confidence = proba.max(axis=1)
    category = np.where(confidence >= min_confidence, model.classes_[best], None)
    return (pd.Series(category, index=ops.index, dtype="object"),
            pd.Series(confidence, index=ops.index, dtype=float))
