"""Режим жюри (P2, §9.1): любая компания датасета открывается как отдельный предприниматель.

Выписка берётся с сервера (`data/raw/statements/Cxxx.csv`) при первом входе в компанию, а не при посеве: 150 прогнозов заранее —
минуты ожидания ради двух-трёх открытых компаний. Работает только при DEMO_MODE=true (проверяет роутер).
Если перед этим выполнен `seed_demo.py --jury`, у компаний уже сохранён контекст «со стороны», и прогноз не хуже, чем у персон.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.db.models import Business, BusinessContext, Transaction, User
from app.engine.io import load_companies
from app.schemas import JuryCompany
from app.services import import_service
from app.services.forecast_service import ForecastService
from app.services.personas import DEMO_AS_OF, PERSONAS, Persona, jury_email
from app.services.replay_service import MIN_HISTORY_DAYS
from app.services.seed_service import first_text, seed_business, wipe_business_data

log = logging.getLogger("kassa.jury")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_seeding = threading.Lock()              # одна загрузка за раз: прогноз тяжёлый, а очередь у жюри короткая


class JuryError(ValueError):
    """Компанию открыть нельзя: текст показываем пользователю как есть, `status` — HTTP-код ответа."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def raw_dir(settings: AppSettings) -> Path:
    return settings.data_dir / "raw"


def list_companies(raw: Path) -> list[JuryCompany]:
    """Компании, по которым есть и строка справочника, и файл выписки. Нет датасета — пусто (режим жюри не показывается)."""
    path = raw / "companies.csv"
    if not path.is_file():
        return []
    try:
        table = load_companies(path)
    except (OSError, ValueError):
        log.warning("companies.csv unreadable")
        return []
    out = []
    for row in table.to_dict("records"):
        company_id = str(row.get("company_id") or "").strip()
        if not _ID.match(company_id) or not (raw / "statements" / f"{company_id}.csv").is_file():
            continue
        out.append(JuryCompany(
            company_id=company_id, name=first_text(row, "company_name", "name", "title", default=f"Компания {company_id}"),
            legal_form=first_text(row, "legal_form", "form"), industry=first_text(row, "industry", "sector")))
    return sorted(out, key=lambda c: c.company_id)


def jury_persona(company: JuryCompany) -> Persona:
    """Персона режима жюри. Для трёх основных персон — их собственная карточка: тот же аккаунт и тот же источник счетов."""
    featured = next((p for p in PERSONAS if p.company_id == company.company_id), None)
    if featured is not None:
        return featured
    return Persona(email=jury_email(company.company_id), display_name=company.name, company_id=company.company_id,
                   fallback_name=company.name, fallback_industry=company.industry or "—",
                   fallback_legal_form=company.legal_form or "—", note="")


def choose_as_of(first: date, last: date, wanted: date) -> date | None:
    """Дата «Данные по …»: как у персон (31.07.2026), но не позже конца выписки и с историей не короче месяца."""
    as_of = min(wanted, last)
    if (as_of - first).days < MIN_HISTORY_DAYS:
        as_of = last
    return as_of if (as_of - first).days >= MIN_HISTORY_DAYS else None


def _has_data(db: Session, company_id: str) -> bool:
    business = db.get(Business, company_id)
    return business is not None and business.data_as_of is not None and bool(
        db.scalar(select(func.count()).select_from(Transaction).where(Transaction.business_id == company_id)))


def _load(db: Session, service: ForecastService, persona: Persona, raw: Path) -> None:
    content = (raw / "statements" / f"{persona.company_id}.csv").read_bytes()
    try:
        frame = import_service.parse_dataset_csv(content)
    except import_service.StatementError as exc:
        raise JuryError(f"Выписку этой компании не удалось прочитать: {exc}", 422) from exc
    as_of = choose_as_of(frame["date"].min(), frame["date"].max(), date.fromisoformat(DEMO_AS_OF))
    if as_of is None:
        raise JuryError(f"У этой компании слишком короткая история: нужно хотя бы {MIN_HISTORY_DAYS} дней операций.", 422)
    stored = db.get(BusinessContext, persona.company_id)
    if stored is not None and stored.as_of > as_of:       # у компании, закрывшейся раньше, контекст знал бы про данные из её будущего
        db.delete(stored)
        db.commit()
    try:
        seed_business(db, service, persona, content, as_of, load_companies(raw / "companies.csv"))
    except Exception as exc:                  # любой сбой движка на нетипичной компании: не оставляем половину данных
        db.rollback()
        wipe_business_data(db, persona.company_id, keep_context=True)
        log.error("jury seed failed", extra={"fields": {"business": persona.company_id, "error": type(exc).__name__}})
        raise JuryError("Не удалось подготовить прогноз по этой компании. Попробуйте другую.", 422) from exc


def open_company(db: Session, service: ForecastService, settings: AppSettings, company_id: str) -> User:
    """Предприниматель компании: при первом входе создаёт его и загружает выписку, потом просто находит."""
    raw = raw_dir(settings)
    company = next((c for c in list_companies(raw) if c.company_id == company_id.strip()), None)
    if company is None:
        raise JuryError("Такой компании нет в датасете.", 404)
    if not _has_data(db, company.company_id):
        with _seeding:
            db.expire_all()                                        # пока ждали очередь, другой запрос мог загрузить компанию
            if not _has_data(db, company.company_id):
                _load(db, service, jury_persona(company), raw)
    business = db.get(Business, company.company_id)
    log.info("jury open", extra={"fields": {"business": company.company_id}})
    return db.get(User, business.owner_user_id)
