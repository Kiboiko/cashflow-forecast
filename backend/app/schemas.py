"""КОНТРАКТЫ API (§8) — общая зона A и B. Менять только вдвоём.

Здесь — модели прогноза, сценариев, план-факта, импорта, событий и предупреждений.
Модели входа/пользователя/бизнеса/операций (`/auth`, `/me`, `/businesses`, `/transactions`) добавляет B.
Типы фронта генерируются из OpenAPI: `make types`.
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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


# =====================================================================================================
# Ниже — модели сервиса (человек B): вход, пользователь, бизнес, настройки, операции. Движок их не использует.
# =====================================================================================================

# ---------- вход и пользователь ----------
class LoginRequest(Schema):
    email: str
    password: str


class RegisterRequest(Schema):
    """Регистрация предпринимателя вместе с его бизнесом: выписку он загружает уже после входа."""
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    password: Annotated[str, Field(min_length=8, max_length=72)]       # bcrypt учитывает первые 72 байта
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    business_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    legal_form: Literal["ООО", "ИП"] | None = None
    inn: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^(\d{10}|\d{12})$")] | None = None


class DemoLoginRequest(Schema):
    email: str


class UserOut(Schema):
    id: int
    email: str
    display_name: str
    telegram_connected: bool = False


class TokenResponse(Schema):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut


class BusinessBrief(Schema):
    id: str
    name: str
    legal_form: str | None = None
    industry: str | None = None
    data_as_of: Date | None = None


class MeResponse(Schema):
    user: UserOut
    businesses: list[BusinessBrief]


class DemoPersona(Schema):
    """Карточка на экране «Демо-персоны»: вход одним нажатием."""
    email: str
    display_name: str
    title: str                                      # «детский центр (ИП)»
    note: str | None = None
    business_id: str


# ---------- бизнес и настройки ----------
class OtherAccount(Schema):
    bank: str = Field(max_length=120)
    balance: float


class BusinessSettings(Schema):
    """Настройки бизнеса (`Business.settings`): поля совпадают с `engine.types.Settings`."""
    horizon_days: Literal[30, 60, 90] = 30
    threshold: float = Field(default=0.0, ge=0)                 # неснижаемый остаток
    overdraft_limit: float = Field(default=0.0, ge=0)
    risk_yellow: float = Field(default=0.10, gt=0, lt=1)
    risk_red: float = Field(default=0.35, gt=0, lt=1)
    other_accounts: list[OtherAccount] = []
    include_other_accounts: bool = False
    include_owner_withdrawals: bool = True


class SettingsPatch(Schema):
    """Частичное обновление настроек. `telegram_chat_id` хранится у пользователя, а не в настройках бизнеса."""
    horizon_days: Literal[30, 60, 90] | None = None
    threshold: float | None = Field(default=None, ge=0)
    overdraft_limit: float | None = Field(default=None, ge=0)
    other_accounts: list[OtherAccount] | None = None
    include_other_accounts: bool | None = None
    include_owner_withdrawals: bool | None = None
    telegram_chat_id: str | None = Field(default=None, max_length=64)


class ReplayInfo(Schema):
    """Машина времени (демо): данные после `data_as_of` уже загружены, но скрыты и «подгружаются» шагами."""
    enabled: bool
    data_until: Date | None = None                  # последняя дата в выписке; после неё шагать некуда
    can_advance: bool = False


class BusinessOut(Schema):
    id: str
    name: str
    inn: str | None = None
    legal_form: str | None = None
    industry: str | None = None
    settings: BusinessSettings
    telegram_chat_id: str | None = None
    data_as_of: Date | None = None
    accounting_source: str | None = None            # откуда счета: demo_accounting | csv | 1c | None
    data_quality: DataQuality | None = None
    replay: ReplayInfo


# ---------- операции и категории ----------
CategorySource = Literal["user", "rule", "name_rule", "memory", "ml", "unknown"]


class TransactionOut(Schema):
    id: int
    date: Date
    amount: float                                   # всегда положительная; знак — в direction
    direction: Direction
    counterparty_inn: str | None = None
    counterparty_name: str | None = None
    purpose: str | None = None
    category: str | None = None
    category_title: str | None = None
    category_source: CategorySource | None = None
    needs_review: bool = False                      # не распознано — просим уточнить
    flags: list[str] = []                           # duplicate | reversal | gap_adjacent


class TransactionPage(Schema):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int
    needs_review_count: int                         # всего нераспознанных (для «Нераспознанные (3) — уточните»)


class CategoryInfo(Schema):
    code: str
    title: str
    group: str
    mandatory: bool
    direction: Direction                            # категорию поступлений нельзя поставить списанию и наоборот


class CategoryPatch(Schema):
    """Исправление категории. `apply_to_counterparty` — запомнить правило для контрагента и пересобрать все его операции."""
    category: str
    apply_to_counterparty: bool = False


class CategoryPatchResult(Schema):
    transaction: TransactionOut
    updated: int                                    # сколько операций сменили категорию
    rule_saved: bool


class CategorySummaryRow(Schema):
    category: str
    title: str
    group: str
    direction: Direction
    total: float
    by_month: list[float]


class CategorySummary(Schema):
    """«Куда уходят деньги»: суммы по категориям и месяцам."""
    months: list[str]                               # ГГГГ-ММ, по возрастанию
    rows: list[CategorySummaryRow]
    inflow_total: float
    outflow_total: float


class RecurringOut(Schema):
    key: str
    category: str
    title: str
    direction: Direction
    schedule: str                                   # «каждый месяц 10-го»
    amount: float
    last_date: Date
    active: bool
    confidence: float
    mandatory: bool
    counterparty: str | None = None
    explain: str


# ---------- план: события, счета и договоры ----------
class EventOut(Schema):
    id: int
    kind: EventKind
    source: str                                     # manual | csv | 1c | demo_accounting | contract_detected | …
    direction: Direction
    amount: float
    expected_date: Date | None = None
    due_date: Date | None = None
    doc_type: str | None = None
    doc_no: str | None = None
    doc_date: Date | None = None
    counterparty_inn: str | None = None
    counterparty_name: str | None = None
    title: str
    probability: float
    mandatory: bool
    status: EventStatus


class EventPatch(Schema):
    """Правка события плана: меняем только то, что передано."""
    title: str | None = Field(default=None, min_length=1, max_length=255)
    amount: float | None = Field(default=None, gt=0)
    date: Date | None = None
    probability: float | None = Field(default=None, ge=0, le=1)
    mandatory: bool | None = None
    counterparty_name: str | None = None


class EventsImportResult(Schema):
    kind: Literal["invoices", "contracts"]
    added: int
    skipped: int                                    # уже были в плане
    errors: list[str] = []
    accounting_source: str | None = None


# ---------- «Вам должны» / «Вы должны» ----------
class ReceivableInvoice(Schema):
    doc_no: str
    doc_date: Date
    amount: float
    age_days: int
    expected_from: Date                             # оплата вероятна не раньше (10-й процентиль)
    expected_to: Date                               # …и не позже (90-й процентиль)
    overdue_by_client_standard: bool                # счёту больше, чем клиент обычно тянет


class ReceivableRow(Schema):
    counterparty_inn: str | None = None
    counterparty: str | None = None
    open_amount: float
    typical_delay_days: float                       # «обычно платит через 18±6 дн.»
    delay_spread_days: float
    profile_level: Literal["client", "company", "industry", "default"]
    overdue_amount: float
    invoices: list[ReceivableInvoice]


class ReceivablesResponse(Schema):
    direction: Direction
    connected: bool                                 # подключён ли источник счетов
    source: str | None = None
    source_label: str | None = None
    as_of: Date
    total: float
    overdue_total: float
    rows: list[ReceivableRow]


# ---------- «что если» ----------
class ScenarioPreset(Schema):
    id: str
    title: str
    description: str
    request: ScenarioRequest


class ScenarioPresets(Schema):
    presets: list[ScenarioPreset]


# ---------- снимки, машина времени ----------
class SnapshotBrief(Schema):
    id: int
    as_of: Date
    horizon: int
    level: RiskLevel
    gap_probability: float
    created_at: DateTime


class ReplayAdvanceRequest(Schema):
    days: int = Field(default=1, ge=1, le=14)


class ReplayResetRequest(Schema):
    as_of: Date | None = None                       # по умолчанию — исходная дата демо (31.07.2026)


class ReplayResult(Schema):
    as_of: Date
    previous_as_of: Date
    new_operations: int
    level: RiskLevel
    gap_probability: float
    gap_probability_before: float | None = None
    headline: str
    alerts: list[Alert]                             # новые предупреждения этого шага
    snapshot_id: int
    data_until: Date
    can_advance: bool


# ---------- отчёт бэктеста ----------
class ForecastTrace(Schema):
    """Пошаговый журнал расчёта прогноза (engine/trace.py): что модель увидела в данных и как получила ответ."""
    steps: list[str]


class BacktestReport(Schema):
    """Отчёт A (`reports/backtest_<дата>.json`) как есть: структуру разбирает страница «Как мы считаем»."""
    file: str
    report: dict


# ---------- проверка связи с Telegram ----------
class TelegramTestResult(Schema):
    sent: bool


# ---------- режим жюри: любая компания датасета как отдельный предприниматель ----------
class JuryCompany(Schema):
    company_id: str
    name: str
    legal_form: str | None = None
    industry: str | None = None


class JuryLoginRequest(Schema):
    company_id: str = Field(max_length=16)


# ---------- справочники: ключевая ставка ЦБ и проверка контрагентов ----------
class KeyRateOut(Schema):
    rate: float                                     # доля: 0.14 = 14% годовых
    as_of: Date | None = None                       # дата записи ЦБ; у запасного значения её нет
    source: Literal["cbr", "fallback"]


class CounterpartyCheck(Schema):
    inn: str
    status: Literal["liquidating", "bankrupt", "unreliable"]
    title: str                                      # «Идёт ликвидация»
    note: str | None = None


class CounterpartyChecks(Schema):
    source: Literal["demo"]                         # пока заглушка; с настоящим реестром станет другим значением
    flagged: list[CounterpartyCheck]                # только контрагенты, с которыми что-то не так


# ---------- план-факт: расширение контракта (поля сверх §8, клиенты старого формата их просто не читают) ----------
class PlanFactEventOut(PlanFactEvent):
    direction: Direction | None = None              # приход или платёж: от этого зависят слова «пришло / заплатили»
    source: str | None = None
    mandatory: bool = False


class PlanFactOut(PlanFactResponse):
    events: list[PlanFactEventOut]                  # type: ignore[assignment]
