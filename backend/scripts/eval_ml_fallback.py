"""Оценка ML-добивки категоризации (P2): обучение на 80% компаний, проверка на остальных 20%.

Запуск из backend/:  uv run python scripts/eval_ml_fallback.py
Печатает точность при пороге уверенности и охват; пишет reports/categorization_ml.md.
Модель видит только имя контрагента, направление, сумму и день месяца — назначения платежа у неё нет,
как у тех операций, ради которых она нужна.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.categorize import categorize
from app.engine.categorize.ml_fallback import MIN_CONFIDENCE, predict, train
from app.engine.io import load_operations
from app.engine.normalize import normalize

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ops = categorize(normalize(load_operations(ROOT / "data" / "raw" / "all_operations.csv")))
    companies = np.array(sorted(ops["company_id"].unique()))
    rng = np.random.default_rng(0)
    holdout = set(rng.choice(companies, size=len(companies) // 5, replace=False))
    train_ops = ops[~ops["company_id"].isin(holdout)]
    test = ops[ops["company_id"].isin(holdout) & ops["category_source"].isin(["rule", "name_rule"])]
    model = train(train_ops)
    category, _ = predict(model, test)
    accepted = category.notna()
    accuracy = float((category[accepted] == test.loc[accepted, "category"]).mean())
    print(f"holdout: {len(holdout)} компаний, {len(test)} операций; охват при уверенности ≥{MIN_CONFIDENCE}: "
          f"{accepted.mean():.1%}; точность на принятых: {accuracy:.1%}")
    # то же для операций с «серыми» контрагентами (банк, физлица, казначейство), где назначения чаще пустые
    grey = test["counterparty_name"].str.contains("Банк|Физическое лицо|Казначейство|ФНС", na=False)
    acc_grey = float((category[accepted & grey] == test.loc[accepted & grey, "category"]).mean())
    print(f"серые контрагенты: {int(grey.sum())} операций, охват {accepted[grey].mean():.1%}, точность {acc_grey:.1%}")

    # применение к настоящим нераспознанным операциям всего датасета
    model_all = train(ops)
    unknown = ops[ops["category_source"] == "unknown"]
    cat_u, _ = predict(model_all, unknown)
    print(f"нераспознанных было {len(unknown)}; добивка закрыла {int(cat_u.notna().sum())} "
          f"({cat_u.notna().mean():.0%}), остаток {int(cat_u.isna().sum())} → «уточните»")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "categorization_ml.md").write_text("\n".join([
        "# ML-добивка категоризации",
        "",
        f"Проверка на {len(holdout)} компаниях, которых модель не видела: {len(test)} операций, размеченных правилами. "
        "Модель получает только имя контрагента, направление, сумму и день месяца (назначения платежа нет).",
        "",
        f"- охват при уверенности ≥{MIN_CONFIDENCE}: **{accepted.mean():.1%}**, точность на принятых: **{accuracy:.1%}**;",
        f"- «серые» контрагенты (банк, физлица, казначейство): охват {accepted[grey].mean():.1%}, точность {acc_grey:.1%};",
        f"- на настоящих нераспознанных операциях: было {len(unknown)}, добивка закрыла {int(cat_u.notna().sum())} "
        f"({cat_u.notna().mean():.0%}), остальное остаётся «уточните».",
        "",
    ]), encoding="utf-8")


if __name__ == "__main__":
    main()
