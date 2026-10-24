"""Режим жюри: любая компания датасета открывается как отдельный предприниматель, выписка грузится при первом входе."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import Business, BusinessContext, ImportBatch, Transaction, User
from app.db.session import init_db, make_engine, make_session_factory
from app.engine.context import Context
from app.main import create_app
from app.schemas import JuryCompany
from app.services import jury_service
from app.services.engine_adapter import save_context
from app.services.personas import PERSONAS, jury_email
from app.services.seed_service import ensure_persona, wipe_business_data
from tests.api.conftest import api_settings
from tests.api.synthetic import synthetic_statement, to_csv_bytes

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

HEADER = "company_id;company_name;company_inn;legal_form;industry;first_operation_date;last_operation_date;operations_count\n"
COMPANIES = [
    ("C201", "ООО «Ромашка»", "0050000201", "ООО", "Торговля"),
    ("C064", "ИП Детский центр", "0050000064", "ИП", "Образование"),
    ("C203", "ООО «Без выписки»", "0050000203", "ООО", "Услуги"),
    ("C204", "ООО «Короткая история»", "0050000204", "ООО", "Услуги"),
    ("C205", "ООО «Со сбоем»", "0050000205", "ООО", "Услуги"),
    ("C206", "ООО «Закрылась в мае»", "0050000206", "ООО", "Услуги"),
    ("C207", "ООО «Работает»", "0050000207", "ООО", "Услуги"),
    ("../etc", "Подделка", "0050000999", "ООО", "Услуги"),
]


@pytest.fixture(scope="module")
def raw(tmp_path_factory) -> Path:
    """Крошечный датасет: справочник и выписки четырёх компаний (у C203 выписки нет, у C204 история короче месяца)."""
    root = tmp_path_factory.mktemp("data") / "raw"
    (root / "statements").mkdir(parents=True)
    lines = [f"{cid};{name};{inn};{form};{industry};2025-01-01;2026-08-31;900\n" for cid, name, inn, form, industry in COMPANIES]
    (root / "companies.csv").write_text(HEADER + "".join(lines), encoding="utf-8-sig")
    for n, cid in enumerate(("C201", "C064", "C205"), start=1):
        (root / "statements" / f"{cid}.csv").write_bytes(to_csv_bytes(synthetic_statement(company=cid, seed=n)))
    short = synthetic_statement(company="C204", start=date(2026, 7, 20), end=date(2026, 8, 5), seed=9)
    (root / "statements" / "C204.csv").write_bytes(to_csv_bytes(short))
    closed = synthetic_statement(company="C206", start=date(2026, 1, 1), end=date(2026, 5, 20), seed=6)
    (root / "statements" / "C206.csv").write_bytes(to_csv_bytes(closed))
    (root / "statements" / "C207.csv").write_bytes(to_csv_bytes(synthetic_statement(company="C207", seed=7)))
    return root


@pytest.fixture(scope="module")
def app(raw):
    settings = api_settings("engine").model_copy(update={"data_dir": raw.parent})
    engine = make_engine(settings.database_url)
    init_db(engine)
    application = create_app(settings, engine)
    application.state.session_factory = make_session_factory(engine)
    return application


@pytest.fixture(scope="module")
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def db_of(app):
    return app.state.session_factory()


# ---------- чистые функции ----------
def _companies() -> list[JuryCompany]:
    return [JuryCompany(company_id=cid, name=name) for cid, name, *_ in [*COMPANIES, ("C109", "Грузоперевозки")]]


def test_list_companies_keeps_only_those_with_a_statement_and_a_safe_id(raw):
    companies = jury_service.list_companies(raw)
    assert [c.company_id for c in companies] == ["C064", "C201", "C204", "C205", "C206", "C207"]          # без выписки и с «../etc» — нет
    romashka = next(c for c in companies if c.company_id == "C201")
    assert (romashka.name, romashka.legal_form, romashka.industry) == ("ООО «Ромашка»", "ООО", "Торговля")
    assert jury_service.list_companies(raw.parent / "нет-такой-папки") == []


def test_choose_as_of_follows_the_demo_date_but_respects_the_statement():
    wanted = date(2026, 7, 31)
    assert jury_service.choose_as_of(date(2024, 10, 1), date(2026, 9, 30), wanted) == wanted
    assert jury_service.choose_as_of(date(2024, 10, 1), date(2026, 5, 20), wanted) == date(2026, 5, 20)    # счёт закрыт раньше
    assert jury_service.choose_as_of(date(2026, 7, 10), date(2026, 9, 30), wanted) == date(2026, 9, 30)    # к 31.07 истории мало
    assert jury_service.choose_as_of(date(2026, 7, 20), date(2026, 8, 5), wanted) is None                  # и всей истории мало


def test_featured_persona_keeps_its_own_account_and_accounting_source():
    oleg = jury_service.jury_persona(next(c for c in _companies() if c.company_id == "C109"))
    assert oleg.email == "oleg@example.com" and oleg.accounting_source == "demo_accounting"
    other = jury_service.jury_persona(next(c for c in _companies() if c.company_id == "C201"))
    assert other.email == jury_email("C201") == "jury-c201@example.com" and other.accounting_source is None


def test_wipe_can_keep_the_stored_context():
    engine = make_engine("sqlite://")
    init_db(engine)
    with make_session_factory(engine)() as db:
        business = ensure_persona(db, PERSONAS[0])
        db.add(BusinessContext(business_id=business.id, as_of=date(2026, 7, 31), payload=b"x"))
        db.commit()
        wipe_business_data(db, business.id, keep_context=True)
        assert db.get(BusinessContext, business.id) is not None
        wipe_business_data(db, business.id)
        assert db.get(BusinessContext, business.id) is None


# ---------- API ----------
def test_company_list_endpoint_and_no_cards_for_jury_users(client):
    listed = client.get("/api/v1/auth/jury-companies")
    assert listed.status_code == 200 and [c["company_id"] for c in listed.json()] == ["C064", "C201", "C204", "C205", "C206", "C207"]
    assert all(set(c) == {"company_id", "name", "legal_form", "industry"} for c in listed.json())


def test_first_login_loads_the_statement_and_the_second_one_reuses_it(client, app):
    first = client.post("/api/v1/auth/jury-login", json={"company_id": "C201"})
    assert first.status_code == 200, first.text
    headers = {"Authorization": f"Bearer {first.json()['access_token']}"}
    me = client.get("/api/v1/me", headers=headers).json()
    assert me["user"]["email"] == "jury-c201@example.com"
    assert [(b["id"], b["name"]) for b in me["businesses"]] == [("C201", "ООО «Ромашка»")]

    business = client.get("/api/v1/businesses/C201", headers=headers).json()
    assert business["data_as_of"] == "2026-07-31" and business["replay"]["enabled"] is True        # хвост выписки ждёт «машину времени»
    forecast = client.get("/api/v1/businesses/C201/forecast", headers=headers)
    assert forecast.status_code == 200 and forecast.json()["as_of"] == "2026-07-31"
    assert client.get("/api/v1/businesses/C064", headers=headers).status_code == 404               # чужие компании недоступны

    with db_of(app) as db:
        batches = db.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.business_id == "C201"))
        rows = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.business_id == "C201"))
    again = client.post("/api/v1/auth/jury-login", json={"company_id": " C201 "})
    assert again.status_code == 200
    with db_of(app) as db:
        assert db.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.business_id == "C201")) == batches == 1
        assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.business_id == "C201")) == rows
        assert db.scalar(select(func.count()).select_from(User).where(User.email == "jury-c201@example.com")) == 1

    cards = client.get("/api/v1/auth/demo-personas").json()
    assert "jury-c201@example.com" not in [c["email"] for c in cards]


def test_main_persona_company_opens_as_that_persona(client):
    response = client.post("/api/v1/auth/jury-login", json={"company_id": "C064"})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == "irina@example.com"
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert client.get("/api/v1/businesses/C064", headers=headers).json()["name"] == "ИП Детский центр"   # имя из справочника
    cards = client.get("/api/v1/auth/demo-personas").json()
    assert [c["email"] for c in cards] == ["irina@example.com"]                    # карточка настоящей персоны на месте


def test_unknown_unsafe_and_too_short_companies_are_refused(client, app):
    assert client.post("/api/v1/auth/jury-login", json={"company_id": "C999"}).status_code == 404
    assert client.post("/api/v1/auth/jury-login", json={"company_id": "../etc"}).status_code == 404
    assert client.post("/api/v1/auth/jury-login", json={"company_id": "C203"}).status_code == 404       # выписки нет
    assert client.post("/api/v1/auth/jury-login", json={"company_id": "x" * 40}).status_code == 422     # слишком длинный id
    short = client.post("/api/v1/auth/jury-login", json={"company_id": "C204"})
    assert short.status_code == 422 and "короткая история" in short.json()["detail"]
    with db_of(app) as db:                                                         # после отказа в БД не остаётся полузагруженного бизнеса
        business = db.get(Business, "C204")
        assert business is None or business.data_as_of is None


def test_failed_forecast_leaves_nothing_behind_and_can_be_retried(client, app, monkeypatch):
    real = jury_service.seed_business

    def broken(db, service, persona, *args, **kwargs):
        real(db, service, persona, *args, **kwargs)          # данные уже в БД, а прогноз «падает»
        raise RuntimeError("движок споткнулся")

    monkeypatch.setattr(jury_service, "seed_business", broken)
    failed = client.post("/api/v1/auth/jury-login", json={"company_id": "C205"})
    assert failed.status_code == 422 and "Не удалось подготовить" in failed.json()["detail"]
    with db_of(app) as db:
        assert (db.scalar(select(func.count()).select_from(Transaction).where(Transaction.business_id == "C205")) or 0) == 0
        assert db.get(Business, "C205").data_as_of is None
    monkeypatch.undo()
    assert client.post("/api/v1/auth/jury-login", json={"company_id": "C205"}).status_code == 200     # повтор проходит


def test_stored_context_from_after_the_cutoff_is_dropped_but_a_fitting_one_is_kept(client, app):
    for company_id in ("C206", "C207"):                                                # как после `seed_demo.py --jury`: контекст на 31.07
        company = next(c for c in _companies() if c.company_id == company_id)
        with db_of(app) as db:
            business = ensure_persona(db, jury_service.jury_persona(company))
            save_context(db, business.id, date(2026, 7, 31), Context())
    assert client.post("/api/v1/auth/jury-login", json={"company_id": "C206"}).status_code == 200
    assert client.post("/api/v1/auth/jury-login", json={"company_id": "C207"}).status_code == 200
    with db_of(app) as db:
        closed, alive = db.get(Business, "C206"), db.get(Business, "C207")
        assert closed.data_as_of == date(2026, 5, 20) and db.get(BusinessContext, "C206") is None     # знал бы о данных из будущего
        assert alive.data_as_of == date(2026, 7, 31) and db.get(BusinessContext, "C207") is not None


def test_jury_mode_exists_only_in_demo_mode(raw):
    settings = api_settings("engine").model_copy(update={"data_dir": raw.parent, "demo_mode": False})
    engine = make_engine(settings.database_url)
    init_db(engine)
    application = create_app(settings, engine)
    with TestClient(application) as client:
        assert client.get("/api/v1/auth/jury-companies").status_code == 404
        assert client.post("/api/v1/auth/jury-login", json={"company_id": "C201"}).status_code == 404
