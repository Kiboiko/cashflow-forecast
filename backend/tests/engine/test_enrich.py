import pandas as pd
import pytest

from app.engine.enrich import enrich, parse_document
from app.engine.normalize import norm_text


@pytest.mark.parametrize("purpose, doc_type, doc_no, doc_date", [
    ("Оплата по УПД № 279 от 19.08.2024 за товар", "upd", "279", "2024-08-19"),
    ("Оплата по УПД №279 от 19.08.2024 за товар", "upd", "279", "2024-08-19"),
    ("Оплата по УПД № 12/3 от 19.08.2024г.", "upd", "12/3", "2024-08-19"),
    ("Оплата по счету 123 от 05.05.2025", "invoice", "123", "2025-05-05"),
    ("Оплата по счету № 4546 от 17.04.2026 за продукты", "invoice", "4546", "2026-04-17"),
    ("Оплата по акту № А-15 от 01.02.2026", "act", "а-15", "2026-02-01"),
])
def test_document_spellings(purpose, doc_type, doc_no, doc_date):
    assert parse_document(norm_text(purpose)) == (doc_type, doc_no, pd.Timestamp(doc_date))


def test_rascheta_is_not_a_document():
    # «расчет» содержит «счет»: без \b это дало бы ложный документ
    assert parse_document(norm_text("Расчет по договору № 5 от 01.02.2026")) is None


def _frame(purposes, categories=None):
    df = pd.DataFrame({"purpose_norm": [norm_text(p) for p in purposes]})
    if categories:
        df["category"] = categories
    return df


def test_contract_number_and_date():
    out = enrich(_frame(["Предоплата по договору № К-12/3 от 05.03.2026", "Оплата по договору поставки"]))
    assert out["contract_no"].tolist()[0] == "к-12/3"
    assert out["contract_date"].iloc[0] == pd.Timestamp("2026-03-05")
    assert pd.isna(out["contract_no"].iloc[1])   # без «№» — не номер договора


def test_sales_date_for_card_and_sbp():
    out = enrich(_frame(
        ["Зачисление по операциям эквайринга. Дата реестра 30.09.2024. Сумма 100",
         "Зачисление по операциям оплаты через СБП (QR-код) за 29.09.2024. Сумма 5",
         "Комиссия за сентябрь"],
        ["revenue_card", "revenue_sbp", "bank_fees"]))
    assert out["sales_date"].iloc[0] == pd.Timestamp("2024-09-30")
    assert out["sales_date"].iloc[1] == pd.Timestamp("2024-09-29")
    assert pd.isna(out["sales_date"].iloc[2])


def test_marketplace_period():
    out = enrich(_frame(["Выплата по договору оферты за период с 01.01.2025 по 07.01.2025"]))
    assert out["period_start"].iloc[0] == pd.Timestamp("2025-01-01")
    assert out["period_end"].iloc[0] == pd.Timestamp("2025-01-07")


def test_salary_kind():
    out = enrich(_frame(["Аванс по заработной плате за октябрь", "Заработная плата за октябрь", "Аренда"],
                        ["salary", "salary", "rent"]))
    assert out["salary_kind"].tolist()[:2] == ["advance", "final"]
    assert pd.isna(out["salary_kind"].iloc[2])
