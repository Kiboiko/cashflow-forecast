"""ForecastResult → ForecastResponse (контракт §8)."""
from __future__ import annotations

from app.engine.calendar_ru import is_workday
from app.engine.scenarios import scenario_delta, scenario_summary
from app.engine.types import ForecastResult, PlannedEvent
from app.schemas import (
    DailyPoint,
    ForecastEvent,
    ForecastResponse,
    ModelInfo,
    ScenarioForecast,
    ScenarioResponse,
)

_SOURCES = {"recurring", "tax_calendar", "invoice", "contract", "contract_detected", "manual", "csv", "1c",
            "demo_accounting", "bank_mock", "revenue", "rates"}


def _event(e: PlannedEvent) -> ForecastEvent:
    return ForecastEvent(
        id=e.id, date=e.date, direction="in" if e.direction > 0 else "out", amount=round(e.amount, 2),
        category=e.category, title=e.title, source=e.source if e.source in _SOURCES else "manual",
        probability=round(e.p_within_horizon, 2), mandatory=e.mandatory, status="planned", explain=e.explain,
        date_range=(min(d for d, _ in e.date_pmf), max(d for d, _ in e.date_pmf)) if e.date_pmf else None,
    )


def _daily(result: ForecastResult) -> list[DailyPoint]:
    return [
        DailyPoint(date=d, working_day=is_workday(d), p10=round(float(result.p10[i]), 2),
                   p50=round(float(result.p50[i]), 2), p90=round(float(result.p90[i]), 2),
                   inflow_p50=round(float(result.inflow_p50[i]), 2), outflow_p50=round(float(result.outflow_p50[i]), 2),
                   prob_below=round(float(result.prob_below[i]), 3))
        for i, d in enumerate(result.dates)
    ]


def to_scenario_response(base: ForecastResult, scenario: ForecastResult, shocks: list) -> ScenarioResponse:
    return ScenarioResponse(
        base=ScenarioForecast(daily=_daily(base), risk=base.risk),
        scenario=ScenarioForecast(daily=_daily(scenario), risk=scenario.risk),
        delta=scenario_delta(base, scenario), summary=scenario_summary(shocks, base, scenario),
    )


def to_response(result: ForecastResult, business_id: str) -> ForecastResponse:
    daily = _daily(result)
    return ForecastResponse(
        business_id=business_id, as_of=result.as_of, horizon_days=result.horizon,
        start_balance=round(result.start_balance, 2), threshold=result.threshold, daily=daily,
        events=[_event(e) for e in result.events], risk=result.risk, recommendations=result.recommendations,
        insights=result.insights, data_quality=result.data_quality, model=ModelInfo(**result.model_info),
    )
