"""Справочники: ключевая ставка ЦБ (разбор, кэш, запасное значение) и демо-проверка контрагентов по ИНН."""
from __future__ import annotations

import json
import os
import time
from datetime import date

import httpx
import pytest

from app.integrations.cbr_rate import KeyRate, KeyRateClient, parse_key_rate
from app.integrations.counterparty_registry import Check, DemoRegistry
from tests.api.conftest import auth_headers, make_user

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

TODAY = date(2026, 10, 24)
CBR_PAGE = """<table class="data"><tbody><tr><th>Дата</th><th>Ставка</th></tr>
<tr><td>23.10.2026</td>
<td>14,00</td></tr>
<tr><td>22.10.2026</td><td>14,00</td></tr>
<tr><td>09.10.2026</td><td>15,50</td></tr></tbody></table>"""


def answering(text: str, status: int = 200):
    return lambda request: httpx.Response(status, text=text)


def offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("нет сети", request=request)


def cbr(handler, **kwargs) -> KeyRateClient:
    return KeyRateClient(0.14, transport=httpx.MockTransport(handler), **kwargs)


# ---------- ключевая ставка ----------
def test_parse_takes_the_latest_row_whatever_the_order():
    assert parse_key_rate(CBR_PAGE) == (date(2026, 10, 23), pytest.approx(0.14))
    rows = CBR_PAGE.split("\n")
    shuffled = "\n".join([rows[0], rows[3], rows[1], rows[2]])
    assert parse_key_rate(shuffled)[0] == date(2026, 10, 23)
    assert parse_key_rate("<td>01.09.2026</td><td>7,5</td>") == (date(2026, 9, 1), pytest.approx(0.075))


def test_parse_ignores_pages_without_a_table_and_impossible_dates():
    assert parse_key_rate("<html>Технические работы</html>") is None
    assert parse_key_rate("<td>31.02.2026</td><td>14,00</td>") is None
    assert parse_key_rate("") is None


def test_client_reads_the_table_once_and_caches_it():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=CBR_PAGE)

    client = cbr(handler)
    first = client.get(TODAY)
    assert first == KeyRate(pytest.approx(0.14), date(2026, 10, 23), "cbr")
    assert client.get(TODAY) is first and len(calls) == 1
    query = calls[0].url.params                                   # запрос не содержит ничего, кроме периода
    assert dict(query) == {"UniDbQuery.Posted": "True", "UniDbQuery.From": "09.09.2026", "UniDbQuery.To": "24.10.2026"}


def test_client_falls_back_when_cbr_is_down_and_does_not_hammer_it():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return offline(request)

    clock = {"now": 0.0}
    client = cbr(handler, clock=lambda: clock["now"])
    for _ in range(3):
        assert client.get(TODAY) == KeyRate(0.14, None, "fallback")
    assert len(calls) == 1                                        # после неудачи пауза, а не запрос на каждый показ экрана
    clock["now"] = 61
    client.get(TODAY)
    assert len(calls) == 2


def test_client_keeps_the_last_good_value_through_a_later_outage():
    clock = {"now": 0.0}
    state = {"up": True}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=CBR_PAGE) if state["up"] else offline(request)

    client = cbr(handler, ttl=100, clock=lambda: clock["now"])
    good = client.get(TODAY)
    state["up"], clock["now"] = False, 101                        # кэш протух, а ЦБ недоступен
    assert client.get(TODAY) == good and good.source == "cbr"


def test_unreadable_or_error_answers_fall_back():
    for handler in (answering("<html>Технические работы</html>"), answering("упало", status=503)):
        assert cbr(handler).get(TODAY).source == "fallback"


# ---------- проверка контрагентов (демо) ----------
def test_registry_defaults_to_active_and_reads_overrides(tmp_path):
    path = tmp_path / "registry_mock.json"
    path.write_text(json.dumps({
        "0030000001": {"status": "liquidating", "note": "Подана заявка"},
        "0030000002": "bankrupt",
        "0030000003": {"status": "что-то другое"},
    }), encoding="utf-8")
    registry = DemoRegistry(path)
    assert registry.check("0030000001") == Check("0030000001", "liquidating", "Подана заявка")
    assert registry.check("0030000001").title == "Идёт ликвидация"
    assert registry.check("0030000002").status == "bankrupt"
    assert registry.check("0030000003").status == "active"        # неизвестный статус не придумываем
    assert registry.check(" 0099999999 ").status == "active"
    flagged = registry.flagged(["0099999999", "0030000002", "0030000001", "0030000002"])
    assert [c.inn for c in flagged] == ["0030000002", "0030000001"]


def test_registry_survives_missing_or_broken_files_and_sees_edits(tmp_path):
    path = tmp_path / "registry_mock.json"
    registry = DemoRegistry(path)
    assert registry.flagged(["1"]) == [] and DemoRegistry(None).flagged(["1"]) == []
    path.write_text("{не json", encoding="utf-8")
    assert registry.flagged(["1"]) == []
    path.write_text(json.dumps({"1": "unreliable"}), encoding="utf-8")
    later = time.time() + 5
    os.utime(path, (later, later))                                # правка на демо подхватывается без перезапуска
    assert [c.status for c in registry.flagged(["1"])] == ["unreliable"]
    path.write_text(json.dumps(["не", "словарь"]), encoding="utf-8")
    os.utime(path, (later + 5, later + 5))
    assert registry.flagged(["1"]) == []


# ---------- API ----------
def test_key_rate_endpoint(app, client, irina_headers):
    app.state.key_rate = cbr(answering(CBR_PAGE))
    got = client.get("/api/v1/reference/key-rate", headers=irina_headers)
    assert got.status_code == 200
    assert got.json() == {"rate": pytest.approx(0.14), "as_of": "2026-10-23", "source": "cbr"}
    assert client.get("/api/v1/reference/key-rate").status_code == 401


def test_key_rate_endpoint_works_offline(app, client, irina_headers):
    app.state.key_rate = cbr(offline)
    got = client.get("/api/v1/reference/key-rate", headers=irina_headers)
    assert got.status_code == 200 and got.json() == {"rate": 0.14, "as_of": None, "source": "fallback"}


def test_counterparty_checks_endpoint(app, client, db, irina_headers, tmp_path):
    path = tmp_path / "registry_mock.json"
    path.write_text(json.dumps({"0030000001": {"status": "liquidating", "note": "Условный статус для демо"}}), encoding="utf-8")
    app.state.registry = DemoRegistry(path)
    url = "/api/v1/businesses/C064/counterparty-checks"

    got = client.get(url, params=[("inn", "0030000001"), ("inn", "0030000002")], headers=irina_headers)
    assert got.status_code == 200
    assert got.json() == {"source": "demo", "flagged": [
        {"inn": "0030000001", "status": "liquidating", "title": "Идёт ликвидация", "note": "Условный статус для демо"}]}
    assert client.get(url, headers=irina_headers).json() == {"source": "demo", "flagged": []}
    too_many = [("inn", f"{i:010d}") for i in range(201)]
    assert client.get(url, params=too_many, headers=irina_headers).status_code == 422

    make_user(db, "oleg@example.com", "Олег")                     # чужой бизнес и анонимный вызов
    assert client.get(url, headers=auth_headers(client, "oleg@example.com")).status_code == 404
    assert client.get(url).status_code == 401
