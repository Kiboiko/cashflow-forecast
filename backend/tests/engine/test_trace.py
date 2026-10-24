"""Пошаговый журнал расчёта: все шаги на месте, без ИНН и названий контрагентов."""
from datetime import date, timedelta

import pandas as pd

from app.engine.pipeline import build_model, evaluate
from app.engine.trace import compare_with_fact, trace_forecast
from tests.helpers import make_statement, merge, monthly

AS_OF = date(2026, 7, 31)


def _model(balance: float = 100_000.0):
    days = merge(
        monthly("2025-07", 13, 5, "next", 150_000.0, "Арендная плата за помещение", inn="0099123456", name="ООО «Арендодатель»"),
        monthly("2025-07", 13, 25, "prev", 120_000.0, "Выплата заработной платы за месяц", inn=None),
        monthly("2025-07", 13, 15, "next", 270_000.0, "Вознаграждение по агентскому договору № 7", inn="0044123456",
                name="ООО «Платформа»", sign=1),
    )
    net = sum(o["sign"] * o["amount"] for ops in days.values() for o in ops)
    return build_model(make_statement(days, start=balance - net), AS_OF, seed=1)


def test_trace_has_every_step_and_no_counterparty_identity():
    model = _model()
    lines = trace_forecast(model, evaluate(model, 300))
    text = "\n".join(lines)
    for step in ("Шаг 1.", "Шаг 2.", "Шаг 3.", "Шаг 4.", "Шаг 9.", "Шаг 10.", "Шаг 12."):
        assert step in text
    assert "Аренда — ежемесячно (5-го)" in text and "откалиброванный шанс" in text
    assert "0099123456" not in text and "Арендодатель" not in text and "Платформа" not in text


def test_compare_with_fact_counts_days_inside_the_corridor():
    model = _model()
    result = evaluate(model, 300)
    fact = pd.Series(result.p50, index=pd.to_datetime(result.dates))          # факт = медиана: ошибка 0, всё в коридоре
    line = compare_with_fact(result, fact)
    assert "за 30 дн." in line and "0 ₽" in line and "100% дней" in line
    assert compare_with_fact(result, pd.Series(dtype=float)) == "Факта после даты прогноза нет."
    assert result.dates[0] == AS_OF + timedelta(days=1)
