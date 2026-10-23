"""Калибровка шанса разрыва (масштабирование Платта по логиту).

Сырая доля сценариев, в которых остаток уходит ниже порога, завышает риск в середине шкалы (бэктест: «говорим 46% —
бывает 21%»). Параметры подбирает `scripts/backtest.py --write-calibration` ТОЛЬКО на отсечках настройки
(31.10.2025 … 28.02.2026) и пишет в calibration.json; на тестовых отсечках калибровка проверяется вне выборки.
Применяется к итоговому шансу разрыва и к уровню риска; даты разрыва и покрытие платежей остаются «сырыми».
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

CALIBRATION_PATH = Path(__file__).with_name("calibration.json")
IDENTITY = {"a": 1.0, "b": 0.0, "eps": 1e-3}


@lru_cache(maxsize=1)
def load_params(path: str | None = None) -> dict:
    p = Path(path) if path else CALIBRATION_PATH
    return {**IDENTITY, **json.loads(p.read_text(encoding="utf-8"))} if p.exists() else dict(IDENTITY)


def _logit(p, eps: float):
    p = np.asarray(p, dtype=float)
    return np.log((p + eps) / (1 - p + eps))


def apply_platt(p, a: float, b: float, eps: float = IDENTITY["eps"]):
    """σ(a·logit(p) + b) с тем же сглаживанием eps, что и при подборе."""
    return 1 / (1 + np.exp(-(a * _logit(p, eps) + b)))


def fit_platt(p, y, eps: float = IDENTITY["eps"]) -> tuple[float, float]:
    """Подбор (a, b) логистической регрессией исход ~ logit(шанс)."""
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression(C=100).fit(_logit(p, eps).reshape(-1, 1), np.asarray(y, dtype=int))
    return float(model.coef_[0][0]), float(model.intercept_[0])


def calibrate(p: float) -> float:
    params = load_params()
    return float(apply_platt(p, params["a"], params["b"], params["eps"]))
