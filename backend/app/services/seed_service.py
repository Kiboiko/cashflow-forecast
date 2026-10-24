"""Демо-пользователи и их бизнесы (персоны §15.2). Выписки и контекст загружает `scripts/seed_demo.py`."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Business, User
from app.security import hash_password
from app.services.personas import DEMO_PASSWORD, PERSONAS, Persona


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
    business.name = _text(row.get("company_name"), None) or _text(row.get("name"), None) or persona.fallback_name
    business.inn = _text(row.get("company_inn"), business.inn)
    business.legal_form = _text(row.get("legal_form"), persona.fallback_legal_form)
    business.industry = _text(row.get("industry"), persona.fallback_industry)
    db.commit()
    return business


def ensure_personas(db: Session, companies: pd.DataFrame | None = None) -> list[Business]:
    return [ensure_persona(db, persona, companies) for persona in PERSONAS]
