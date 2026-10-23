"""Рекомендации с эффектом (§6.15): кандидаты → пересчёт на ТЕХ ЖЕ случайных числах → отбор и сортировка.

Сначала бесплатные действия, кредит — последним. Зарплату и налоги переносить не предлагаем никогда.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.calendar_ru import shift
from app.engine.explain import dm, plural_raz, rub
from app.engine.model import Model
from app.engine.risk import RiskStats, assess_risk
from app.engine.simulate import Simulation
from app.schemas import Recommendation, RecommendationEffect

DEFERRABLE = {"suppliers", "marketing", "services", "contractors", "logistics", "telecom_it"}
DEFER_DAYS = 14                      # на сколько дней просим перенести платёж
CREDIT_DAYS = 14
DEFAULT_CREDIT_RATE = 0.22           # годовых; по умолчанию, пока нет адаптера ставки ЦБ (P2)
MIN_PROB_GAIN = 0.02                 # рекомендация должна заметно снизить шанс или нехватку
MIN_SHORTFALL_GAIN = 0.05
MAX_RECOMMENDATIONS = 4
OWN_MONEY_HEADROOM = 1.5             # реалистичная сумма: до 1,5 максимума прошлых вложений
ROUND_TO = 10_000.0


def _round_up(x: float) -> float:
    return float(math.ceil(x / ROUND_TO) * ROUND_TO)


def _gap_day(stats: RiskStats) -> date:
    return stats.possible_gap_date or stats.likely_gap_date or stats.peak_date


def _deadline(model: Model, stats: RiskStats) -> date:
    """Действовать нужно до дня перед возможным разрывом (но не раньше завтра)."""
    return max(model.dates[0], shift(_gap_day(stats) - timedelta(days=1), "prev"))


def _moved(sim: Simulation, key: str, days: int) -> Simulation:
    """Те же сценарии, но поток компонента сдвинут на `days` дней вперёд (платёж перенесён)."""
    m = sim.components[key]
    moved = np.zeros_like(m)
    if days < m.shape[1]:
        moved[:, days:] = m[:, : m.shape[1] - days]
    flows = sim.flows - m + moved
    return Simulation(
        sim.dates, sim.start_balance, flows,
        sim.inflow - np.maximum(m, 0) + np.maximum(moved, 0), sim.outflow - np.maximum(-m, 0) + np.maximum(-moved, 0),
        sim.start_balance + flows.cumsum(axis=1), sim.seed, sim.components,
    )


def _extra(model: Model, day: date, amount: float) -> np.ndarray:
    out = np.zeros(len(model.dates))
    out[model.dates.index(day)] = amount
    return out


def _effect(model: Model, sim: Simulation, stats: RiskStats) -> RecommendationEffect:
    after = assess_risk(sim, model.as_of, model.settings, model.events)
    return RecommendationEffect(gap_probability_after=round(after.gap_probability, 2),
                                shortfall_p50_after=round(after.shortfall_p50, 2))


def _own_money(model: Model, category: str) -> tuple[int, float]:
    """(сколько раз, максимальная сумма) вложения своих денег за год: перевод со своего счёта или займ владельца."""
    ops = model.ops
    past = ops[(ops["category"] == category) & (ops["date"] > pd.Timestamp(model.as_of - timedelta(days=365)))]
    return int(past["date"].nunique()), float(past["amount"].max()) if len(past) else 0.0


def _candidates(model: Model, sim: Simulation, stats: RiskStats):
    """(тип, заголовок, сумма, срок, симуляция после действия)."""
    gap, deadline = _gap_day(stats), _deadline(model, stats)
    out = []

    # 1. перенос необязательных платежей поставщикам, рекламе и т. п. из окна до разрыва
    deferrable = sorted((e for e in model.events if e.direction == -1 and not e.mandatory and e.date
                         and e.category in DEFERRABLE and model.dates[0] <= e.date <= gap),
                        key=lambda e: -e.amount)
    for e in deferrable[:2]:
        key = f"event:{e.id}"
        if sim.components and key in sim.components:
            new_day = e.date + timedelta(days=DEFER_DAYS)
            who = e.counterparty or e.title
            out.append(("defer_supplier", f"Договоритесь с {who} перенести {rub(e.amount)} с {dm(e.date)} на {dm(new_day)}",
                        e.amount, e.date, _moved(sim, key, DEFER_DAYS)))

    # 2. отложить вывод владельцу
    for e in (e for e in model.events if e.category == "owner_withdrawal" and e.date and model.dates[0] <= e.date <= gap):
        key = f"event:{e.id}"
        if sim.components and key in sim.components:
            out.append(("postpone_withdrawal",
                        f"Отложите вывод {rub(e.amount)} на личную карту до {dm(e.date + timedelta(days=30))}",
                        e.amount, e.date, _moved(sim, key, 30)))

    # 3. вложить свои деньги: перевод с другого счёта или займ владельца (как уже бывало в истории)
    n_transfer, big_transfer = _own_money(model, "own_transfer_in")
    n_owner, big_owner = _own_money(model, "owner_injection")
    other = sum(float(a.get("balance", 0.0)) for a in model.settings.other_accounts)
    if n_transfer or n_owner or other > 0:
        amount = _round_up(stats.safe_buffer)
        if other > 0:
            amount = min(amount, other)
        else:
            amount = min(amount, max(_round_up(max(big_transfer, big_owner) * OWN_MONEY_HEADROOM), ROUND_TO))
        n = max(n_transfer, n_owner)
        past = f" — вы так делали {n} {plural_raz(n)}" if n else ""
        if n_transfer >= n_owner:
            bank = next((a.get("bank") for a in model.settings.other_accounts if a.get("bank")), None)
            action = f"Переведите {rub(amount)} со счёта в {bank or 'другом банке'}"
        else:
            action = f"Внесите {rub(amount)} своих средств на счёт"
        out.append(("move_own_funds", f"{action} до {dm(deadline)}{past}", amount, deadline,
                    sim.with_extra_flows(_extra(model, deadline, amount))))

    # 4. кредитная линия — всегда последней
    amount = _round_up(stats.safe_buffer)
    days = max(CREDIT_DAYS, (model.dates[-1] - gap).days)
    cost = amount * DEFAULT_CREDIT_RATE * days / 365
    title = (f"Овердрафт или кредитная линия на {rub(amount)} на {days} дн. — ориентировочно {rub(cost)} "
             f"при ставке {DEFAULT_CREDIT_RATE:.0%} годовых")
    out.append(("credit_line", title, amount, deadline, sim.with_extra_flows(_extra(model, deadline, amount))))
    return out


def recommend(model: Model, sim: Simulation, stats: RiskStats) -> list[Recommendation]:
    """Рекомендации для жёлтого и красного уровня; эффект каждой — шанс и нехватка после действия."""
    if stats.level == "green":
        return []
    free, credit = [], []
    for kind, title, amount, deadline, after_sim in _candidates(model, sim, stats):
        effect = _effect(model, after_sim, stats)
        helps = (stats.gap_probability - effect.gap_probability_after >= MIN_PROB_GAIN
                 or effect.shortfall_p50_after <= stats.shortfall_p50 * (1 - MIN_SHORTFALL_GAIN))
        if not helps:
            continue
        rec = Recommendation(id="", type=kind, title=title, amount=round(amount, 2), deadline=deadline,
                             effect=effect, priority=0)
        (credit if kind == "credit_line" else free).append(rec)
    free.sort(key=lambda r: (r.effect.gap_probability_after, r.effect.shortfall_p50_after))
    ranked = (free + credit)[:MAX_RECOMMENDATIONS]
    for i, rec in enumerate(ranked, 1):
        rec.id, rec.priority = f"r{i}", i
    return ranked
