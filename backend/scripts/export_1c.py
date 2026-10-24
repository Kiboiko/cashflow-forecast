"""Выгрузка выписки компании из датасета в формат 1С «Клиент-Банк» — для демо импорта 1С без настоящего файла (§13).

Запуск из backend/:
    uv run python scripts/export_1c.py C064 [--raw ../data/raw] [--out ../data/demo/C064_1c.txt] [--until 2026-07-31]
Результат — текст в Windows-1251, который принимает «Операции → Загрузить выписку».
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.io import load_companies, load_statement
from app.engine.normalize import normalize
from app.integrations.client_bank_1c import export_1c

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("company", help="код компании, например C064")
    parser.add_argument("--raw", type=Path, default=ROOT / "data" / "raw")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--until", type=pd.Timestamp, default=None, help="последняя дата выписки (по умолчанию — вся)")
    args = parser.parse_args()

    ops = normalize(load_statement(args.raw, args.company))
    if args.until is not None:
        ops = ops[ops["date"] <= args.until]
    name, inn = args.company, f"00{abs(hash(args.company)) % 10**8:08d}"
    companies = args.raw / "companies.csv"
    if companies.exists():
        row = load_companies(companies).query("company_id == @args.company")
        if len(row):
            name = str(row.iloc[0].get("company_name") or row.iloc[0].get("name") or name)
            inn = str(row.iloc[0].get("company_inn") or inn)
    account = f"40702810{abs(hash(args.company)) % 10**12:012d}"
    out = args.out or ROOT / "data" / "demo" / f"{args.company}_1c.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(export_1c(ops, account, name, inn))
    print(f"{args.company}: {len(ops)} операций → {out}")


if __name__ == "__main__":
    main()
