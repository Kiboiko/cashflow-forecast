"""Бизнес: настройки, машина времени, сборка ответа `BusinessOut`."""
from __future__ import annotations

import dataclasses
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DEFAULT_SETTINGS, Business, Transaction, User
from app.engine.types import Settings
from app.schemas import BusinessOut, BusinessSettings, DataQuality, ReplayInfo, SettingsPatch


def business_settings(business: Business) -> BusinessSettings:
    return BusinessSettings.model_validate({**DEFAULT_SETTINGS, **(business.settings or {})})


def engine_settings(business: Business) -> Settings:
    """Настройки для движка. Берём только поля `Settings`: лишний ключ в нём — TypeError."""
    allowed = {f.name for f in dataclasses.fields(Settings)}
    return Settings(**{k: v for k, v in business_settings(business).model_dump().items() if k in allowed})


def last_operation_date(db: Session, business: Business) -> date | None:
    """Последняя дата выписки в БД — независимо от видимости (`data_as_of`)."""
    return db.scalar(select(func.max(Transaction.date)).where(Transaction.business_id == business.id))


def replay_info(db: Session, business: Business) -> ReplayInfo:
    last = last_operation_date(db, business)
    enabled = business.data_as_of is not None and last is not None and last > business.data_as_of
    return ReplayInfo(enabled=enabled, data_until=last, can_advance=enabled)


def business_out(db: Session, business: Business, quality: DataQuality | None) -> BusinessOut:
    owner = db.get(User, business.owner_user_id)
    return BusinessOut(
        id=business.id, name=business.name, inn=business.inn, legal_form=business.legal_form,
        industry=business.industry, settings=business_settings(business),
        telegram_chat_id=owner.telegram_chat_id if owner else None, data_as_of=business.data_as_of,
        accounting_source=business.accounting_source, data_quality=quality, replay=replay_info(db, business))


def apply_settings_patch(db: Session, business: Business, patch: SettingsPatch) -> None:
    """Частично обновляет настройки. Прогноз зависит от настроек, поэтому растёт версия данных (сброс кэша)."""
    changes = patch.model_dump(exclude_unset=True)
    telegram_given = "telegram_chat_id" in changes
    telegram = changes.pop("telegram_chat_id", None)
    changes = {k: v for k, v in changes.items() if v is not None}       # null в PATCH = «не менять»
    if changes:
        merged = business_settings(business).model_dump()
        merged.update(changes)
        business.settings = BusinessSettings.model_validate(merged).model_dump()
        business.data_version += 1
    if telegram_given:
        owner = db.get(User, business.owner_user_id)
        owner.telegram_chat_id = (telegram or "").strip() or None
    db.commit()
