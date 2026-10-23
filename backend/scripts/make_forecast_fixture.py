"""Генерирует tests/fixtures/forecast_example.json — грубый прогноз по C064 на 31.07.2026 (задача A1).

Это ВРЕМЕННАЯ заглушка, чтобы B строил интерфейс, не дожидаясь движка (§8). Структура и числа берутся
из реальной истории C064, но модель здесь упрощена; настоящий прогноз даст `engine.pipeline.run_forecast`.
Запуск из backend/:  uv run python scripts/make_forecast_fixture.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.calendar_ru import is_workday, nominal_date, shift
from app.engine.categorize import categorize
from app.engine.enrich import enrich
from app.engine.io import load_statement
from app.engine.normalize import normalize
from app.engine.quality import run_quality
from app.schemas import (
    DailyPoint,
    DataQuality,
    Driver,
    ForecastEvent,
    ForecastResponse,
    GapOut,
    Insight,
    MandatoryCoverage,
    ModelInfo,
    Recommendation,
    RecommendationEffect,
    Risk,
)

ROOT = Path(__file__).resolve().parents[2]
COMPANY, AS_OF, HORIZON, N, SEED = "C064", date(2026, 7, 31), 30, 1000, 1234567
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]
REVENUE = {"revenue_card", "revenue_sbp", "revenue_cash", "revenue_marketplace", "revenue_agent",
           "client_payment", "client_prepayment"}
OTHER_OUT = {"suppliers", "services", "telecom_it", "bank_fees", "contractors", "logistics", "marketing"}


def rub(x: float) -> str:
    return f"{round(x, -3):,.0f}".replace(",", " ") + " ₽"


def dm(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def last_amounts(day_sums: pd.Series, k: int = 3) -> tuple[float, float]:
    tail = day_sums.tail(k)
    return float(tail.median()), float(np.clip(day_sums.tail(6).std() / max(day_sums.tail(6).mean(), 1), 0.02, 0.5))


def build() -> ForecastResponse:
    rng = np.random.default_rng(SEED)
    report = run_quality(normalize(load_statement(ROOT / "data" / "raw", COMPANY)), pd.Timestamp(AS_OF))
    ops = enrich(categorize(report.ops))
    ops = ops[(ops["date"] <= pd.Timestamp(AS_OF)) & ops["reversal_pair_id"].isna()]
    start = float(ops.loc[ops["date"] == ops["date"].max(), "balance_eod"].iloc[-1])
    dates = [AS_OF + timedelta(days=i) for i in range(1, HORIZON + 1)]
    idx = {d: i for i, d in enumerate(dates)}
    wd = np.array([is_workday(d) for d in dates])

    # --- регулярные платежи из истории: сумма — медиана 3 последних, день — мода последних 6 ---
    streams = []   # (key, category, title, rule, source, mandatory, amount, cv, anchor_day)
    specs = [("salary", "advance", "Аванс по зарплате", "prev"), ("salary", "final", "Расчёт по зарплате", "prev"),
             ("rent", None, "Аренда помещения", "next"), ("tax_enp", None, "Налоги (ЕНП)", "next"),
             ("utilities", None, "Коммунальные услуги", "next")]
    for cat, kind, title, rule in specs:
        sub = ops[(ops["category"] == cat) & (ops["direction"] == -1)]
        if kind:
            sub = sub[sub["salary_kind"] == kind]
        if sub.empty:
            continue
        per_day = sub.groupby("date")["amount"].sum()
        amount, cv = last_amounts(per_day)
        anchor = int(per_day.tail(6).index.day.to_series().mode().iloc[0])
        streams.append((cat + (":" + kind if kind else ""), cat, title, rule, amount, cv, anchor))

    events, flows_out = [], np.zeros((N, HORIZON))
    flows_in = np.zeros((N, HORIZON))
    for key, cat, title, rule, amount, cv, anchor in streams:
        next_month = (AS_OF.replace(day=1) + timedelta(days=32)).replace(day=1)
        for first_of_month in (AS_OF.replace(day=1), next_month):
            d = shift(nominal_date(first_of_month.year, first_of_month.month, anchor), rule)
            if d not in idx:
                continue
            flows_out[:, idx[d]] += amount * rng.lognormal(0, cv, N)
            events.append(ForecastEvent(
                id=f"{'tax' if cat == 'tax_enp' else 'rec'}:{key}:{d.isoformat()}", date=d, direction="out",
                amount=round(amount, 2), category=cat, title=title,
                source="tax_calendar" if cat == "tax_enp" else "recurring", probability=0.97,
                mandatory=cat != "utilities",
                explain=("ЕНП платите 28-го; сумма оценена по вашей истории" if cat == "tax_enp" else
                         f"Платите {anchor}-го последние 6 месяцев; сумма ≈ медиана 3 последних выплат")))

    # --- выручка и прочие расходы: средний рабочий день × случайный уровень на сценарий ---
    last28 = ops[ops["date"] > pd.Timestamp(AS_OF - timedelta(days=28))]
    n_wd28 = max(sum(is_workday(AS_OF - timedelta(days=k)) for k in range(28)), 1)
    rev_day = last28[last28["category"].isin(REVENUE) & (last28["direction"] == 1)]["amount"].sum() / n_wd28
    last84 = ops[ops["date"] > pd.Timestamp(AS_OF - timedelta(days=84))]
    n_wd84 = max(sum(is_workday(AS_OF - timedelta(days=k)) for k in range(84)), 1)
    oth_day = last84[last84["category"].isin(OTHER_OUT) & (last84["direction"] == -1)]["amount"].sum() / n_wd84
    level = rng.lognormal(0, 0.15, (N, 1))
    flows_in += rev_day * wd * level * rng.lognormal(0, 0.25, (N, HORIZON))
    flows_out += oth_day * wd * rng.lognormal(0, 0.15, (N, 1)) * rng.lognormal(0, 0.3, (N, HORIZON))

    # демонстрационная правка: стартовый остаток подбираем так, чтобы фикстура показывала красный риск
    p50_net = np.median(np.cumsum(flows_in - flows_out, axis=1), axis=0)
    start = min(start, float(-p50_net.min() + 0.15 * max(e.amount for e in events)))
    threshold = 0.0

    def assess(s0: float, extra_in: np.ndarray | None = None):
        fin = flows_in + (extra_in if extra_in is not None else 0)
        B = s0 + np.cumsum(fin - flows_out, axis=1)
        below = B < threshold
        short = np.maximum(threshold - B, 0).max(axis=1)
        return B, fin, below.mean(axis=0), below.any(axis=1).mean(), short

    B, fin, prob_below, gap_prob, short = assess(start)
    first = lambda thr: next((dates[i] for i, p in enumerate(prob_below) if p >= thr), None)
    has = short > 0
    sp50 = float(np.median(short[has])) if has.any() else 0.0
    sp90 = float(np.percentile(short[has], 90)) if has.any() else 0.0
    buffer = float(np.percentile(short, 90))
    possible, likely = first(0.2), first(0.5)
    level_ = "red" if gap_prob >= 0.4 or (likely and (likely - AS_OF).days <= 7) else "yellow" if gap_prob >= 0.1 else "green"

    coverage = []
    for e in (e for e in events if e.mandatory):
        i = idx[e.date]
        prev = B[:, i - 1] if i else np.full(N, start)
        coverage.append(MandatoryCoverage(event_id=e.id, title=e.title, date=e.date, amount=e.amount,
                                          covered_probability=round(float(np.mean(prev + fin[:, i] >= e.amount)), 2)))
    # платежи в окне вокруг возможной даты разрыва: 7 дней до и 2 дня после
    window = [e for e in events if possible and -7 <= (e.date - possible).days <= 2]
    names = " и ".join(e.title.lower() for e in sorted(window, key=lambda e: -e.amount)[:2]) or "обязательные платежи"
    pct = round(gap_prob * 100)
    headline = {
        "red": f"{dm(possible)} может не хватить около {rub(sp50)} на {names}. Шанс — высокий ({pct}%)." if possible else "",
        "yellow": f"Есть риск нехватки денег около {dm(possible) if possible else 'конца месяца'}: в плохом сценарии до {rub(sp90)}. Шанс — {pct}%.",
        "green": f"В ближайшие {HORIZON} дней денег хватает на все обязательные платежи.",
    }[level_]
    drivers = [Driver(kind="outflow", date=e.date, amount=e.amount, title=e.title, category=e.category)
               for e in sorted(window, key=lambda e: -e.amount)[:3]]
    by_month = ops[ops["category"].isin(REVENUE) & (ops["direction"] == 1)].groupby(ops["date"].dt.to_period("M"))["amount"].sum()
    if pd.Period("2026-05") in by_month.index and pd.Period("2026-07") in by_month.index:
        ratio = by_month[pd.Period("2026-07")] / by_month[pd.Period("2026-05")]
        drivers.append(Driver(kind="seasonality", text=f"В июле выручка на {round((1 - ratio) * 100)}% ниже, чем в мае"))

    # --- рекомендации: эффект пересчётом на тех же случайных числах ---
    recs = []
    def effect(extra_day: date, amount: float):
        add = np.zeros((N, HORIZON))
        add[:, idx[extra_day]] = amount
        _, _, _, gp, sh = assess(start, add)
        return RecommendationEffect(gap_probability_after=round(float(gp), 2),
                                    shortfall_p50_after=round(float(np.median(sh[sh > 0])) if (sh > 0).any() else 0.0, 2))
    if possible:
        deadline = max(AS_OF + timedelta(days=1), possible - timedelta(days=1))
        amt = float(np.ceil(buffer / 10_000) * 10_000)
        recs = [
            Recommendation(id="r1", type="move_own_funds", title=f"Переведите {rub(amt)} со счёта в другом банке до {dm(deadline)}",
                           amount=amt, deadline=deadline, effect=effect(deadline, amt), priority=1),
            Recommendation(id="r2", type="postpone_withdrawal", title=f"Отложите вывод {rub(amt / 2)} на личную карту до {dm(deadline + timedelta(days=14))}",
                           amount=amt / 2, deadline=deadline, effect=effect(deadline, amt / 2), priority=2),
            Recommendation(id="r3", type="credit_line", title=f"Овердрафт или кредитная линия на {rub(buffer)} на 14 дней — ориентировочно {rub(buffer * 0.2 * 14 / 365)} при ставке 20% годовых",
                           amount=buffer, deadline=deadline, effect=effect(deadline, buffer), priority=3),
        ]
    n_inj = int(((ops["date"] > pd.Timestamp(AS_OF - timedelta(days=365))) & ops["category"].isin(["owner_injection", "own_transfer_in"])).sum())
    insights = [Insight(kind="owner_injections", text=f"За год вы {n_inj} раз докладывали свои деньги, чтобы закрыть нехватку")] if n_inj else []

    p10, p50, p90 = (np.percentile(B, q, axis=0) for q in (10, 50, 90))
    daily = [DailyPoint(date=d, working_day=bool(wd[i]), p10=round(float(p10[i]), 2), p50=round(float(p50[i]), 2),
                        p90=round(float(p90[i]), 2), inflow_p50=round(float(np.median(fin[:, i])), 2),
                        outflow_p50=round(float(np.median(flows_out[:, i])), 2), prob_below=round(float(prob_below[i]), 3))
             for i, d in enumerate(dates)]
    return ForecastResponse(
        business_id=COMPANY, as_of=AS_OF, horizon_days=HORIZON, start_balance=round(start, 2), threshold=threshold,
        daily=daily, events=sorted(events, key=lambda e: e.date),
        risk=Risk(level=level_, gap_probability=round(float(gap_prob), 2), possible_gap_date=possible, likely_gap_date=likely,
                  shortfall_p50=round(sp50, 2), shortfall_p90=round(sp90, 2), safe_buffer=round(buffer, 2), headline=headline,
                  drivers=drivers, mandatory_coverage=coverage, flags=["owner_injections_history"] if n_inj else []),
        recommendations=recs, insights=insights,
        data_quality=DataQuality(data_as_of=AS_OF, gaps=[GapOut(start=g.start_date, end=g.end_date, unexplained_amount=g.unexplained_amount) for g in report.gaps],
                                 unknown_ops=int((ops["category_source"] == "unknown").sum()), history=report.history),
        model=ModelInfo(version="fixture-0", n_paths=N, seed=SEED, components=["recurring", "taxes", "revenue", "rates"]),
    )


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "forecast_example.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    resp = build()
    out.write_text(resp.model_dump_json(indent=2, by_alias=True, exclude_none=False), encoding="utf-8")
    print("written", out)
    print(resp.risk.level, resp.risk.gap_probability, resp.risk.headline)
    for r in resp.recommendations:
        print(" -", r.title, "→", r.effect.gap_probability_after)
