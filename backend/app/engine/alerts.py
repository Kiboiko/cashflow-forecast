"""Предупреждения предпринимателю (§6.17): новые или выросшие риски, неоплаченные счета, пропуски данных.

Движок возвращает черновики с ключом `key` — по нему сервис дедуплицирует предупреждения за 24 часа и хранит их.
Типы: gap_risk, mandatory_at_risk, missed_inflow, overdue_invoice, data_gap, needs_review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from app.engine.calendar_ru import workdays_between
from app.engine.explain import dm, rub
from app.engine.planfact import Match
from app.engine.types import ForecastResult

LEVEL_RANK = {"green": 0, "yellow": 1, "red": 2}
COVERAGE_ALERT = 0.80               # покрытие обязательного платежа ниже — предупреждаем
COVERAGE_HORIZON_DAYS = 14
GAP_RISE = 0.15                     # шанс разрыва вырос на столько — новое предупреждение
DATA_GAP_WORKDAYS = 5
INVOICE_SOURCES = {"invoice", "csv", "demo_accounting", "1c", "contract", "contract_detected"}
REVIEW_LOOKBACK_DAYS = 30


@dataclass
class AlertDraft:
    type: str
    severity: str                           # info | warning | critical
    key: str
    title: str
    body: str
    payload: dict = field(default_factory=dict)


def _percent(p: float) -> str:
    return f"{round(p * 100)}%"


def gap_risk_alerts(prev: dict | None, new: ForecastResult) -> list[AlertDraft]:
    """Новый или выросший риск разрыва. `prev` — риск прошлого снимка (`snapshot["risk"]`) или None."""
    risk = new.risk
    if risk.level == "green":
        return []
    rose = (prev is None or LEVEL_RANK[risk.level] > LEVEL_RANK[prev["level"]]
            or risk.gap_probability - prev["gap_probability"] >= GAP_RISE)
    if not rose:
        return []
    gap = risk.possible_gap_date or risk.likely_gap_date
    before = "" if prev is None else f" Раньше шанс был {_percent(prev['gap_probability'])}."
    return [AlertDraft(
        "gap_risk", "critical" if risk.level == "red" else "warning",
        f"gap_risk:{risk.level}:{gap.isoformat() if gap else 'x'}",
        f"Может не хватить денег{f' около {dm(gap)}' if gap else ''}", risk.headline + before,
        {"level": risk.level, "gap_probability": risk.gap_probability})]


def mandatory_alerts(new: ForecastResult) -> list[AlertDraft]:
    """Обязательные платежи, на которые денег может не хватить, в ближайшие две недели."""
    horizon = new.as_of + timedelta(days=COVERAGE_HORIZON_DAYS)
    return [AlertDraft(
        "mandatory_at_risk", "critical" if c.covered_probability < 0.5 else "warning", f"mandatory:{c.event_id}",
        f"{c.title} {dm(c.date)}: может не хватить",
        f"Платёж {rub(c.amount)} {dm(c.date)} — хватит с вероятностью {_percent(c.covered_probability)}.",
        {"event_id": c.event_id, "covered_probability": c.covered_probability})
        for c in new.risk.mandatory_coverage if c.covered_probability < COVERAGE_ALERT and c.date <= horizon]


def missed_inflow_alerts(matches: list[Match], prev: dict | None, new: ForecastResult) -> list[AlertDraft]:
    """Не пришла ожидаемая оплата: `overdue_invoice` для счетов и договоров, `missed_inflow` для остальных приходов."""
    out = []
    for m in matches:
        e = m.event
        if m.status != "overdue" or e["direction"] != 1:
            continue
        who = e.get("counterparty") or e["title"]
        due = date.fromisoformat(e["date"])
        effect = ""
        if prev is not None:
            effect = (f" Шанс нехватки денег {'вырос' if new.risk.gap_probability >= prev['gap_probability'] else 'изменился'}"
                      f" с {_percent(prev['gap_probability'])} до {_percent(new.risk.gap_probability)}.")
        invoice = e["source"] in INVOICE_SOURCES
        out.append(AlertDraft(
            "overdue_invoice" if invoice else "missed_inflow", "warning", f"inflow:{e['id']}",
            f"Не пришла оплата {rub(e['amount'])} от {who}",
            f"Ожидали к {dm(due)}.{effect}", {"event_id": e["id"], "days_late": m.days_late}))
    return out


def data_gap_alerts(ops: pd.DataFrame, as_of: date) -> list[AlertDraft]:
    """В выписке нет данных больше 5 рабочих дней подряд до as_of."""
    last = ops["date"].max().date()
    missing = workdays_between(last, as_of)
    if missing <= DATA_GAP_WORKDAYS:
        return []
    return [AlertDraft("data_gap", "warning", f"data_gap:{last.isoformat()}", "Нет свежих данных по счёту",
                       f"Последняя операция — {dm(last)}, дальше {missing} рабочих дней пусто. "
                       "Пока выписка не обновится, прогноз может быть неточным.", {"last_operation": last.isoformat()})]


def needs_review_alerts(ops: pd.DataFrame, as_of: date) -> list[AlertDraft]:
    """Нераспознанные операции за последние 30 дней — просим уточнить категорию."""
    recent = ops[(ops["category_source"] == "unknown")
                 & (ops["date"] > pd.Timestamp(as_of - timedelta(days=REVIEW_LOOKBACK_DAYS)))]
    if recent.empty:
        return []
    n = len(recent)
    return [AlertDraft("needs_review", "info", f"needs_review:{as_of.isoformat()}:{n}",
                       f"Нераспознанные операции ({n}) — уточните",
                       "Укажите, на что были эти платежи, — прогноз станет точнее.", {"count": n})]


def build_alerts(new: ForecastResult, ops: pd.DataFrame, prev_risk: dict | None = None,
                 matches: list[Match] | None = None) -> list[AlertDraft]:
    """Все предупреждения после обновления данных и пересчёта прогноза."""
    return (gap_risk_alerts(prev_risk, new) + mandatory_alerts(new)
            + missed_inflow_alerts(matches or [], prev_risk, new)
            + data_gap_alerts(ops, new.as_of) + needs_review_alerts(ops, new.as_of))
