"""Контракты API (§8): фикстура прогноза валидна, примеры запросов разбираются."""
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas import Alert, EventCreate, ForecastResponse, ScenarioRequest, Shock

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "forecast_example.json"


def test_forecast_fixture_matches_contract():
    resp = ForecastResponse.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    assert resp.horizon_days == len(resp.daily) >= 30
    assert all(d.p10 <= d.p50 <= d.p90 for d in resp.daily)
    assert resp.risk.level in {"green", "yellow", "red"}
    assert resp.risk.mandatory_coverage and resp.recommendations


def test_scenario_request_example_from_spec():
    req = ScenarioRequest.model_validate({"horizon_days": 30, "shocks": [
        {"type": "client_delay", "counterparty_inn": "0048401219", "delay_days": 21},
        {"type": "revenue_drop", "pct": 20, "from": "2026-08-01"},
        {"type": "one_off_expense", "amount": 150000, "date": "2026-08-12", "title": "Ремонт оборудования"},
        {"type": "fixed_cost_increase", "pct": 10, "categories": ["rent", "suppliers"]},
    ]})
    assert [s.type for s in req.shocks] == ["client_delay", "revenue_drop", "one_off_expense", "fixed_cost_increase"]
    assert req.shocks[1].from_date.isoformat() == "2026-08-01"


def test_unknown_shock_is_rejected():
    with pytest.raises(ValidationError):
        TypeAdapter(Shock).validate_python({"type": "meteor", "pct": 1})


def test_event_create_and_alert_examples_from_spec():
    ev = EventCreate.model_validate({"title": "Клиент обещал оплатить", "direction": "in", "amount": 300000,
                                     "date": "2026-08-20", "counterparty_name": "ООО «Ромашка»",
                                     "probability": 0.8, "mandatory": False})
    assert ev.amount == 300000
    alert = Alert.model_validate({"id": 17, "type": "missed_inflow", "severity": "warning",
                                  "title": "Не пришла оплата 310 000 ₽ от ООО «Клиент-1»", "body": "…",
                                  "created_at": "2026-08-10T09:00:00+03:00", "acknowledged_at": None})
    assert alert.created_at.utcoffset() is not None
