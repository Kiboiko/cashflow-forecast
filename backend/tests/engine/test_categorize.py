"""Категоризация: по примеру на каждое правило, на каждую ловушку §3.2 и покрытие на реальных данных."""
from datetime import date

import pytest

from app.engine.categorize import categorize
from app.engine.categorize.rules import load_rules
from app.engine.categorize.taxonomy import EXCLUDED_FROM_MODEL, MANDATORY, TAXONOMY
from tests.helpers import RAW_DIR, make_statement, needs_dataset, op


def cat(sign: int, purpose: str, name: str = "ООО «Контрагент»", inn: str | None = "0011") -> str:
    ops = make_statement({date(2026, 3, 2): [op(sign, 100.0, purpose, inn=inn, name=name)]})
    return categorize(ops)["category"].iloc[0]


# (направление, назначение, ожидаемая категория, id правила)
RULE_EXAMPLES = [
    (1, "Зачисление средств по операциям эквайринга. Мерчант №3543067. Дата реестра 30.09.2024", "revenue_card", "in_card"),
    (1, "Зачисление по операциям оплаты через СБП (QR-код) за 30.09.2024", "revenue_sbp", "in_sbp"),
    (1, "Инкассированная выручка по сумке № 241295 от 01.10.2024", "revenue_cash", "in_cash"),
    (1, "Выплата по договору оферты № 5 за период с 01.01.2025 по 07.01.2025", "revenue_marketplace", "in_marketplace"),
    (1, "Вознаграждение по агентскому договору № 7", "revenue_agent", "in_agent"),
    (1, "Перевод собственных средств", "own_transfer_in", "in_own_transfer"),
    (1, "Выдача кредита по кредитному договору № 3", "loan_in", "in_loan"),
    (1, "Займ от учредителя по договору", "owner_injection", "in_owner"),
    (1, "Возврат платежа: неверно указаны реквизиты получателя", "refund_in", "in_refund"),
    (1, "Выплата страхового возмещения по полису", "insurance_in", "in_insurance"),
    (1, "Субсидия на возмещение затрат", "subsidy_in", "in_subsidy"),
    (1, "Предоплата по договору № 9", "client_prepayment", "in_prepayment"),
    (1, "Оплата по УПД № 5 от 01.02.2026", "client_payment", "in_client"),
    (-1, "Списание по поручению налогового органа", "tax_enforced", "out_tax_enforced"),
    (-1, "Оплата по исполнительному листу № 2-123", "enforcement", "out_enforcement"),
    (-1, "Единый налоговый платёж", "tax_enp", "out_enp"),
    (-1, "Выплата заработной платы за октябрь", "salary", "out_salary"),
    (-1, "Оплата по договору подряда № 4", "contractors", "out_contractors"),
    (-1, "Погашение кредита по договору № 3", "loan_repayment", "out_loan"),
    (-1, "Лизинговый платеж по договору № 8", "leasing", "out_leasing"),
    (-1, "Возврат займа учредителю", "owner_loan_repayment", "out_owner_loan"),
    (-1, "Перевод на личную карту владельца", "owner_withdrawal", "out_withdrawal"),
    (-1, "Перевод собственных средств", "own_transfer_out", "out_own_transfer"),
    (-1, "Комиссия за ведение счета за сентябрь", "bank_fees", "out_bank_fees"),
    (-1, "Обеспечительный платеж по договору аренды", "rent_deposit", "out_rent_deposit"),
    (-1, "Арендная плата за помещение за август", "rent", "out_rent"),
    (-1, "Оплата электроэнергии за август", "utilities", "out_utilities"),
    (-1, "Оплата услуг связи за август", "telecom_it", "out_telecom"),
    (-1, "Оплата рекламных услуг", "marketing", "out_marketing"),
    (-1, "Оплата топливных карт", "logistics", "out_logistics"),
    (-1, "Услуги уборки помещений", "services", "out_services"),
    (-1, "По договору купли-продажи оборудования", "capex", "out_capex"),
    (-1, "Оплата по УПД № 5 от 01.02.2026 за товар", "suppliers", "out_suppliers"),
    (-1, "Возврат излишне уплаченных средств", "refund_out", "out_refund"),
]


@pytest.mark.parametrize("sign, purpose, expected, rule_id", RULE_EXAMPLES, ids=[r[3] for r in RULE_EXAMPLES])
def test_rule_example(sign, purpose, expected, rule_id):
    assert cat(sign, purpose) == expected


def test_every_rule_has_an_example_and_known_category():
    rules = load_rules()["purpose_rules"]
    assert {r["id"] for r in rules} == {e[3] for e in RULE_EXAMPLES}
    assert all(r["category"] in TAXONOMY for r in rules + load_rules()["name_rules"])


# ---- ловушки §3.2 ----
def test_trap1_bez_naloga_is_not_tax():
    assert cat(-1, "Оплата по УПД № 5 от 01.02.2026 за товар. Без налога (НДС)") == "suppliers"
    assert cat(-1, "Аренда помещения за август. Без налога (НДС)") == "rent"


def test_trap2_acquiring_with_return_is_revenue():
    p = "Зачисление по операциям эквайринга. Сумма 100, комиссия 2, возврат покупки 0.00"
    assert cat(1, p) == "revenue_card"


def test_trap3_avans_depends_on_direction():
    assert cat(-1, "Аванс по заработной плате за октябрь") == "salary"
    assert cat(1, "Авансовый платеж по договору № 5") == "client_prepayment"


def test_trap4_pos_rent_is_bank_fee_and_deposit_is_not_rent():
    assert cat(-1, "Плата за аренду POS-терминалов за август") == "bank_fees"
    assert cat(-1, "Обеспечительный платеж по договору аренды") == "rent_deposit"


def test_trap5_own_transfer_both_directions():
    assert cat(1, "Перевод собственных средств") == "own_transfer_in"
    assert cat(-1, "Перевод собственных средств") == "own_transfer_out"


def test_trap6_erroneous_payment_return():
    p = "Возврат платежа: неверно указаны реквизиты получателя. Оплата по УПД № 5 от 01.02.2026"
    assert cat(1, p) == "refund_in"


def test_trap7_guarantee_retention_is_client_payment():
    assert cat(1, "Возврат гарантийного удержания по договору № 5") == "client_payment"


def test_trap9_empty_purpose_by_counterparty_name():
    assert cat(-1, "", name="Казначейство России (ФНС России)") == "tax_enp"
    assert cat(-1, "", name="ПАО «Банк»") == "bank_fees"
    assert cat(1, "", name="ПАО «Банк»", inn="0099") == "unknown_in"


def test_user_rule_wins_over_purpose_rules():
    ops = make_statement({date(2026, 3, 2): [op(-1, 100.0, "Аренда помещения", inn="0042")]})
    out = categorize(ops, user_rules={"0042": "services"})
    assert out["category"].iloc[0] == "services"
    assert out["category_source"].iloc[0] == "user"


def test_memory_fills_empty_purpose_for_unambiguous_counterparty():
    days = {date(2026, 3, 2 + i): [op(-1, 100.0, "Оплата по счету за доставку воды", inn="0077")] for i in range(3)}
    days[date(2026, 3, 9)] = [op(-1, 100.0, "", inn="0077")]
    out = categorize(make_statement(days))
    last = out.iloc[-1]
    assert (last["category"], last["category_source"]) == ("suppliers", "memory")
    assert last["category_confidence"] >= 0.95


def test_memory_ignores_ambiguous_counterparty():
    days = {date(2026, 3, 2): [op(-1, 1.0, "Аренда", inn="0077")],
            date(2026, 3, 3): [op(-1, 1.0, "Оплата рекламных услуг", inn="0077")],
            date(2026, 3, 4): [op(-1, 1.0, "Услуги уборки", inn="0077")],
            date(2026, 3, 5): [op(-1, 1.0, "", inn="0077")]}
    assert categorize(make_statement(days)).iloc[-1]["category"] == "unknown_out"


def test_taxonomy_groups():
    assert "salary" in MANDATORY and "tax_enp" in MANDATORY and "suppliers" not in MANDATORY
    assert {"owner_injection", "loan_in", "capex", "refund_in", "tax_enforced"} <= EXCLUDED_FROM_MODEL
    assert "loan_repayment" not in EXCLUDED_FROM_MODEL and "leasing" not in EXCLUDED_FROM_MODEL


@needs_dataset
@pytest.mark.parametrize("company", ["C064", "C103", "C107"])
def test_coverage_on_demo_companies(company):
    from app.engine.io import load_statement
    from app.engine.normalize import normalize

    out = categorize(normalize(load_statement(RAW_DIR, company)))
    assert (out["category_source"] == "unknown").mean() <= 0.01        # покрытие ≥99%
    assert out["category"].isin(TAXONOMY).all()
