"""План (события, счета, договоры), «Вам должны», сценарии, план-факт, предупреждения и машина времени — на настоящем движке."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import Business, Transaction
from app.schemas import (
    EventOut,
    EventsImportResult,
    PlanFactResponse,
    ReceivablesResponse,
    RecurringOut,
    ReplayResult,
    ScenarioPresets,
    ScenarioResponse,
)
from tests.api.conftest import api_settings, auth_headers, build_env, make_business, make_user

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

INVOICES_CSV = (
    "doc_type;doc_no;doc_date;direction;counterparty_inn;counterparty_name;amount;due_date\n"
    "invoice;9001;2026-08-10;in;0030000001;ООО «Клиент-1»;300000;\n"
    "invoice;9002;2026-08-12;in;0030000002;ООО «Клиент-2»;120000;\n"
    "invoice;7001;2026-08-14;out;0020000001;ООО «Поставщик-1»;80000;2026-09-15\n"
).encode("utf-8-sig")


def _events(env) -> list[EventOut]:
    return [EventOut.model_validate(e) for e in env.client.get(env.url("/events"), headers=env.headers).json()]


def _forecast_ids(env) -> dict[str, dict]:
    forecast = env.client.get(env.url("/forecast"), headers=env.headers).json()
    return {e["id"]: e for e in forecast["events"]}


# ---------- события ----------
def test_manual_event_lifecycle(fresh_env):
    env = fresh_env
    body = {"title": "Клиент обещал оплатить", "direction": "in", "amount": 300000, "date": "2026-09-20",
            "counterparty_name": "ООО «Ромашка»", "probability": 0.8, "mandatory": False}
    created = env.client.post(env.url("/events"), headers=env.headers, json=body)
    assert created.status_code == 201, created.text
    event = EventOut.model_validate(created.json())
    assert event.kind == "manual" and event.source == "manual" and event.status == "planned"

    plan = _forecast_ids(env)[f"manual:{event.id}"]
    assert plan["source"] == "manual" and plan["amount"] == 300000 and plan["direction"] == "in"

    edited = env.client.patch(env.url(f"/events/{event.id}"), headers=env.headers, json={"amount": 250000, "date": "2026-09-22"})
    assert edited.status_code == 200 and edited.json()["amount"] == 250000
    plan = _forecast_ids(env)[f"manual:{event.id}"]
    assert plan["amount"] == 250000 and plan["date"] == "2026-09-22"

    assert env.client.delete(env.url(f"/events/{event.id}"), headers=env.headers).status_code == 204
    assert f"manual:{event.id}" not in _forecast_ids(env)
    assert _events(env) == []
    assert env.client.delete(env.url(f"/events/{event.id}"), headers=env.headers).status_code == 404


def test_manual_event_changes_the_forecast(fresh_env):
    env = fresh_env
    before = env.client.get(env.url("/forecast"), headers=env.headers).json()
    env.client.post(env.url("/events"), headers=env.headers, json={
        "title": "Крупная выплата", "direction": "out", "amount": 900000, "date": "2026-09-10", "probability": 1.0, "mandatory": True})
    after = env.client.get(env.url("/forecast"), headers=env.headers).json()
    assert after["daily"][-1]["p50"] < before["daily"][-1]["p50"] - 800000


def test_event_validation(fresh_env):
    env = fresh_env
    past = env.client.post(env.url("/events"), headers=env.headers, json={
        "title": "Было", "direction": "in", "amount": 1000, "date": "2026-08-01"})
    assert past.status_code == 422 and "позже даты данных" in past.json()["detail"]
    assert env.client.post(env.url("/events"), headers=env.headers, json={
        "title": "x", "direction": "in", "amount": -5, "date": "2026-09-20"}).status_code == 422
    assert env.client.post(env.url("/events"), headers=env.headers, json={
        "title": "x", "direction": "sideways", "amount": 5, "date": "2026-09-20"}).status_code == 422
    assert env.client.patch(env.url("/events/404"), headers=env.headers, json={"amount": 5}).status_code == 404


def test_invoices_csv_import_and_receivables(fresh_env):
    env = fresh_env
    empty = ReceivablesResponse.model_validate(env.client.get(env.url("/receivables"), headers=env.headers).json())
    assert empty.connected is False and empty.rows == []

    upload = env.client.post(env.url("/events/import"), headers=env.headers, files={"file": ("invoices.csv", INVOICES_CSV, "text/csv")})
    assert upload.status_code == 200, upload.text
    result = EventsImportResult.model_validate(upload.json())
    assert (result.kind, result.added, result.skipped, result.accounting_source) == ("invoices", 3, 0, "csv")
    again = env.client.post(env.url("/events/import"), headers=env.headers, files={"file": ("invoices.csv", INVOICES_CSV, "text/csv")})
    assert again.json()["added"] == 0 and again.json()["skipped"] == 3

    owed = ReceivablesResponse.model_validate(env.client.get(env.url("/receivables?direction=in"), headers=env.headers).json())
    assert owed.connected and owed.source == "csv" and owed.source_label == "Файл со счетами"
    assert owed.total == pytest.approx(420000) and {r.counterparty_inn for r in owed.rows} == {"0030000001", "0030000002"}
    first = owed.rows[0]
    assert first.counterparty == "ООО «Клиент-1»" and first.typical_delay_days > 0 and first.invoices[0].expected_from <= first.invoices[0].expected_to
    owe = ReceivablesResponse.model_validate(env.client.get(env.url("/receivables?direction=out"), headers=env.headers).json())
    assert owe.total == pytest.approx(80000)

    forecast = env.client.get(env.url("/forecast"), headers=env.headers).json()
    sources = {e["source"] for e in forecast["events"]}
    assert "csv" in sources                                              # источник виден у каждого события (★4)
    assert "invoices" in forecast["model"]["components"]


def test_paid_invoice_is_not_open(fresh_env):
    env = fresh_env
    # счёт, который выписка уже показывает оплаченным: берём реальную оплату клиента из синтетической выписки
    paid = env.db.scalars(select(Transaction).where(Transaction.category == "client_payment", Transaction.doc_no.is_not(None))).first()
    assert paid is not None
    csv = ("doc_type;doc_no;doc_date;direction;counterparty_inn;counterparty_name;amount;due_date\n"
           f"invoice;{paid.doc_no};{paid.doc_date};in;{paid.counterparty_inn};{paid.counterparty_name};{paid.amount};\n").encode("utf-8-sig")
    assert env.client.post(env.url("/events/import"), headers=env.headers, files={"file": ("p.csv", csv, "text/csv")}).json()["added"] == 1
    owed = env.client.get(env.url("/receivables"), headers=env.headers).json()
    assert owed["connected"] is True and owed["rows"] == []                  # оплачен — «Вам должны» его не показывает


_HEADER = "doc_type;doc_no;doc_date;direction;counterparty_inn;counterparty_name;amount;due_date\n"


def _csv(row: str) -> bytes:
    return (_HEADER + row + "\n").encode()


@pytest.mark.parametrize("content, fragment", [
    (b"", "пустой"),
    (b"doc_no;amount\n1;5\n", "нет столбцов"),
    (_csv("invoice;1;вчера;in;;;5;"), "неверная дата документа"),
    (_csv("invoice;1;2026-08-01;sideways;;;5;"), "направление"),
    (_csv("invoice;1;2026-08-01;in;;;0;"), "сумма"),
])
def test_invoices_csv_errors_reject_the_whole_file(fresh_env, content, fragment):
    response = fresh_env.client.post(fresh_env.url("/events/import"), headers=fresh_env.headers,
                                     files={"file": ("bad.csv", content, "text/csv")})
    assert response.status_code == 422 and fragment in response.json()["detail"]
    assert fresh_env.client.get(fresh_env.url("/events"), headers=fresh_env.headers).json() == []


def test_contracts_csv_become_planned_flows(fresh_env):
    env = fresh_env
    csv = ("contract_no;date;amount;direction;counterparty_inn;counterparty_name\n"
           "К-12;2026-09-15;500000;in;0030000003;ООО «Клиент-3»\n").encode("utf-8-sig")
    result = env.client.post(env.url("/events/import"), headers=env.headers, files={"file": ("c.csv", csv, "text/csv")}).json()
    assert result["kind"] == "contracts" and result["added"] == 1
    contract = next(e for e in _forecast_ids(env).values() if e["source"] == "contract")
    assert contract["amount"] == 500000 and contract["date"] == "2026-09-15" and "Договор" in contract["explain"]


# ---------- регулярные платежи и сценарии ----------
def test_recurring_streams(env):
    streams = [RecurringOut.model_validate(r) for r in env.client.get(env.url("/recurring"), headers=env.headers).json()]
    by_title = {s.title: s for s in streams}
    assert {"Аванс по зарплате", "Расчёт по зарплате", "Аренда"} <= set(by_title)
    assert by_title["Аренда"].schedule == "каждый месяц 8-го" and by_title["Аренда"].mandatory and by_title["Аренда"].active
    assert by_title["Аванс по зарплате"].schedule == "каждый месяц 25-го"


def test_scenario_presets_and_run(env):
    presets = ScenarioPresets.model_validate(env.client.get(env.url("/scenarios/presets"), headers=env.headers).json())
    ids = [p.id for p in presets.presets]
    assert ids[0] == "client_delay" and {"revenue_drop", "one_off_expense", "fixed_cost_increase"} <= set(ids)
    assert "Клиент" in presets.presets[0].description
    drop = next(p for p in presets.presets if p.id == "revenue_drop")
    response = env.client.post(env.url("/scenarios/run"), headers=env.headers, json=drop.request.model_dump(mode="json", by_alias=True))
    assert response.status_code == 200, response.text
    scenario = ScenarioResponse.model_validate(response.json())
    assert len(scenario.base.daily) == len(scenario.scenario.daily) == 30
    assert scenario.scenario.daily[-1].p50 <= scenario.base.daily[-1].p50 + 1.0     # падение выручки не увеличивает P50
    assert scenario.summary.startswith("Если выручка упадёт на 20%")


def test_every_preset_runs(env):
    presets = env.client.get(env.url("/scenarios/presets"), headers=env.headers).json()["presets"]
    for preset in presets:
        response = env.client.post(env.url("/scenarios/run"), headers=env.headers, json=preset["request"])
        assert response.status_code == 200, (preset["id"], response.text)
        assert response.json()["summary"]


def test_scenario_validation(env):
    bad = {"horizon_days": 30, "shocks": [{"type": "revenue_drop", "pct": 250}]}
    assert env.client.post(env.url("/scenarios/run"), headers=env.headers, json=bad).status_code == 422
    unknown = {"horizon_days": 30, "shocks": [{"type": "meteor", "amount": 1}]}
    assert env.client.post(env.url("/scenarios/run"), headers=env.headers, json=unknown).status_code == 422


# ---------- демо «учётная система» ----------
def test_demo_accounting_receivables():
    for env in build_env(data_as_of="2026-07-31"):
        business = env.db.get(Business, "C064")
        business.accounting_source = "demo_accounting"
        business.data_version += 1
        env.db.commit()
        owed = ReceivablesResponse.model_validate(env.client.get(env.url("/receivables"), headers=env.headers).json())
        assert owed.connected and owed.source == "demo_accounting" and "восстановлены из выписки" in owed.source_label
        assert owed.rows and all(r.open_amount > 0 for r in owed.rows)
        forecast = env.client.get(env.url("/forecast"), headers=env.headers).json()
        assert "demo_accounting" in {e["source"] for e in forecast["events"]}


# ---------- план-факт, предупреждения, машина времени ----------
def test_plan_fact_before_any_step_has_only_plan():
    for env in build_env(crunch=True, data_as_of="2026-07-31"):
        fact = PlanFactResponse.model_validate(env.client.get(env.url("/plan-fact"), headers=env.headers).json())
        assert fact.snapshot_as_of == fact.fact_until == date(2026, 7, 31)
        assert all(d.fact_in is None for d in fact.daily) and fact.events == [] and fact.summary.missed_events == 0
        snapshots = env.client.get(env.url("/snapshots"), headers=env.headers).json()
        assert [s["as_of"] for s in snapshots] == ["2026-07-31"]


def test_time_machine_step_plan_fact_and_alerts():
    for env in build_env(crunch=True, data_as_of="2026-07-31"):
        first_forecast = env.client.get(env.url("/forecast"), headers=env.headers).json()
        assert first_forecast["risk"]["level"] in {"yellow", "red"}                  # без риска демо нечего показывать

        step = env.client.post(f"/api/v1/demo/businesses/{env.bid}/replay/advance", headers=env.headers, json={"days": 7})
        assert step.status_code == 200, step.text
        result = ReplayResult.model_validate(step.json())
        assert result.previous_as_of == date(2026, 7, 31) and result.as_of == date(2026, 8, 7)
        assert result.new_operations > 0 and result.can_advance and result.data_until == date(2026, 8, 31)
        assert result.gap_probability_before is not None and result.headline

        forecast = env.client.get(env.url("/forecast"), headers=env.headers).json()
        assert forecast["as_of"] == "2026-08-07" and forecast["daily"][0]["date"] == "2026-08-08"
        page = env.client.get(env.url("/transactions?limit=1"), headers=env.headers).json()
        assert page["items"][0]["date"] <= "2026-08-07"
        new_rows = env.client.get(env.url("/transactions?from=2026-08-01&to=2026-08-07&limit=500"), headers=env.headers).json()["items"]
        assert new_rows and all(t["category"] for t in new_rows if "duplicate" not in t["flags"])   # новые операции категоризованы

        fact = PlanFactResponse.model_validate(env.client.get(env.url("/plan-fact"), headers=env.headers).json())
        assert fact.snapshot_as_of == date(2026, 7, 31) and fact.fact_until == date(2026, 8, 7)
        with_fact = [d for d in fact.daily if d.fact_balance is not None]
        assert len(with_fact) == 7 and all(d.date <= date(2026, 8, 7) for d in with_fact)
        snapshots = env.client.get(env.url("/snapshots"), headers=env.headers).json()
        assert [s["as_of"] for s in snapshots] == ["2026-07-31", "2026-08-07"]
        old = env.client.get(env.url(f"/plan-fact?snapshot_id={snapshots[-1]['id']}"), headers=env.headers).json()
        assert old["snapshot_as_of"] == "2026-08-07"

        # второй шаг, как в сценарии демо («+7 дней» дважды): аренда и зарплата 10 августа уже должны быть в факте
        second = env.client.post(f"/api/v1/demo/businesses/{env.bid}/replay/advance", headers=env.headers, json={"days": 7}).json()
        assert second["as_of"] == "2026-08-14"
        fact = PlanFactResponse.model_validate(env.client.get(env.url("/plan-fact"), headers=env.headers).json())
        assert fact.snapshot_as_of == date(2026, 7, 31) and fact.fact_until == date(2026, 8, 14)   # накопленно с начала демо
        done = {e.title: e for e in fact.events if e.status == "fulfilled"}
        assert "Аренда" in done and done["Аренда"].matched_transaction_id is not None
        assert "Расчёт по зарплате" in done

        alerts = env.client.get(env.url("/alerts?status=open"), headers=env.headers).json()
        assert alerts and all(a["acknowledged_at"] is None for a in alerts)
        assert {a["type"] for a in alerts} <= {"gap_risk", "mandatory_at_risk", "missed_inflow", "overdue_invoice", "data_gap", "needs_review"}
        assert {a["created_at"][:10] for a in alerts} <= {"2026-08-07", "2026-08-14"}     # виртуальное время, а не сегодняшнее
        acked = env.client.post(env.url(f"/alerts/{alerts[0]['id']}/ack"), headers=env.headers).json()
        assert acked["acknowledged_at"] is not None
        assert len(env.client.get(env.url("/alerts?status=open"), headers=env.headers).json()) == len(alerts) - 1
        assert len(env.client.get(env.url("/alerts?status=all"), headers=env.headers).json()) >= len(alerts)
        assert env.client.post(env.url("/alerts/999999/ack"), headers=env.headers).status_code == 404
        assert env.client.get(env.url("/alerts?status=weird"), headers=env.headers).status_code == 422


def test_time_machine_reaches_the_end_and_resets():
    for env in build_env(crunch=True, data_as_of="2026-07-31"):
        url = f"/api/v1/demo/businesses/{env.bid}/replay"
        for _ in range(3):
            last = env.client.post(f"{url}/advance", headers=env.headers, json={"days": 14}).json()
        assert last["as_of"] == "2026-08-31" and last["can_advance"] is False
        end = env.client.post(f"{url}/advance", headers=env.headers, json={"days": 1})
        assert end.status_code == 409 and "Данные закончились" in end.json()["detail"]
        assert env.client.post(f"{url}/advance", headers=env.headers, json={"days": 30}).status_code == 422

        reset = env.client.post(f"{url}/reset", headers=env.headers, json={})
        assert reset.status_code == 200 and reset.json()["as_of"] == "2026-07-31" and reset.json()["can_advance"] is True
        snapshots = env.client.get(env.url("/snapshots"), headers=env.headers).json()
        assert [s["as_of"] for s in snapshots] == ["2026-07-31"]
        alerts = env.client.get(env.url("/alerts?status=all"), headers=env.headers).json()
        assert all(a["created_at"] <= "2026-08-01" for a in alerts)
        forecast = env.client.get(env.url("/forecast"), headers=env.headers).json()
        assert forecast["as_of"] == "2026-07-31"
        assert env.client.post(f"{url}/reset", headers=env.headers, json={"as_of": "2030-01-01"}).status_code == 422


def test_replay_is_demo_only_and_isolated():
    for env in build_env(data_as_of="2026-07-31"):
        url = f"/api/v1/demo/businesses/{env.bid}/replay/advance"
        make_user(env.db, "oleg@example.com", "Олег")
        other = auth_headers(env.client, "oleg@example.com")
        assert env.client.post(url, headers=other, json={"days": 1}).status_code == 404
        assert env.client.get(env.url("/plan-fact"), headers=other).status_code == 404
        assert env.client.get(env.url("/alerts"), headers=other).status_code == 404
        env.app.state.settings.demo_mode = False
        assert env.client.post(url, headers=env.headers, json={"days": 1}).status_code == 404


def test_backtest_report_endpoint(env):
    response = env.client.get("/api/v1/quality/backtest", headers=env.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["file"].startswith("backtest_") and "test" in body["report"] and "approaches" in body["report"]["test"]
    assert env.client.get("/api/v1/quality/backtest").status_code == 401


def test_backtest_report_missing(tmp_path):
    from fastapi.testclient import TestClient

    from app.db.session import init_db, make_engine, make_session_factory
    from app.main import create_app
    settings = api_settings("fixture")
    settings.reports_dir = tmp_path
    engine = make_engine("sqlite://")
    init_db(engine)
    app = create_app(settings, engine)
    app.state.session_factory = make_session_factory(engine)
    db = app.state.session_factory()
    make_business(db, make_user(db, "a@example.com", "А"), "B1")
    with TestClient(app) as client:
        headers = auth_headers(client, "a@example.com")
        assert client.get("/api/v1/quality/backtest", headers=headers).status_code == 404
