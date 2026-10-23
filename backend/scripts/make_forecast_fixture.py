"""Генерирует tests/fixtures/forecast_example.json — настоящий прогноз по C064 на 31.07.2026 (демо-персона «Ирина»).

Фикстура нужна фронту и тестам API: ответ GET /forecast в формате контракта (§8), посчитанный движком.
Запуск из backend/:  uv run python scripts/make_forecast_fixture.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.categorize import categorize
from app.engine.context import build_context
from app.engine.io import load_companies, load_operations
from app.engine.normalize import normalize
from app.engine.pipeline import run_forecast
from app.engine.response import to_response

ROOT = Path(__file__).resolve().parents[2]
COMPANY, AS_OF = "C064", date(2026, 7, 31)


def build():
    raw = ROOT / "data" / "raw"
    ops_all = normalize(load_operations(raw / "all_operations.csv"))
    context = build_context(categorize(ops_all), load_companies(raw / "companies.csv"), COMPANY, AS_OF)
    result = run_forecast(ops_all[ops_all["company_id"] == COMPANY], AS_OF, context=context, horizon=30)
    return to_response(result, COMPANY)


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "forecast_example.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    resp = build()
    out.write_text(resp.model_dump_json(indent=2, by_alias=True), encoding="utf-8")
    print("written", out)
    print(resp.risk.level, resp.risk.gap_probability, "|", resp.risk.headline)
    for r in resp.recommendations:
        print(f" [{r.priority}] {r.title} -> шанс {r.effect.gap_probability_after:.0%}")
