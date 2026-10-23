"""Замер: обновление прогноза после новых данных против полного пересчёта (§6.17, слайд про архитектуру).

Для компаний с самыми длинными выписками: состояние на 24.07.2026, затем +7 дней данных.
  полный пересчёт — EngineState.build по всей истории + прогноз;
  обновление     — EngineState.update(новые операции) + прогноз (категоризируются только новые операции).
Запуск из backend/:  uv run python scripts/measure_update.py
"""
from __future__ import annotations

import statistics
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.io import load_operations
from app.engine.normalize import normalize
from app.engine.state import EngineState

ROOT = Path(__file__).resolve().parents[2]
BASE, LATER = date(2026, 7, 24), date(2026, 7, 31)


def timed(fn):
    t = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - t


def main(n_companies: int = 12, repeats: int = 3) -> None:
    ops_all = normalize(load_operations(ROOT / "data" / "raw" / "all_operations.csv"))
    sizes = ops_all.groupby("company_id").size().sort_values(ascending=False)
    rows = []
    for cid in sizes.index[:n_companies]:
        ops = ops_all[ops_all["company_id"] == cid]
        before, after = ops[ops["date"] <= pd.Timestamp(BASE)], ops[ops["date"] <= pd.Timestamp(LATER)]
        state = EngineState.build(before, BASE)
        full_t, upd_t, prep_full, prep_upd = [], [], [], []
        for _ in range(repeats):
            (full, t_prep) = timed(lambda after=after: EngineState.build(after, LATER))
            _, t_fc = timed(lambda full=full: full.forecast(n_paths=1000, with_recommendations=True))
            (upd, t_prep_u) = timed(lambda state=state, after=after: state.update(after, LATER))
            _, t_fc_u = timed(lambda upd=upd: upd.forecast(n_paths=1000, with_recommendations=True))
            full_t.append(t_prep + t_fc), upd_t.append(t_prep_u + t_fc_u)
            prep_full.append(t_prep), prep_upd.append(t_prep_u)
        rows.append((cid, len(after), len(after) - len(before), statistics.median(prep_full), statistics.median(prep_upd),
                     statistics.median(full_t), statistics.median(upd_t)))
    print(f"{'компания':9}{'операций':>9}{'новых':>7}{'сборка, с':>11}{'обновл., с':>12}{'итого полн., с':>16}{'итого обн., с':>15}")
    for r in rows:
        print(f"{r[0]:9}{r[1]:>9}{r[2]:>7}{r[3]:>11.3f}{r[4]:>12.3f}{r[5]:>16.3f}{r[6]:>15.3f}")
    full_med, upd_med = statistics.median(r[5] for r in rows), statistics.median(r[6] for r in rows)
    prep_med, prep_upd_med = statistics.median(r[3] for r in rows), statistics.median(r[4] for r in rows)
    summary = (f"медиана: подготовка данных {prep_med:.3f} с → {prep_upd_med:.3f} с ({prep_med / prep_upd_med:.1f}×); "
               f"от новых данных до готового прогноза с рекомендациями: полный пересчёт {full_med:.3f} с, "
               f"обновление {upd_med:.3f} с ({full_med / upd_med:.1f}×)")
    print("\n" + summary)
    table = ["| Компания | Операций | Новых | Подготовка, с | Обновление, с | Итого полный, с | Итого обновление, с |",
             "|---|---|---|---|---|---|---|"]
    table += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:.3f} | {r[4]:.3f} | {r[5]:.3f} | {r[6]:.3f} |" for r in rows]
    intro = (f"Состояние на {BASE}, затем +7 дней данных ({LATER}); {n_companies} компаний с самыми длинными "
             "выписками, медиана по 3 повторам. Итого = подготовка данных + прогноз на 1000 сценариев с "
             "рекомендациями.")
    out = ROOT / "reports" / "update_timing.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n\n".join(["# Обновление прогноза против полного пересчёта", intro, "\n".join(table),
                                f"**{summary}**"]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
