"""Обогащение операций (§6.4): документы, договоры, даты продаж, периоды, подвид зарплаты.

Регулярки работают по `purpose_norm` (нижний регистр, ё→е, схлопнутые пробелы).
Все написания из §3.2 п. 8 покрыты тестами: «№ 279», «№279», «№ 12/3», «счету 123», «от 19.08.2024г.».
"""
from __future__ import annotations

import re

import pandas as pd

_DATE = r"(\d{2}\.\d{2}\.\d{4})"

# \b перед типом документа: иначе «счет» совпадёт внутри слова «расчет».
DOC_RE = re.compile(rf"\b(упд|счет[ауе]?|акт[ау]?)\s*№?\s*([\w/-]+)\s*от\s*{_DATE}")
CONTRACT_RE = re.compile(rf"договор[ауе]?\s*№\s*([\w/-]+)(?:\s*от\s*{_DATE})?")
CARD_DATE_RE = re.compile(rf"дата реестра {_DATE}")
SBP_DATE_RE = re.compile(rf"\bза {_DATE}")
PERIOD_RE = re.compile(rf"\bс {_DATE} по {_DATE}")

_DOC_TYPE = {"у": "upd", "с": "invoice", "а": "act"}  # по первой букве: упд / счет* / акт*


def _doc_type(word: str) -> str:
    return _DOC_TYPE[word[0]]


def parse_document(purpose_norm: str) -> tuple[str, str, pd.Timestamp] | None:
    """(doc_type, doc_no, doc_date) или None. doc_type: upd | invoice | act."""
    m = DOC_RE.search(purpose_norm or "")
    if not m:
        return None
    return _doc_type(m.group(1)), m.group(2), pd.to_datetime(m.group(3), format="%d.%m.%Y", errors="coerce")


def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%d.%m.%Y", errors="coerce")


def enrich(ops: pd.DataFrame) -> pd.DataFrame:
    """Добавляет столбцы: doc_type, doc_no, doc_date, contract_no, contract_date,
    period_start, period_end и (если есть `category`) sales_date, salary_kind."""
    out = ops.copy()
    p = out["purpose_norm"]

    doc = p.str.extract(DOC_RE)
    out["doc_type"] = doc[0].map(lambda w: _doc_type(w) if isinstance(w, str) else None)
    out["doc_no"] = doc[1]
    out["doc_date"] = _to_date(doc[2])

    contract = p.str.extract(CONTRACT_RE)
    out["contract_no"] = contract[0]
    out["contract_date"] = _to_date(contract[1])

    period = p.str.extract(PERIOD_RE)
    out["period_start"] = _to_date(period[0])
    out["period_end"] = _to_date(period[1])

    if "category" in out.columns:
        sales = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
        card = out["category"] == "revenue_card"
        sbp = out["category"] == "revenue_sbp"
        sales[card] = _to_date(p[card].str.extract(CARD_DATE_RE)[0])
        sales[sbp] = _to_date(p[sbp].str.extract(SBP_DATE_RE)[0])
        out["sales_date"] = sales
        kind = pd.Series(None, index=out.index, dtype="object")
        sal = out["category"] == "salary"
        kind[sal] = p[sal].map(lambda s: "advance" if "аванс" in s else "final")
        out["salary_kind"] = kind
    return out
