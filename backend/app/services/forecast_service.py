"""Прогноз для бизнеса: вызов движка, кэш, снимки (§4.1, ENGINE_API.md).

Состояние движка (`EngineState`) и результаты кэшируются в памяти процесса по ключу `(бизнес, версия данных, …)`:
версия растёт при любом изменении данных, правил, настроек или плана, поэтому устаревшего ответа не будет.
Сервис рассчитан на один процесс uvicorn. Режим `fixture` отдаёт готовый ответ `forecast_example.json` — для
первого запуска, тестов и случая, когда датасета нет.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Hashable
from datetime import date
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import AppSettings
from app.db.models import Business, ExpectedEvent, ForecastSnapshot
from app.engine.categorize import categorize
from app.engine.categorize.taxonomy import TAXONOMY
from app.engine.enrich import enrich
from app.engine.pipeline import run_scenario
from app.engine.plan import manual_event
from app.engine.planfact import make_snapshot
from app.engine.receivables import Invoice, restore_open_documents
from app.engine.recurring import SALARY_TITLE, detect_streams
from app.engine.response import to_response, to_scenario_response
from app.engine.state import EngineState
from app.engine.types import ForecastResult, PlannedEvent
from app.schemas import (
    DataQuality,
    EventCreate,
    ForecastResponse,
    RecurringOut,
    ScenarioRequest,
    ScenarioResponse,
)
from app.services.business_service import business_settings, engine_settings
from app.services.engine_adapter import NoDataError, load_context, load_ops, load_user_rules

_WEEKDAYS_DAT = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]
_MAX_DATE = date(9999, 12, 31)


@lru_cache
def _load_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class _Lru:
    """Маленький потокобезопасный LRU для кэша результатов."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._items: OrderedDict[Hashable, object] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> object | None:
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        return None

    def put(self, key: Hashable, value: object) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.size:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


def schedule_text(period: str, anchor_day: int) -> str:
    if period == "weekly":
        return f"каждую неделю по {_WEEKDAYS_DAT[anchor_day % 7]}"
    if period == "quarterly":
        return f"раз в квартал, {anchor_day}-го"
    return f"каждый месяц {anchor_day}-го"


def _stream_title(key: str, category: str, counterparty: str | None) -> str:
    if category == "salary":
        return SALARY_TITLE.get(key.split(":")[1], TAXONOMY[category].title)
    if category == "client_payment" and counterparty:
        return f"Оплата от {counterparty}"
    return TAXONOMY[category].title if category in TAXONOMY else category


class ForecastService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._states = _Lru(32)
        self._results = _Lru(128)
        self._scenarios = _Lru(64)
        self._full_ops = _Lru(8)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    # ---------- служебное ----------
    def _lock(self, business_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(business_id, threading.RLock())

    def clear(self) -> None:
        for cache in (self._states, self._results, self._scenarios, self._full_ops):
            cache.clear()

    @property
    def fixture_mode(self) -> bool:
        return self.settings.forecast_mode == "fixture"

    # ---------- состояние движка ----------
    def state(self, db: Session, business: Business) -> EngineState:
        """Состояние на `data_as_of`: из кэша или полная сборка (качество → категории → обогащение)."""
        key = (business.id, business.data_version)
        with self._lock(business.id):
            cached = self._states.get(key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            ops = load_ops(db, business)
            as_of = business.data_as_of or ops["date"].max().date()
            state = EngineState.build(ops, as_of, engine_settings(business), user_rules=load_user_rules(db, business),
                                      context=load_context(db, business))
            self._states.put(key, state)
            return state

    def adopt_state(self, business: Business, state: EngineState) -> None:
        """Кладёт дособранное состояние (машина времени) под текущую версию данных — без полной пересборки."""
        self._states.put((business.id, business.data_version), state)

    # ---------- план и счета ----------
    def planned(self, db: Session, business: Business, as_of: date) -> list[PlannedEvent]:
        """Ручные события и договоры предпринимателя, которые ещё впереди."""
        rows = db.scalars(select(ExpectedEvent).where(
            ExpectedEvent.business_id == business.id, ExpectedEvent.status == "planned",
            ExpectedEvent.kind.in_(("manual", "contract")), ExpectedEvent.expected_date > as_of)
            .order_by(ExpectedEvent.id)).all()
        out: list[PlannedEvent] = []
        for e in rows:
            if e.kind == "manual":
                out.append(manual_event(EventCreate(
                    title=e.title, direction=e.direction, amount=e.amount, date=e.expected_date,
                    counterparty_name=e.counterparty_name, probability=e.probability, mandatory=e.mandatory), e.id))
            else:
                out.append(PlannedEvent(
                    id=f"contract:{e.id}", direction=1 if e.direction == "in" else -1, amount=e.amount,
                    date=e.expected_date, p_within_horizon=e.probability, category="client_payment" if e.direction == "in" else "suppliers",
                    title=e.title, source="contract", mandatory=e.mandatory, counterparty=e.counterparty_name,
                    counterparty_inn=e.counterparty_inn, explain="Договор из файла: ожидаемый платёж"))
        return out

    def _full_enriched(self, db: Session, business: Business):
        """Вся выписка (включая скрытое «будущее» демо) с категориями — для демо-«учётной системы»."""
        count = db.scalar(select(func.count()).select_from(ExpectedEvent).where(ExpectedEvent.business_id == business.id))
        key = (business.id, business.data_version, count)
        cached = self._full_ops.get(key)
        if cached is None:
            full = load_ops(db, business, up_to=_MAX_DATE)
            cached = enrich(categorize(full, load_user_rules(db, business)))
            self._full_ops.put(key, cached)
        return cached

    def invoices(self, db: Session, business: Business, as_of: date) -> list[Invoice] | None:
        """`None` — источник счетов не подключён; список (даже пустой) — подключён (ENGINE_API §4)."""
        source = business.accounting_source
        if source is None:
            return None
        if source == "demo_accounting":
            return restore_open_documents(self._full_enriched(db, business), as_of)
        rows = db.scalars(select(ExpectedEvent).where(
            ExpectedEvent.business_id == business.id, ExpectedEvent.kind.in_(("invoice_out", "bill_in")),
            ExpectedEvent.status.in_(("planned", "overdue", "partially"))).order_by(ExpectedEvent.id)).all()
        return [Invoice(doc_no=e.doc_no or str(e.id), doc_date=e.doc_date or as_of, direction=e.direction, amount=e.amount,
                        counterparty_inn=e.counterparty_inn, counterparty_name=e.counterparty_name,
                        doc_type=e.doc_type or "invoice", due_date=e.due_date, source=e.source) for e in rows]

    # ---------- прогноз ----------
    def result(self, db: Session, business: Business, horizon: int) -> ForecastResult:
        key = (business.id, business.data_version, horizon)
        with self._lock(business.id):
            cached = self._results.get(key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            state = self.state(db, business)
            result = state.forecast(planned=self.planned(db, business, state.as_of), horizon=horizon,
                                    invoices=self.invoices(db, business, state.as_of))
            result.simulation = None                   # матрицы сценариев нужны только внутри вызова (ENGINE_API §2)
            self._results.put(key, result)
            return result

    def forecast(self, db: Session, business: Business, horizon: int | None = None) -> ForecastResponse:
        if self.fixture_mode:
            data = copy.deepcopy(_load_fixture(str(self.settings.fixture_path)))
            data["business_id"] = business.id
            return ForecastResponse.model_validate(data)
        horizon = horizon or business_settings(business).horizon_days
        return to_response(self.result(db, business, horizon), business.id)

    def data_quality(self, db: Session, business: Business) -> DataQuality | None:
        if self.fixture_mode:
            return ForecastResponse.model_validate(_load_fixture(str(self.settings.fixture_path))).data_quality
        try:
            return self.result(db, business, business_settings(business).horizon_days).data_quality
        except NoDataError:
            return None

    # ---------- «что если» ----------
    def scenario(self, db: Session, business: Business, request: ScenarioRequest) -> ScenarioResponse:
        key = (business.id, business.data_version, hashlib.sha1(request.model_dump_json(by_alias=True).encode()).hexdigest())
        with self._lock(business.id):
            cached = self._scenarios.get(key)
            if cached is not None:
                return cached  # type: ignore[return-value]
            state = self.state(db, business)
            model = state.model(self.planned(db, business, state.as_of), horizon=request.horizon_days,
                                invoices=self.invoices(db, business, state.as_of))
            base, scenario = run_scenario(model, request.shocks, n_paths=1000)
            response = to_scenario_response(base, scenario, request.shocks)
            self._scenarios.put(key, response)
            return response

    # ---------- регулярные платежи ----------
    def recurring(self, db: Session, business: Business) -> list[RecurringOut]:
        state = self.state(db, business)
        out = []
        for s in detect_streams(state.ops, state.as_of):
            out.append(RecurringOut(
                key=s.key, category=s.category, title=_stream_title(s.key, s.category, s.counterparty),
                direction="in" if s.direction > 0 else "out", schedule=schedule_text(s.period, s.anchor_day),
                amount=round(s.amount, 2), last_date=s.last_date, active=s.active, confidence=round(s.confidence, 2),
                mandatory=s.mandatory, counterparty=s.counterparty, explain=s.explain))
        return sorted(out, key=lambda r: (not r.active, r.direction == "in", -r.amount))

    # ---------- снимки для план-факта ----------
    def params_hash(self, db: Session, business: Business) -> str:
        events = db.execute(select(ExpectedEvent.id, ExpectedEvent.status, ExpectedEvent.amount, ExpectedEvent.expected_date)
                            .where(ExpectedEvent.business_id == business.id).order_by(ExpectedEvent.id)).all()
        payload = {"settings": business.settings, "accounting": business.accounting_source,
                   "events": [[e.id, e.status, e.amount, str(e.expected_date)] for e in events]}
        return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def ensure_snapshot(self, db: Session, business: Business) -> ForecastSnapshot:
        """Снимок прогноза на текущую дату данных: база для сравнения «план против факта»."""
        as_of = business.data_as_of
        digest = self.params_hash(db, business)
        existing = db.scalar(select(ForecastSnapshot).where(
            ForecastSnapshot.business_id == business.id, ForecastSnapshot.as_of == as_of,
            ForecastSnapshot.params_hash == digest).order_by(ForecastSnapshot.id.desc()))
        if existing is not None:
            return existing
        result = self.result(db, business, business_settings(business).horizon_days)
        snapshot = ForecastSnapshot(business_id=business.id, as_of=result.as_of, horizon=result.horizon, params_hash=digest,
                                    model_version=str(result.model_info.get("version", "")),
                                    payload=make_snapshot(result, business.id))
        db.add(snapshot)
        db.commit()
        return snapshot
