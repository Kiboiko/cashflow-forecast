"""Предупреждения (§6.17): хранение черновиков движка, дедупликация, отметка «прочитано».

Движок возвращает `AlertDraft` с ключом `key`; сервис не создаёт повторов, пока прежнее предупреждение с этим
ключом не прочитано или моложе 24 часов. Время — «виртуальное» (дата данных, 09:00 по Москве): в демо с машиной
времени предупреждения датируются днём, на который строился прогноз, а не сегодняшним числом.
"""
from __future__ import annotations

import datetime as dt
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Alert, Business
from app.engine.alerts import AlertDraft
from app.logging_setup import MSK
from app.schemas import Alert as AlertOut

DEDUP_WINDOW = dt.timedelta(hours=24)
ALERT_HOUR = 9


def virtual_now(as_of: date) -> dt.datetime:
    """Наивное московское время «в день данных»."""
    return dt.datetime.combine(as_of, dt.time(ALERT_HOUR))


def store_alerts(db: Session, business: Business, drafts: list[AlertDraft], now: dt.datetime) -> list[Alert]:
    stored: list[Alert] = []
    for draft in drafts:
        duplicate = db.scalar(select(Alert.id).where(
            Alert.business_id == business.id, Alert.key == draft.key,
            or_(Alert.acknowledged_at.is_(None), Alert.created_at > now - DEDUP_WINDOW)))
        if duplicate is not None:
            continue
        alert = Alert(business_id=business.id, type=draft.type, severity=draft.severity, key=draft.key,
                      title=draft.title, body=draft.body, payload=draft.payload, created_at=now, sent_channels=[])
        db.add(alert)
        stored.append(alert)
    db.commit()
    return stored


def to_out(alert: Alert) -> AlertOut:
    return AlertOut(
        id=alert.id, type=alert.type, severity=alert.severity, title=alert.title, body=alert.body,
        created_at=alert.created_at.replace(tzinfo=MSK),
        acknowledged_at=alert.acknowledged_at.replace(tzinfo=MSK) if alert.acknowledged_at else None)


def list_alerts(db: Session, business: Business, status: str = "open", limit: int = 100) -> list[AlertOut]:
    stmt = select(Alert).where(Alert.business_id == business.id)
    if status == "open":
        stmt = stmt.where(Alert.acknowledged_at.is_(None))
    rows = db.scalars(stmt.order_by(Alert.created_at.desc(), Alert.id.desc()).limit(limit)).all()
    return [to_out(a) for a in rows]


def acknowledge(db: Session, business: Business, alert: Alert) -> AlertOut:
    if alert.acknowledged_at is None:
        alert.acknowledged_at = virtual_now(business.data_as_of) if business.data_as_of else dt.datetime.now()
        db.commit()
    return to_out(alert)
