"""КОНТРАКТЫ API (§8) — общая зона A и B. Менять только вдвоём.

Здесь — модели прогноза, сценариев, план-факта, импорта, событий и предупреждений.
Модели входа/пользователя/бизнеса/операций (`/auth`, `/me`, `/businesses`, `/transactions`) добавляет B.
Типы фронта генерируются из OpenAPI: `make types`.
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Direction = Literal["in", "out"]
RiskLevel = Literal["green", "yellow", "red"]
EventStatus = Literal["planned", "fulfilled", "partially", "overdue", "cancelled"]
EventSource = Literal["recurring", "tax_calendar", "invoice", "contract", "contract_detected",
                      "manual", "csv", "1c", "demo_accounting", "bank_mock", "revenue", "rates"]
EventKind = Literal["invoice_out", "bill_in", "contract", "manual", "tax", "bank_internal"]


# Алиасы: поля с именем `date` затеняют тип `date` внутри тела класса.
Date = dt.date
DateTime = dt.datetime


class Schema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# ---------- прогноз ----------
class DailyPoint(Schema):
    date: Date
    working_day: bool
    p10: float          # «если всё пойдёт хуже обычного»
    p50: float          # «ожидаемый остаток»
    p90: float          # «если всё пойдёт лучше обычного»
    inflow_p50: float
    outflow_p50: float
    prob_below: float   # P(остаток < порога) в этот день


class ForecastEvent(Schema):
    """Регулярный платёж, налог, счёт, договор или ручное событие."""
    id: str
    date: Date | None = None
    date_range: tuple[Date, Date] | None = None     # для счетов клиентов: диапазон вероятной оплаты
    direction: Direction
    amount: float
    category: str
    title: str
    source: EventSource
    probability: float                              # шанс, что событие случится в горизонте
    mandatory: bool
    status: EventStatus = "planned"
    explain: str


class Driver(Schema):
    """Причина риска (блок «Почему»)."""
    kind: Literal["outflow", "late_inflow", "seasonality", "deviation"]
    date: Date | None = None
    amount: float | None = None
    title: str | None = None
    category: str | None = None
    text: str | None = None


class MandatoryCoverage(Schema):
    event_id: str
    title: str
    date: Date
    amount: float
    covered_probability: float                      # «хватит с вероятностью N%»


class Risk(Schema):
    level: RiskLevel
    gap_probability: float
    possible_gap_date: Date | None = None           # первый день с prob_below ≥ 0,2
    likely_gap_date: Date | None = None             # первый день с prob_below ≥ 0,5
    shortfall_p50: float                            # «скорее всего не хватит»
    shortfall_p90: float                            # «в плохом сценарии»
    safe_buffer: float                              # сколько добавить, чтобы обойтись без разрыва с вероятностью 90%
    headline: str                                   # фраза-заголовок для Главной
    drivers: list[Driver] = []
    mandatory_coverage: list[MandatoryCoverage] = []
    flags: list[str] = []                           # owner_injections_history, tax_debt, overdraft_hint, ...


class RecommendationEffect(Schema):
    gap_probability_after: float
    shortfall_p50_after: float


class Recommendation(Schema):
    id: str
    type: Literal["collect_receivable", "defer_supplier", "move_own_funds", "postpone_withdrawal", "credit_line"]
    title: str
    amount: float | None = None
    deadline: Date | None = None
    effect: RecommendationEffect
    priority: int


class Insight(Schema):
    kind: str
    text: str


class GapOut(Schema):
    start: Date
    end: Date
    unexplained_amount: float


class DataQuality(Schema):
    data_as_of: Date
    gaps: list[GapOut] = []
    unknown_ops: int = 0
    history: Literal["full", "no_full_year", "short_history"] = "full"
    inactive: bool = False


class ModelInfo(Schema):
    version: str
    n_paths: int
    seed: int
    components: list[str]


class ForecastResponse(Schema):
    business_id: str
    as_of: Date
    horizon_days: int
    currency: str = "RUB"
    start_balance: float
    threshold: float
    daily: list[DailyPoint]
    events: list[ForecastEvent]
    risk: Risk
    recommendations: list[Recommendation] = []
    insights: list[Insight] = []
    data_quality: DataQuality
    model: ModelInfo


# ---------- сценарии «что если» ----------
class ClientDelayShock(Schema):
    type: Literal["client_delay"]
    counterparty_inn: str | None = None             # None → крупнейший клиент
    delay_days: int = Field(ge=1, le=90)


class RevenueDropShock(Schema):
    type: Literal["revenue_drop"]
    pct: float = Field(gt=0, le=100)
    from_date: Date | None = Field(default=None, alias="from")


class OneOffExpenseShock(Schema):
    type: Literal["one_off_expense"]
    amount: float = Field(gt=0)
    date: Date
    title: str = "Внеплановый расход"
    mandatory: bool = False


class FixedCostIncreaseShock(Schema):
    type: Literal["fixed_cost_increase"]
    pct: float = Field(gt=0, le=100)
    categories: list[str]


Shock = Annotated[ClientDelayShock | RevenueDropShock | OneOffExpenseShock | FixedCostIncreaseShock,
                  Field(discriminator="type")]


class ScenarioRequest(Schema):
    horizon_days: int = 30
    shocks: list[Shock]


class ScenarioForecast(Schema):
    daily: list[DailyPoint]
    risk: Risk


class ScenarioDelta(Schema):
    gap_probability: float                          # изменение шанса разрыва (в долях)
    shortfall_p50: float
    gap_date_shift_days: int | None = None          # отрицательное — разрыв наступает раньше


class ScenarioResponse(Schema):
    base: ScenarioForecast
    scenario: ScenarioForecast
    delta: ScenarioDelta
    summary: str


# ---------- план-факт ----------
class PlanFactDay(Schema):
    date: Date
    plan_in: float
    fact_in: float | None = None
    plan_out: float
    fact_out: float | None = None
    plan_balance_p50: float
    fact_balance: float | None = None


class PlanFactEvent(Schema):
    event_id: str
    title: str
    expected_date: Date
    amount: float
    status: EventStatus
    matched_transaction_id: str | None = None
    days_late: int | None = None


class PlanFactSummary(Schema):
    inflow_deviation_pct: float
    outflow_deviation_pct: float
    missed_events: int


class PlanFactResponse(Schema):
    snapshot_id: int
    snapshot_as_of: Date
    fact_until: Date
    daily: list[PlanFactDay]
    events: list[PlanFactEvent]
    summary: PlanFactSummary


# ---------- импорт, события, предупреждения ----------
class ImportResult(Schema):
    batch_id: int
    format: Literal["dataset_csv", "1c", "replay", "seed"]
    period: tuple[Date, Date]
    added: int
    duplicates_removed: int
    data_gaps: list[GapOut] = []
    categorized_share: float
    needs_review: int


class EventCreate(Schema):
    """Ручное событие: «клиент обещал оплатить 300 000 ₽ 20-го»."""
    title: str
    direction: Direction
    amount: float = Field(gt=0)
    date: Date
    counterparty_name: str | None = None
    probability: float = Field(default=1.0, ge=0, le=1)
    mandatory: bool = False


AlertType = Literal["gap_risk", "mandatory_at_risk", "missed_inflow", "overdue_invoice", "data_gap", "needs_review"]


class Alert(Schema):
    id: int
    type: AlertType
    severity: Literal["info", "warning", "critical"]
    title: str
    body: str
    created_at: DateTime
    acknowledged_at: DateTime | None = None
