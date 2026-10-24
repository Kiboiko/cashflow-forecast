"""Модели БД (§5): предприниматель → бизнесы → счета → операции, обязательства и планы.

Колонки с вложенными данными — тип `JSON` (не `JSONB`): работает и в PostgreSQL, и в SQLite тестов.
Время хранится как наивное московское (так проще показывать и «машину времени»: события «в прошлом» пишутся
виртуальной датой).
"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

Money = Numeric(18, 2, asdecimal=False)

DEFAULT_SETTINGS: dict[str, Any] = {
    "horizon_days": 30, "threshold": 0.0, "overdraft_limit": 0.0, "risk_yellow": 0.10, "risk_red": 0.40,
    "other_accounts": [], "include_other_accounts": False, "include_owner_withdrawals": True,
}


def now_msk() -> dt.datetime:
    """Текущее московское время без таймзоны."""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).replace(tzinfo=None)


class User(Base):
    """Предприниматель — центр модели (§2)."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), default=None)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)         # карточка на экране «Демо-персоны»
    persona_note: Mapped[str | None] = mapped_column(String(300), default=None)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now_msk)

    businesses: Mapped[list[Business]] = relationship(back_populates="owner", order_by="Business.id")


class Business(Base):
    """Бизнес предпринимателя. `id` — код компании датасета (C064) или сгенерированный."""
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str | None] = mapped_column(String(16), default=None)
    legal_form: Mapped[str | None] = mapped_column(String(16), default=None)       # ООО | ИП
    industry: Mapped[str | None] = mapped_column(String(120), default=None)
    settings: Mapped[dict] = mapped_column(JSON, default=lambda: copy.deepcopy(DEFAULT_SETTINGS))
    data_as_of: Mapped[dt.date | None] = mapped_column(Date, default=None)        # «Данные по …»: чтение видит операции ≤ даты
    data_version: Mapped[int] = mapped_column(Integer, default=1)                  # растёт при любом изменении данных/правил
    accounting_source: Mapped[str | None] = mapped_column(String(24), default=None)  # demo_accounting | csv | 1c | None
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now_msk)

    owner: Mapped[User] = relationship(back_populates="businesses")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    bank_name: Mapped[str] = mapped_column(String(120), default="Основной счёт")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    source: Mapped[str] = mapped_column(String(24))                       # dataset_csv | 1c | replay | seed
    filename: Mapped[str | None] = mapped_column(String(255), default=None)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now_msk)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (Index("ix_transactions_business_date", "business_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), default=None)
    external_id: Mapped[str] = mapped_column(String(64), index=True)       # operation_id выписки; НЕ уникален (дубли)
    date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Money)
    direction: Mapped[int] = mapped_column(SmallInteger)                   # +1 поступление, −1 списание
    counterparty_inn: Mapped[str | None] = mapped_column(String(16), default=None)
    counterparty_name: Mapped[str | None] = mapped_column(String(512), default=None)
    purpose: Mapped[str | None] = mapped_column(Text, default=None)
    balance_eod: Mapped[float] = mapped_column(Money)
    category: Mapped[str | None] = mapped_column(String(40), index=True, default=None)
    category_source: Mapped[str | None] = mapped_column(String(16), default=None)   # user|rule|name_rule|memory|ml|unknown
    category_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    doc_type: Mapped[str | None] = mapped_column(String(24), default=None)
    doc_no: Mapped[str | None] = mapped_column(String(64), default=None)
    doc_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    contract_no: Mapped[str | None] = mapped_column(String(64), default=None)
    sales_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    flags: Mapped[dict] = mapped_column(JSON, default=dict)                # duplicate, reversal_pair_id, gap_adjacent
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), default=None)


class DataGap(Base):
    __tablename__ = "data_gaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date] = mapped_column(Date)
    unexplained_amount: Mapped[float] = mapped_column(Money)


class CounterpartyRule(Base):
    """Исправление предпринимателя: «все платежи этому контрагенту — такая категория»."""
    __tablename__ = "counterparty_rules"
    __table_args__ = (UniqueConstraint("business_id", "counterparty_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    counterparty_key: Mapped[str] = mapped_column(String(512))             # ИНН или нормализованное имя
    category: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(16), default="user")        # user | memory
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now_msk)


class ExpectedEvent(Base):
    """План предпринимателя и внешние источники. Регулярные платежи не храним — движок вычисляет их из выписки."""
    __tablename__ = "expected_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))                          # invoice_out|bill_in|contract|manual|tax|bank_internal
    source: Mapped[str] = mapped_column(String(24))                        # csv|1c|manual|demo_accounting|contract_detected|tax_calendar|bank_mock
    direction: Mapped[str] = mapped_column(String(3))                      # in | out
    amount: Mapped[float] = mapped_column(Money)
    expected_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    due_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    doc_type: Mapped[str | None] = mapped_column(String(24), default=None)
    doc_no: Mapped[str | None] = mapped_column(String(64), default=None)
    doc_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    counterparty_inn: Mapped[str | None] = mapped_column(String(16), default=None)
    counterparty_name: Mapped[str | None] = mapped_column(String(512), default=None)
    title: Mapped[str] = mapped_column(String(255))
    probability: Mapped[float] = mapped_column(Float, default=1.0)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="planned")     # planned|fulfilled|partially|overdue|cancelled
    matched_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), default=None)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now_msk)


class ForecastSnapshot(Base):
    """Снимок прогноза для план-факта (`engine.planfact.make_snapshot`)."""
    __tablename__ = "forecast_snapshots"
    __table_args__ = (Index("ix_snapshots_business_asof", "business_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"))
    as_of: Mapped[dt.date] = mapped_column(Date)
    horizon: Mapped[int] = mapped_column(Integer)
    params_hash: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now_msk)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_business_key", "business_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"))
    type: Mapped[str] = mapped_column(String(24))
    severity: Mapped[str] = mapped_column(String(12))
    key: Mapped[str] = mapped_column(String(160))                          # дедупликация за 24 часа
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now_msk)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    sent_channels: Mapped[list] = mapped_column(JSON, default=list)


class BusinessContext(Base):
    """Контекст «со стороны» (сезонность отрасли, ЕНП, задержки): считается в seed один раз, хранится pickle."""
    __tablename__ = "business_contexts"

    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), primary_key=True)
    as_of: Mapped[dt.date] = mapped_column(Date)
    payload: Mapped[bytes] = mapped_column(LargeBinary)
