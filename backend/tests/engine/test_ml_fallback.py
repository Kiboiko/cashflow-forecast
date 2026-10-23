"""ML-добивка категоризации: учится на размеченных правилами, закрывает пустые назначения по имени контрагента."""
from datetime import date, timedelta

import pandas as pd

from app.engine.categorize import categorize
from app.engine.categorize.ml_fallback import predict, train
from app.engine.state import EngineState
from tests.helpers import make_statement, op

NAMES = {
    ("Водоканал-Плюс", -1, "Оплата водоснабжения за месяц"): "utilities",
    ("Аренда-Центр", -1, "Арендная плата за помещение"): "rent",
    ("Продукты-Опт", -1, "Оплата по УПД № 5 от 01.01.2026 за товар"): "suppliers",
    ("Клиент-Один", 1, "Оплата по счету № 7 от 01.01.2026"): "client_payment",
}


def training_ops(n: int = 60):
    days: dict[date, list[dict]] = {}
    for k in range(n):
        d = date(2026, 1, 5) + timedelta(days=k)
        if d.weekday() >= 5:
            continue
        for name, sign, purpose in NAMES:
            days.setdefault(d, []).append(op(sign, 10_000.0 + 137 * k, purpose, inn=None, name=name))
    return categorize(make_statement(days, start=5_000_000.0))


def test_model_recognises_counterparties_it_has_learned():
    model = train(training_ops())
    unknown = make_statement({date(2026, 4, 1): [op(-1, 12_345.0, "", inn=None, name="Водоканал-Плюс"),
                                                 op(1, 12_345.0, "", inn=None, name="Клиент-Один")]})
    category, confidence = predict(model, unknown)
    assert category.tolist() == ["utilities", "client_payment"] and (confidence >= 0.6).all()


def test_low_confidence_stays_unrecognised():
    model = train(training_ops())
    stranger = make_statement({date(2026, 4, 1): [op(-1, 7.0, "", inn=None, name="Zxqvw Ltd")]})
    category, confidence = predict(model, stranger, min_confidence=0.99)
    assert category.isna().all() and confidence.iloc[0] < 0.99


def test_categorize_uses_ml_as_the_last_resort_and_labels_the_source():
    model = train(training_ops())
    ops = make_statement({date(2026, 4, 1): [op(-1, 12_345.0, "", inn=None, name="Водоканал-Плюс"),
                                             op(-1, 100.0, "Оплата рекламных услуг", inn=None, name="Х"),
                                             op(-1, 5.0, "", inn=None, name="Zxqvw Ltd")]})
    plain = categorize(ops)
    with_ml = categorize(ops, ml_model=model)
    assert plain["category_source"].tolist() == ["unknown", "rule", "unknown"]
    assert with_ml["category_source"].tolist()[0] == "ml" and with_ml["category"].iloc[0] == "utilities"
    assert with_ml["category_confidence"].iloc[0] >= 0.6
    assert with_ml["category_source"].iloc[1] == "rule"                    # правила по-прежнему главные
    assert with_ml["category"].iloc[1] == "marketing"


def test_engine_state_applies_the_model_to_new_operations_only():
    model = train(training_ops())
    base = make_statement({date(2026, 3, 2): [op(-1, 1_000.0, "Арендная плата за помещение", inn="0099")]}, start=1e6)
    state = EngineState.build(base, date(2026, 3, 2), ml_model=model)
    more = pd.concat([base, make_statement({date(2026, 3, 3): [op(-1, 12_345.0, "", inn=None, name="Водоканал-Плюс")]})
                      .assign(operation_id="T001-000099")], ignore_index=True)
    updated = state.update(more, date(2026, 3, 3))
    assert updated.ops.loc[updated.ops["operation_id"] == "T001-000099", "category_source"].iloc[0] == "ml"
    assert updated.ml_model is model

