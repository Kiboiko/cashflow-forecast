"""Демо «учётная система»: открытые счета и договоры, восстановленные из выписки (§6.8, §13).

Для выбранной даты берём документы, у которых дата ≤ as_of, а первая оплата позже as_of; сумма — все оплаты по документу.
Модель увидит номер, дату и сумму документа, но не дату оплаты. В интерфейсе и на слайде подписываем честно:
«демо-данные учётной системы, восстановлены из выписки».

Запуск из backend/:  uv run python scripts/make_demo_plans.py [--as-of 2026-07-31] [C064 C107 C109 ...]
Результат: data/demo/invoices_<company>_<as_of>.csv (формат CSV счетов из §13).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.categorize import categorize
from app.engine.enrich import enrich
from app.engine.io import load_operations
from app.engine.normalize import normalize
from app.engine.receivables import restore_open_documents

ROOT = Path(__file__).resolve().parents[2]
DEMO_COMPANIES = ["C064", "C107", "C109"]          # B2B — C109: у C103 нет документов клиентов
COLUMNS = ["doc_type", "doc_no", "doc_date", "direction", "counterparty_inn", "counterparty_name", "amount", "due_date"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 7, 31))
    ap.add_argument("companies", nargs="*", default=DEMO_COMPANIES)
    args = ap.parse_args()
    ops_all = normalize(load_operations(ROOT / "data" / "raw" / "all_operations.csv"))
    out_dir = ROOT / "data" / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    for cid in args.companies:
        full = enrich(categorize(ops_all[ops_all["company_id"] == cid]))
        rows = [{"doc_type": i.doc_type, "doc_no": i.doc_no, "doc_date": i.doc_date.isoformat(),
                 "direction": i.direction, "counterparty_inn": i.counterparty_inn,
                 "counterparty_name": i.counterparty_name, "amount": round(i.amount, 2), "due_date": ""}
                for i in restore_open_documents(full, args.as_of)]
        path = out_dir / f"invoices_{cid}_{args.as_of.isoformat()}.csv"
        pd.DataFrame(rows, columns=COLUMNS).to_csv(path, sep=";", index=False, encoding="utf-8-sig")
        print(f"{cid}: {len(rows)} открытых документов → {path}")


if __name__ == "__main__":
    main()
