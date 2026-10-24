"""Посев демо-персон на синтетических выписках: пользователи, видимость до 31.07, снимок, предупреждения, пересев."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import func, select

from app.db.models import Alert, Business, ForecastSnapshot, Transaction, User
from app.db.session import init_db, make_engine, make_session_factory
from app.services.forecast_service import ForecastService
from app.services.personas import PERSONAS
from app.services.seed_service import ensure_personas, seed_business
from tests.api.conftest import api_settings
from tests.api.synthetic import synthetic_statement, to_csv_bytes

AS_OF = date(2026, 7, 31)


@pytest.fixture(scope="module")
def seeded():
    settings = api_settings("engine")
    engine = make_engine("sqlite://")
    init_db(engine)
    service = ForecastService(settings)
    db = make_session_factory(engine)()
    reports = []
    for n, persona in enumerate(PERSONAS):
        statement = to_csv_bytes(synthetic_statement(company=persona.company_id, seed=n + 1, crunch=n == 0))
        reports.append(seed_business(db, service, persona, statement, AS_OF))
    yield db, service, reports
    db.close()


def test_personas_are_created_with_data(seeded):
    db, _, reports = seeded
    assert [r.business.id for r in reports] == ["C064", "C109", "C107"]
    assert db.scalar(select(func.count()).select_from(User).where(User.is_demo.is_(True))) == 3
    for report in reports:
        assert report.rows > 800 and report.business.data_as_of == AS_OF


def test_everything_after_cutoff_is_loaded_but_hidden(seeded):
    db, _, _ = seeded
    last = db.scalar(select(func.max(Transaction.date)).where(Transaction.business_id == "C064"))
    assert last == date(2026, 8, 31)
    assert db.get(Business, "C064").data_as_of == AS_OF


def test_demo_accounting_is_only_for_oleg(seeded):
    db, _, _ = seeded
    assert {b.id: b.accounting_source for b in db.scalars(select(Business))} == {"C064": None, "C109": "demo_accounting", "C107": None}


def test_first_snapshot_and_alerts_exist(seeded):
    db, _, reports = seeded
    irina = next(r for r in reports if r.business.id == "C064")
    assert irina.risk_level in {"yellow", "red"} and irina.alerts >= 1
    snapshots = db.scalars(select(ForecastSnapshot).where(ForecastSnapshot.business_id == "C064")).all()
    assert [s.as_of for s in snapshots] == [AS_OF]
    alerts = db.scalars(select(Alert).where(Alert.business_id == "C064")).all()
    assert all(a.created_at.date() == AS_OF for a in alerts)


def test_reseed_is_clean_and_idempotent(seeded):
    db, service, _ = seeded
    persona = PERSONAS[0]
    before = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.business_id == "C064"))
    statement = to_csv_bytes(synthetic_statement(company="C064", seed=1, crunch=True))
    seed_business(db, service, persona, statement, AS_OF)
    after = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.business_id == "C064"))
    assert after == before
    assert db.scalar(select(func.count()).select_from(ForecastSnapshot).where(ForecastSnapshot.business_id == "C064")) == 1
    assert db.scalar(select(func.count()).select_from(User).where(User.email == persona.email)) == 1


def test_warm_up_precomputes_demo_forecasts(seeded):
    db, service, _ = seeded
    service.clear()
    assert service.warm_up(make_session_factory(db.get_bind())) == 3
    business = db.get(Business, "C064")
    first = service.result(db, business, 30)
    assert service.result(db, business, 30) is first                    # уже в кэше: повторного расчёта нет


def test_personas_without_dataset():
    engine = make_engine("sqlite://")
    init_db(engine)
    db = make_session_factory(engine)()
    businesses = ensure_personas(db)
    assert [b.id for b in businesses] == ["C064", "C109", "C107"]
    assert all(b.data_as_of is None for b in businesses)
    db.close()
