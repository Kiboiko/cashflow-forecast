"""Демо-пользователи, их бизнесы и выписки (персоны §15.2). Ввод-вывод (чтение датасета) — в `scripts/seed_demo.py`.

Выписка грузится целиком, но бизнес «видит» её только до `as_of` (31.07.2026): остальное подгружает машина времени.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    Account,
    Alert,
    Business,
    BusinessContext,
    CounterpartyRule,
    DataGap,
    ExpectedEvent,
    ForecastSnapshot,
    ImportBatch,
    Transaction,
    User,
)
from app.engine.alerts import build_alerts
from app.engine.context import Context
from app.security import hash_password
from app.services import alert_service, import_service
from app.services.business_service import business_settings
from app.services.engine_adapter import save_context
from app.services.forecast_service import ForecastService
from app.services.personas import DEMO_PASSWORD, PERSONAS, Persona


@dataclass
class SeedReport:
    persona: Persona
    business: Business
    rows: int
    risk_level: str
    gap_probability: float
    headline: str
    alerts: int


def _company_row(companies: pd.DataFrame | None, company_id: str) -> dict:
    if companies is None:
        return {}
    rows = companies[companies["company_id"] == company_id]
    return rows.iloc[0].to_dict() if len(rows) else {}


def _text(value: object, default: str | None = None) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return default
    text = str(value).strip()
    return text or default


def ensure_persona(db: Session, persona: Persona, companies: pd.DataFrame | None = None) -> Business:
    """Создаёт (или обновляет) пользователя-персону и его бизнес. Данные компании — из `companies.csv`, если он есть."""
    row = _company_row(companies, persona.company_id)
    user = db.scalar(select(User).where(User.email == persona.email))
    if user is None:
        user = User(email=persona.email, password_hash=hash_password(DEMO_PASSWORD), display_name=persona.display_name)
        db.add(user)
    user.is_demo = True
    user.persona_note = persona.note
    db.flush()

    business = db.get(Business, persona.company_id)
    if business is None:
        business = Business(id=persona.company_id, owner_user_id=user.id, name=persona.fallback_name)
        db.add(business)
    business.owner_user_id = user.id
    business.name = _text(row.get("company_name")) or _text(row.get("name")) or persona.fallback_name
    business.inn = _text(row.get("company_inn"), business.inn)
    business.legal_form = _text(row.get("legal_form"), persona.fallback_legal_form)
    business.industry = _text(row.get("industry"), persona.fallback_industry)
    db.commit()
    return business


def ensure_personas(db: Session, companies: pd.DataFrame | None = None) -> list[Business]:
    return [ensure_persona(db, persona, companies) for persona in PERSONAS]


def wipe_business_data(db: Session, business_id: str) -> None:
    """Стирает всё, что относится к бизнесу, кроме самого бизнеса и пользователя (для `--reset`)."""
    db.execute(delete(ExpectedEvent).where(ExpectedEvent.business_id == business_id))
    for model in (Alert, ForecastSnapshot, DataGap, CounterpartyRule, Transaction, ImportBatch, Account):
        db.execute(delete(model).where(model.business_id == business_id))
    db.execute(delete(BusinessContext).where(BusinessContext.business_id == business_id))
    business = db.get(Business, business_id)
    business.data_as_of, business.accounting_source = None, None
    business.data_version += 1
    db.commit()


def seed_business(db: Session, service: ForecastService, persona: Persona, statement: bytes, as_of: date,
                  companies: pd.DataFrame | None = None, context: Context | None = None) -> SeedReport:
    """Один бизнес: выписка целиком → видимость до `as_of` → снимок прогноза и первые предупреждения."""
    business = ensure_persona(db, persona, companies)
    wipe_business_data(db, business.id)
    if context is not None:                                    # контекст нужен до первой сборки состояния движка
        save_context(db, business.id, as_of, context)
    frame = import_service.parse_dataset_csv(statement)
    import_service.import_statement(db, business, service, frame, f"{persona.company_id}.csv", source="seed")
    business.data_as_of = as_of
    business.accounting_source = persona.accounting_source
    business.data_version += 1
    db.commit()
    service.clear()

    result = service.result(db, business, business_settings(business).horizon_days)
    service.ensure_snapshot(db, business)
    state = service.state(db, business)
    drafts = build_alerts(result, state.ops, None, [])
    stored = service.publish_alerts(db, business, drafts, alert_service.virtual_now(as_of))
    rows = db.query(Transaction).filter(Transaction.business_id == business.id).count()
    return SeedReport(persona, business, rows, result.risk.level, result.risk.gap_probability, result.risk.headline, len(stored))
