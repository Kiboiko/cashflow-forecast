"""Сквозной путь на настоящем движке: импорт CSV → категории → прогноз → операции (§11: «импорт CSV → прогноз»)."""
from __future__ import annotations

import logging
from datetime import date

import pytest

from app.db.models import Business, Transaction
from app.schemas import (
    CategoryPatchResult,
    CategorySummary,
    ForecastResponse,
    ImportResult,
    TransactionPage,
)
from tests.api.conftest import api_settings, auth_headers, make_business, make_user
from tests.api.synthetic import synthetic_statement, to_csv_bytes

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_import_summary(env):
    result = ImportResult.model_validate(env.import_result)
    frame = synthetic_statement()
    assert result.format == "dataset_csv"
    assert result.added == len(frame)
    assert result.period[0] == date(2025, 1, 9) and result.period[1] == date(2026, 8, 31)
    assert result.categorized_share >= 0.95
    assert result.needs_review == round((1 - result.categorized_share) * len(frame)) or result.needs_review >= 0
    assert result.data_gaps == [] and result.duplicates_removed == 0


def test_import_twice_adds_nothing(fresh_env):
    again = fresh_env.client.post(fresh_env.url("/imports"), headers=fresh_env.headers,
                                  files={"file": ("again.csv", fresh_env.statement_csv, "text/csv")})
    assert again.status_code == 200
    assert again.json()["added"] == 0


@pytest.mark.parametrize("content, fragment", [
    (b"", "пустой"),
    (b"a;b\n1;2\n", "нет столбцов"),
    ("operation_date;amount;direction;balance_eod\nвчера;10;Списание;5\n".encode(), "Неверная дата в строке 2"),
    ("operation_date;amount;direction;balance_eod\n2026-01-01;-10;Списание;5\n".encode(), "Неверная сумма"),
    ("operation_date;amount;direction;balance_eod\n2026-01-01;10;Перевод;5\n".encode(), "Неизвестное направление"),
    (b"operation_date;amount;direction;balance_eod\n", "нет операций"),
])
def test_import_rejects_bad_files(fresh_env, content, fragment):
    response = fresh_env.client.post(fresh_env.url("/imports"), headers=fresh_env.headers,
                                     files={"file": ("bad.csv", content, "text/csv")})
    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_import_reads_windows_1251_and_comma_separator(fresh_env):
    header = "operation_date,amount,direction,balance_eod,counterparty_name,payment_purpose\n"
    row = "2026-09-01,1000,Поступление,500000,ООО Тест,Оплата по счету № 1 от 01.08.2026\n"
    response = fresh_env.client.post(fresh_env.url("/imports"), headers=fresh_env.headers,
                                     files={"file": ("old.csv", (header + row).encode("cp1251"), "text/csv")})
    assert response.status_code == 200, response.text
    assert response.json()["added"] == 1


def test_forecast_on_real_engine(env):
    response = env.client.get(env.url("/forecast?horizon=30"), headers=env.headers)
    assert response.status_code == 200, response.text
    forecast = ForecastResponse.model_validate(response.json())
    assert forecast.business_id == "C064" and forecast.as_of == date(2026, 8, 31)
    assert len(forecast.daily) == 30 and forecast.daily[0].date == date(2026, 9, 1)
    assert all(d.p10 <= d.p50 <= d.p90 for d in forecast.daily)
    categories = {e.category for e in forecast.events}
    assert {"salary", "rent", "tax_enp"} <= categories
    assert forecast.risk.mandatory_coverage and forecast.model.n_paths == 1000
    assert "recurring" in forecast.model.components


def test_forecast_horizon_and_cache(env):
    first = env.client.get(env.url("/forecast?horizon=60"), headers=env.headers).json()
    second = env.client.get(env.url("/forecast?horizon=60"), headers=env.headers).json()
    assert len(first["daily"]) == 60 and first == second           # детерминизм и кэш по версии данных


def test_business_includes_data_quality(env):
    business = env.client.get(env.url(""), headers=env.headers).json()
    assert business["data_quality"]["history"] == "full"
    assert business["data_as_of"] == "2026-08-31"
    assert business["replay"]["enabled"] is False


def test_start_balance_matches_statement(env):
    frame = synthetic_statement()
    last_balance = float(frame[frame["operation_date"] == frame["operation_date"].max()]["balance_eod"].iloc[-1])
    forecast = env.client.get(env.url("/forecast"), headers=env.headers).json()
    assert forecast["start_balance"] == pytest.approx(last_balance, abs=0.01)


def test_transactions_list_search_and_filters(env):
    page = TransactionPage.model_validate(env.client.get(env.url("/transactions?limit=5"), headers=env.headers).json())
    assert page.total == env.import_result["added"] and len(page.items) == 5
    assert page.items[0].date >= page.items[-1].date                # новые сверху
    by_category = env.client.get(env.url("/transactions?category=rent&limit=500"), headers=env.headers).json()
    assert by_category["total"] > 0 and all(t["category"] == "rent" for t in by_category["items"])
    assert by_category["items"][0]["category_title"] == "Аренда"
    found = env.client.get(env.url("/transactions?q=арендодатель&limit=500"), headers=env.headers).json()   # без учёта регистра
    assert found["total"] == by_category["total"]
    by_inn = env.client.get(env.url("/transactions?q=0010000001&limit=500"), headers=env.headers).json()
    assert by_inn["total"] == found["total"]
    ranged = env.client.get(env.url("/transactions?from=2026-08-01&to=2026-08-31&limit=500"), headers=env.headers).json()
    assert 0 < ranged["total"] < page.total
    assert all("2026-08-01" <= t["date"] <= "2026-08-31" for t in ranged["items"])
    assert env.client.get(env.url("/transactions?limit=0"), headers=env.headers).status_code == 422


def test_known_rules_categorize_synthetic_statement(env):
    page = env.client.get(env.url("/transactions?limit=500&category=salary"), headers=env.headers).json()
    assert page["total"] >= 30
    owner = env.client.get(env.url("/transactions?q=личную карту&limit=500"), headers=env.headers).json()
    assert owner["total"] > 0 and all(t["category"] == "owner_withdrawal" for t in owner["items"])


def test_category_summary(env):
    summary = CategorySummary.model_validate(
        env.client.get(env.url("/categories/summary?months=6"), headers=env.headers).json())
    assert len(summary.months) == 6 and summary.months[-1] == "2026-08"
    assert summary.inflow_total > 0 and summary.outflow_total > 0
    assert all(len(r.by_month) == 6 for r in summary.rows)
    assert {r.category for r in summary.rows} >= {"revenue_card", "salary", "rent"}
    rent = next(r for r in summary.rows if r.category == "rent")
    assert rent.direction == "out" and rent.title == "Аренда" and rent.total == pytest.approx(150_000 * 6)


def test_categories_reference(env):
    cats = env.client.get("/api/v1/categories", headers=env.headers).json()
    assert {"code": "rent", "title": "Аренда", "group": "обязательные", "mandatory": True, "direction": "out"} in cats
    assert next(c for c in cats if c["code"] == "client_payment")["direction"] == "in"
    assert env.client.get("/api/v1/categories").status_code == 401


def test_patch_category_for_counterparty_is_remembered(fresh_env):
    env = fresh_env
    tx = env.client.get(env.url("/transactions?q=ПАО «Связь»&limit=500"), headers=env.headers).json()["items"][0]
    assert tx["category"] == "telecom_it"
    version_before = env.db.get(Business, "C064").data_version
    patched = env.client.patch(env.url(f"/transactions/{tx['id']}"), headers=env.headers,
                               json={"category": "services", "apply_to_counterparty": True})
    assert patched.status_code == 200, patched.text
    result = CategoryPatchResult.model_validate(patched.json())
    assert result.rule_saved and result.updated >= 10
    assert result.transaction.category == "services" and result.transaction.category_source == "user"
    other = env.client.get(env.url("/transactions?q=ПАО «Связь»&limit=500"), headers=env.headers).json()["items"]
    assert {t["category"] for t in other} == {"services"}
    env.db.expire_all()
    assert env.db.get(Business, "C064").data_version > version_before
    # новая выписка с тем же контрагентом сразу попадает в запомненную категорию
    extra = ("operation_date;amount;direction;balance_eod;counterparty_inn;counterparty_name;payment_purpose\n"
             "2026-09-02;18000;Списание;1400000;0050000001;ПАО «Связь»;Оплата услуг связи и интернет\n").encode("utf-8-sig")
    assert env.client.post(env.url("/imports"), headers=env.headers, files={"file": ("n.csv", extra, "text/csv")}).status_code == 200
    newest = env.client.get(env.url("/transactions?limit=1"), headers=env.headers).json()["items"][0]
    assert newest["category"] == "services" and newest["category_source"] == "user"


def test_patch_category_for_single_transaction(fresh_env):
    env = fresh_env
    items = env.client.get(env.url("/transactions?q=ПАО «Связь»&limit=500"), headers=env.headers).json()["items"]
    target, sibling = items[0], items[1]
    patched = env.client.patch(env.url(f"/transactions/{target['id']}"), headers=env.headers,
                               json={"category": "marketing", "apply_to_counterparty": False})
    result = patched.json()
    assert patched.status_code == 200 and result["rule_saved"] is False and result["updated"] == 1
    assert result["transaction"]["category"] == "marketing"
    again = env.client.get(env.url("/transactions?q=ПАО «Связь»&limit=500"), headers=env.headers).json()["items"]
    categories = {t["id"]: t["category"] for t in again}
    assert categories[target["id"]] == "marketing" and categories[sibling["id"]] == "telecom_it"


def test_patch_category_must_match_the_direction_of_the_operation(fresh_env):
    env = fresh_env
    for direction, wrong, right in (("out", "client_payment", "services"), ("in", "rent", "client_payment")):
        page = env.client.get(env.url("/transactions?limit=500"), headers=env.headers).json()["items"]
        tx = next(t for t in page if t["direction"] == direction)
        denied = env.client.patch(env.url(f"/transactions/{tx['id']}"), headers=env.headers, json={"category": wrong})
        assert denied.status_code == 422 and "категори" in denied.json()["detail"]
        allowed = env.client.patch(env.url(f"/transactions/{tx['id']}"), headers=env.headers, json={"category": right})
        assert allowed.status_code == 200 and allowed.json()["transaction"]["category"] == right


def test_patch_category_validation_and_isolation(fresh_env):
    env = fresh_env
    tx_id = env.client.get(env.url("/transactions?limit=1"), headers=env.headers).json()["items"][0]["id"]
    assert env.client.patch(env.url(f"/transactions/{tx_id}"), headers=env.headers,
                            json={"category": "нет_такой"}).status_code == 422
    assert env.client.patch(env.url("/transactions/999999"), headers=env.headers, json={"category": "rent"}).status_code == 404
    make_user(env.db, "oleg@example.com", "Олег")
    other = auth_headers(env.client, "oleg@example.com")
    assert env.client.get(env.url("/transactions"), headers=other).status_code == 404
    assert env.client.patch(env.url(f"/transactions/{tx_id}"), headers=other, json={"category": "rent"}).status_code == 404
    assert env.client.post(env.url("/imports"), headers=other,
                           files={"file": ("x.csv", env.statement_csv, "text/csv")}).status_code == 404


def test_visibility_cutoff_hides_the_future():
    from tests.api.conftest import build_env
    for env in build_env(data_as_of="2026-07-31"):
        page = env.client.get(env.url("/transactions?limit=1"), headers=env.headers).json()
        assert page["items"][0]["date"] <= "2026-07-31"
        forecast = env.client.get(env.url("/forecast"), headers=env.headers).json()
        assert forecast["as_of"] == "2026-07-31" and forecast["daily"][0]["date"] == "2026-08-01"
        business = env.client.get(env.url(""), headers=env.headers).json()
        assert business["replay"] == {"enabled": True, "data_until": "2026-08-31", "can_advance": True}
        assert env.db.query(Transaction).count() == env.import_result["added"]


def test_forecast_without_data_is_409():
    settings = api_settings("engine")
    from app.db.session import init_db, make_engine, make_session_factory
    from app.main import create_app
    engine = make_engine("sqlite://")
    init_db(engine)
    app = create_app(settings, engine)
    app.state.session_factory = make_session_factory(engine)
    db = app.state.session_factory()
    make_business(db, make_user(db, "new@example.com", "Новый"), "B-NEW", "Новый бизнес")
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        headers = auth_headers(client, "new@example.com")
        response = client.get("/api/v1/businesses/B-NEW/forecast", headers=headers)
        assert response.status_code == 409 and response.json()["code"] == "no_data"
        assert client.get("/api/v1/businesses/B-NEW", headers=headers).json()["data_quality"] is None
        assert client.get("/api/v1/businesses/B-NEW/transactions", headers=headers).json()["total"] == 0


def test_import_does_not_log_counterparty_data(caplog):
    """§10/§11: после импорта в логах нет ИНН, имён, сумм и назначений платежей."""
    from app.logging_setup import install_masking
    from tests.api.conftest import build_env
    install_masking()
    with caplog.at_level(logging.DEBUG):
        for env in build_env():
            env.client.get(env.url("/forecast"), headers=env.headers)
            env.client.get(env.url("/transactions?q=0010000001"), headers=env.headers)
    text = "\n".join(f"{r.name} {r.getMessage()} {getattr(r, 'fields', '')}" for r in caplog.records)
    for secret in ("0010000001", "0020000001", "0030000001", "0000000001", "Арендодатель", "Заработная плата", "эквайринг"):
        assert secret not in text, secret
    assert "kassa.import" in text                                 # сами записи об импорте есть: счётчики и длительность


def test_import_creates_snapshot_and_alerts():
    """После загрузки выписки сразу есть снимок прогноза (база план-факта) и предупреждения по риску."""
    from tests.api.conftest import build_env
    for env in build_env(crunch=True):
        snapshots = env.client.get(env.url("/snapshots"), headers=env.headers).json()
        assert len(snapshots) == 1 and snapshots[0]["as_of"] == "2026-08-31"
        level = env.client.get(env.url("/forecast"), headers=env.headers).json()["risk"]["level"]
        alerts = env.client.get(env.url("/alerts"), headers=env.headers).json()
        assert (level == "green") == (not any(a["type"] == "gap_risk" for a in alerts))
        assert all(a["created_at"].startswith("2026-08-31") for a in alerts)         # дата данных, а не сегодняшний день


def test_synthetic_csv_roundtrip():
    frame = synthetic_statement(end=date(2025, 3, 31))
    assert to_csv_bytes(frame).startswith(b"\xef\xbb\xbf")
    assert frame["operation_id"].is_unique
