"""Риск кассового разрыва (§6.13) на матрице сценариев `B[N, H]`.

Вероятность — доля из N смоделированных сценариев, в которых остаток хотя бы один день ниже порога.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

from app.engine.calibration import calibrate
from app.engine.simulate import Simulation
from app.engine.types import PlannedEvent, Settings
from app.schemas import MandatoryCoverage

POSSIBLE_PROB = 0.2          # возможная дата разрыва — первый день с prob_below ≥ 0,2
LIKELY_PROB = 0.5            # вероятная — с prob_below ≥ 0,5
RED_IF_WITHIN_DAYS = 7
PAY_ORDER = {"salary": 0, "tax_enp": 1}  # очерёдность обязательных платежей одного дня при проверке покрытия


@dataclass
class RiskStats:
    level: str                               # green | yellow | red
    gap_probability: float                   # откалиброванный шанс разрыва (см. calibration.py)
    gap_probability_raw: float               # доля сценариев с разрывом
    prob_below: np.ndarray
    possible_gap_date: date | None
    likely_gap_date: date | None
    peak_date: date                          # день с наибольшим шансом нехватки (для текста, если даты нет)
    shortfall_p50: float
    shortfall_p90: float
    safe_buffer: float
    min_p50: float
    min_p50_date: date
    coverage: list[MandatoryCoverage] = field(default_factory=list)
    shortfall: np.ndarray | None = None      # нехватка по сценариям (для рекомендаций)


def _first_day(prob_below: np.ndarray, dates: list[date], p: float) -> date | None:
    hit = np.nonzero(prob_below >= p)[0]
    return dates[int(hit[0])] if len(hit) else None


def mandatory_coverage(sim: Simulation, events: list[PlannedEvent], threshold: float) -> list[MandatoryCoverage]:
    """Для каждого обязательного платежа: хватит ли денег в день платежа — `P(остаток до + приходы дня ≥ сумма)`.

    Несколько обязательных платежей в один день делят одни и те же деньги: сначала зарплата, затем налоги, затем
    остальные (крупные раньше), и каждый следующий проверяется на том, что осталось после предыдущих.
    """
    out, spent = [], {}
    for e in sorted((e for e in events if e.mandatory and e.direction == -1 and e.date in sim.dates),
                    key=lambda e: (e.date, PAY_ORDER.get(e.category, len(PAY_ORDER)), -e.amount)):
        i = sim.dates.index(e.date)
        before = sim.balance[:, i - 1] if i else np.full(sim.balance.shape[0], sim.start_balance)
        prior = spent.get(e.date, 0.0)
        spent[e.date] = prior + e.amount
        covered = float(np.mean(before + sim.inflow[:, i] - prior - e.amount >= threshold))
        out.append(MandatoryCoverage(event_id=e.id, title=e.title, date=e.date, amount=e.amount,
                                     covered_probability=round(covered, 2)))
    return out


def assess_risk(sim: Simulation, as_of: date, settings: Settings, events: list[PlannedEvent],
                calibrated: bool = True) -> RiskStats:
    """`calibrated=False` — сырая доля сценариев (для проверки арифметики и бэктеста)."""
    threshold = settings.effective_threshold
    below = sim.balance < threshold
    prob_below = below.mean(axis=0)
    gap_raw = float(below.any(axis=1).mean())
    gap_probability = calibrate(gap_raw) if calibrated else gap_raw
    shortfall = np.maximum(threshold - sim.balance, 0).max(axis=1)       # нехватка сценария
    has_gap = shortfall > 0
    p50 = float(np.median(shortfall[has_gap])) if has_gap.any() else 0.0
    p90 = float(np.percentile(shortfall[has_gap], 90)) if has_gap.any() else 0.0
    buffer = float(np.percentile(shortfall, 90))
    possible = _first_day(prob_below, sim.dates, POSSIBLE_PROB)
    likely = _first_day(prob_below, sim.dates, LIKELY_PROB)
    if gap_probability >= settings.risk_red or (likely and (likely - as_of).days <= RED_IF_WITHIN_DAYS):
        level = "red"
    elif gap_probability >= settings.risk_yellow:
        level = "yellow"
    else:
        level = "green"
    median_path = np.median(sim.balance, axis=0)
    i_min = int(np.argmin(median_path))
    return RiskStats(
        level=level, gap_probability=gap_probability, gap_probability_raw=gap_raw, prob_below=prob_below, possible_gap_date=possible,
        likely_gap_date=likely, peak_date=sim.dates[int(np.argmax(prob_below))], shortfall_p50=p50,
        shortfall_p90=p90, safe_buffer=buffer, min_p50=float(median_path[i_min]), min_p50_date=sim.dates[i_min],
        coverage=mandatory_coverage(sim, events, threshold), shortfall=shortfall,
    )
