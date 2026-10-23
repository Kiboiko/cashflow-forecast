"""Воспроизводит факты о датасете из §3.1 ARCHITECTURE.md.

Запуск из backend/:  uv run python scripts/eda_facts.py [путь к data/raw]
Если цифра не сходится с документом — верим данным и правим документ.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.io import load_companies, load_operations

DUP_KEY = ["company_id", "operation_date", "amount", "direction", "counterparty_inn",
           "counterparty_name", "payment_purpose"]


def section(title: str) -> None:
    print(f"\n== {title} ==")


def main(raw_dir: Path) -> None:
    ops = load_operations(raw_dir / "all_operations.csv")
    comp = load_companies(raw_dir / "companies.csv")

    section("Масштаб")
    n_in = (ops.direction == "Поступление").sum()
    print(f"компаний {len(comp)} (ООО {(comp.legal_form == 'ООО').sum()}, ИП {(comp.legal_form == 'ИП').sum()}), "
          f"отраслей {comp.industry.nunique()}")
    print(f"операций {len(ops)} (поступлений {n_in}, списаний {len(ops) - n_in})")
    print(f"период {ops.operation_date.min().date()} – {ops.operation_date.max().date()}")
    per = ops.groupby("company_id").size()
    print(f"на компанию: min {per.min()}, max {per.max()}, медиана {per.median():.0f}")

    section("Короткая история и закрытые счета")
    months = (comp.last_operation_date - comp.first_operation_date).dt.days / 30.44
    print(f"<13 мес. {(months < 13).sum()}, <6 мес. {(months < 6).sum()}, <3 мес. {(months < 3).sum()}")
    print(f"закрыли счёт до 01.09.2026: {(comp.last_operation_date < '2026-09-01').sum()}")

    section("Пустые поля")
    print(f"назначение {ops.payment_purpose.isna().sum()} ({ops.payment_purpose.isna().mean():.2%}), "
          f"ИНН {ops.counterparty_inn.isna().sum()} ({ops.counterparty_inn.isna().mean():.1%})")

    section("Дубли")
    dup_mask = ops.duplicated(subset=DUP_KEY, keep="first")
    print(f"лишних копий строк: {dup_mask.sum()}")

    section("Сверка остатков (balance_eod)")
    ops = ops.sort_values(["company_id", "operation_date", "operation_id"]).reset_index(drop=True)
    ops["signed"] = ops.amount.where(ops.direction == "Поступление", -ops.amount)
    day = ops.groupby(["company_id", "operation_date"], as_index=False).agg(
        eod=("balance_eod", "first"), net=("signed", "sum"))
    day["prev_eod"] = day.groupby("company_id").eod.shift()
    day["diff"] = day.eod - (day.prev_eod + day.net)
    checked = day.dropna(subset=["prev_eod"])
    tiny = checked[(checked["diff"].abs() > 0.005) & (checked["diff"].abs() <= 1)]
    big = checked[checked["diff"].abs() > 1]
    print(f"balance_eod одинаков внутри дня: "
          f"{(ops.groupby(['company_id', 'operation_date']).balance_eod.nunique() == 1).all()}")
    print(f"дней с расхождением: всего {len(tiny) + len(big)}; копейки (≤1 ₽) {len(tiny)}; больше 1 ₽ {len(big)}")
    dup_sum = ops[dup_mask].groupby(["company_id", "operation_date"]).signed.sum().rename("dup_net")
    big = big.join(dup_sum, on=["company_id", "operation_date"])
    # остаток дубль не учитывает: факт − ожидание = −(знаковая сумма дубля)
    explained = big.dup_net.notna() & ((big["diff"] + big.dup_net).abs() <= 1)
    print(f"  из них объясняются дублем: {explained.sum()}; остальное (дыры): {(~explained).sum()}")

    section("Минус на счёте")
    neg = day[day.eod < 0]
    print(f"дней {len(neg)} у {neg.company_id.nunique()} компаний, минимум {day.eod.min():,.0f} ₽")

    section("Налоги (ЕНП)")
    purpose = ops.payment_purpose.fillna("").str.lower().str.replace("ё", "е")
    enp = ops[(ops.direction == "Списание") & purpose.str.contains(r"единый налоговый|(?:^|\W)енп(?:\W|$)")]
    print(f"платежей ЕНП {len(enp)}, ровно 28-го: {(enp.operation_date.dt.day == 28).sum()}")


if __name__ == "__main__":
    default = Path(__file__).resolve().parents[2] / "data" / "raw"
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else default)
