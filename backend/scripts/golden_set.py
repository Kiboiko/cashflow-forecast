"""Золотой набор категоризации (A12): ~250 операций для ручной проверки разметки.

Шаги (из backend/):
  1. uv run python scripts/golden_set.py sample         — выбрать ~250 операций (стратифицированно по категориям, из 20
                                                           компаний) → reports/golden_set.csv с колонками для разметки;
  2. человек (или двое за час) заполняет колонку `label` — верная категория; пусто = «правильно как предсказано»;
  3. uv run python scripts/golden_set.py evaluate        — precision и recall по категориям → reports/golden_set.md.

Данные синтетические, набор небольшой — лежит в reports/golden_set.csv и коммитится.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.categorize import categorize
from app.engine.categorize.taxonomy import TAXONOMY, UNKNOWN
from app.engine.io import load_operations
from app.engine.normalize import normalize

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "reports" / "golden_set.csv"
COMPANIES, PER_CATEGORY, SEED = 20, 7, 42


def sample() -> None:
    ops = categorize(normalize(load_operations(ROOT / "data" / "raw" / "all_operations.csv")))
    ids = pd.Series(sorted(ops["company_id"].unique())).sample(COMPANIES, random_state=SEED)
    pool = ops[ops["company_id"].isin(ids)]
    parts = [g.sample(min(len(g), PER_CATEGORY), random_state=SEED) for _, g in pool.groupby("category")]
    gs = pd.concat(parts).sort_values(["category", "company_id", "date"])
    out = gs[["operation_id", "company_id", "date", "direction", "amount", "counterparty_name", "purpose"]].copy()
    out["direction"] = out["direction"].map({1: "приход", -1: "расход"})
    out["predicted"] = gs["category"]
    out["source"] = gs["category_source"]
    out["label"] = ""
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(GOLDEN, sep=";", index=False, encoding="utf-8-sig")
    print(f"{len(out)} операций из {COMPANIES} компаний → {GOLDEN}")


def evaluate(note: str = "") -> None:
    df = pd.read_csv(GOLDEN, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
    unrecognised = int(df["predicted"].isin(UNKNOWN).sum())
    df = df[~df["predicted"].isin(UNKNOWN)].copy()            # «не распознано» — не ошибка разметки, а просьба уточнить
    df["truth"] = df["label"].where(df["label"] != "", df["predicted"])
    unknown = [c for c in set(df["truth"]) | set(df["predicted"]) if c not in TAXONOMY]
    if unknown:
        raise SystemExit(f"Неизвестные категории в разметке: {unknown}")
    rows = []
    for cat in sorted(set(df["truth"]) | set(df["predicted"])):
        tp = int(((df["predicted"] == cat) & (df["truth"] == cat)).sum())
        pred, true = int((df["predicted"] == cat).sum()), int((df["truth"] == cat).sum())
        rows.append((cat, TAXONOMY[cat].title, true, tp / pred if pred else None, tp / true if true else None))
    accuracy = float((df["predicted"] == df["truth"]).mean())
    fixed = df[df["label"] != ""]
    lines = ["# Золотой набор категоризации", "",
             f"{len(df)} операций из {df['company_id'].nunique()} компаний, стратифицировано по категориям. "
             f"Общая точность: **{accuracy:.1%}**; исправлено вручную: {len(fixed)}. "
             f"Ещё {unrecognised} операций с пустым назначением остались «не распознано» (просим уточнить) — в точность не входят.",
             "", *([note, ""] if note else []),
             "Оговорка: выборка стратифицирована по предсказанной категории, поэтому precision оценивается честно, а recall "
             "по ней — нет (пропуски не попадают в выборку). Для recall нужна выборка по независимой разметке.", "",
             "| Категория | Операций | Precision | Recall |", "|---|---|---|---|"]
    fmt = lambda v: "—" if v is None else f"{v:.0%}"
    lines += [f"| {title} | {n} | {fmt(p)} | {fmt(r)} |" for _, title, n, p, r in rows]
    if len(fixed):
        lines += ["", "Исправления:", ""] + [f"- {r.operation_id}: {r.predicted} → {r.label} ({r.purpose[:70]})"
                                              for r in fixed.itertuples()]
    out = ROOT / "reports" / "golden_set.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"точность {accuracy:.1%}, исправлено {len(fixed)} → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["sample", "evaluate"])
    ap.add_argument("--note", default="", help="примечание в отчёт (кто проверял разметку)")
    args = ap.parse_args()
    sample() if args.command == "sample" else evaluate(args.note)
