"""Регулярные платежи и поступления (§6.6): зарплата, аренда, кредиты, лизинг, связь и т. п.

`detect_streams` учит потоки на операциях ≤ as_of; `expand_streams` раскладывает их на даты горизонта.
Нужны категоризированные и обогащённые операции одного бизнеса (categorize + enrich + quality).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.calendar_ru import nominal_date, shift
from app.engine.categorize.taxonomy import TAXONOMY
from app.engine.types import PlannedEvent, RecurringStream

# категория → направление потока
CANDIDATES: dict[str, int] = {
    "salary": -1, "rent": -1, "utilities": -1, "telecom_it": -1, "bank_fees": -1, "loan_repayment": -1,
    "leasing": -1, "services": -1, "contractors": -1, "marketing": -1, "owner_withdrawal": -1,
    "revenue_agent": 1, "client_payment": 1,
}
SHIFT_RULE = {"salary": "prev"}          # остальные платежи при выходном переносятся вперёд
HISTORY_MONTHS = 13
MIN_PAYMENTS = 3
PERIOD_DAYS = {"monthly": 30, "weekly": 7, "quarterly": 91}
MAX_REGISTER_DELAY_DAYS = 45
MAX_OVERDUE_DAYS = 20                    # дольше просрочку не достраиваем: поток, скорее всего, остановился
SALARY_TITLE ={"advance": "Аванс по зарплате", "final": "Расчёт по зарплате"}
_WEEKDAYS = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]


def stream_keys(ops: pd.DataFrame) -> pd.Series:
    """Ключ потока `(категория, контрагент[, подвид зарплаты])`; None для остальных операций.

    Зарплата не делится по контрагентам: выплаты бывают реестром или переводами каждому сотруднику.
    """
    key = (ops["category"] + ":" + ops["cp_key"].astype(str)).where(ops["category"].isin(CANDIDATES))
    sal = ops["category"] == "salary"
    if sal.any():
        key[sal] = "salary:" + ops.loc[sal, "salary_kind"].fillna("final")
    return key


def _detect_period(dates: pd.DatetimeIndex) -> str | None:
    """Медианный интервал 25–35 → monthly; 6–8 → weekly; 85–95 → quarterly."""
    median_gap = float(np.median(np.diff(dates.values).astype("timedelta64[D]").astype(int)))
    if 25 <= median_gap <= 35:
        return "monthly"
    if 6 <= median_gap <= 8:
        return "weekly"
    if 85 <= median_gap <= 95:
        return "quarterly"
    return None


def _share_of_recent(dates: pd.DatetimeIndex, period: str) -> float:
    """Доля из последних 6 периодов (месяцев/недель), заканчивающихся последним платежом, с платежом."""
    freq = "W" if period == "weekly" else "M"
    present = set(dates.to_period(freq))
    last = max(present)
    return len({last - k for k in range(6)} & present) / 6


def _neighbour_months(d: date) -> list[tuple[int, int]]:
    ts = pd.Period(d, "M")
    return [((ts + k).year, (ts + k).month) for k in (-1, 0, 1)]


def _anchor_day(dates: list[date], rule: str) -> int:
    """Номинальный день месяца, который лучше всего объясняет даты платежей с учётом переносов.

    Платёж «совпал» с якорем a, если он пришёлся на shift(номинальная дата a, правило). Побеждает якорь
    с максимумом совпадений, затем — с максимумом платежей ровно в этот день, затем меньший день.
    """
    best, best_day = None, 1
    for a in range(1, 32):
        matches = exact = 0
        for d in dates:
            matches += d in {shift(nominal_date(y, m, a), rule) for y, m in _neighbour_months(d)}
            exact += d.day == a
        score = (matches, exact, -a)
        if best is None or score > best:
            best, best_day = score, a
    return best_day


def _cv(values: pd.Series) -> float:
    mean = float(values.mean())
    return float(np.clip(values.std(ddof=0) / mean, 0.02, 0.5)) if mean > 0 else 0.5


def _seasonal_amounts(per_day: pd.Series) -> dict[int, float] | None:
    """Сезонные суммы (коммуналка): месяц → сумма того же месяца год назад, приведённая к текущему уровню.

    Уровень = отношение последних платежей к тем же месяцам год назад (среди тех, для которых они есть).
    """
    by_month = per_day.groupby(per_day.index.to_period("M")).sum()
    recent = by_month.tail(3)
    pairs = [(v, by_month.get(p - 12)) for p, v in recent.items()]
    pairs = [(v, b) for v, b in pairs if b is not None and b > 0]
    if not pairs:
        return None
    growth = sum(v for v, _ in pairs) / sum(b for _, b in pairs)
    return {p.month: float(v) * growth for p, v in by_month.items() if p not in recent.index} or None


def _plan_dates(g: pd.DataFrame) -> pd.Series:
    """Плановая дата каждой операции: дата реестра, если она не позже факта и не старше 45 дней, иначе дата факта.

    Так задержанные выплаты (расчёт по реестру от 10-го, оплаченный 24-го) не ломают период и якорь.
    """
    if "register_date" not in g:
        return g["date"]
    delay = (g["date"] - g["register_date"]).dt.days
    use_register = g["register_date"].notna() & delay.between(0, MAX_REGISTER_DELAY_DAYS)
    return g["date"].where(~use_register, g["register_date"])


def _detect_one(key: str, g: pd.DataFrame, as_of: date) -> RecurringStream | None:
    plan = _plan_dates(g)
    per_day = g.groupby(plan)["amount"].sum()          # выплаты отдельным сотрудникам → один платёж дня
    if len(per_day) < MIN_PAYMENTS:
        return None
    period = _detect_period(per_day.index)
    if period is None:
        return None
    share = _share_of_recent(per_day.index, period)
    if period != "quarterly" and share < 4 / 6:         # платежи не меньше чем в 4 из последних 6 периодов
        return None

    category = key.split(":")[0]
    rule = SHIFT_RULE.get(category, "next")
    dates = [ts.date() for ts in per_day.index]
    last = g["date"].max().date()                        # последний ФАКТИЧЕСКИЙ платёж
    delay = int(np.median((g["date"] - plan).dt.days))
    if period == "weekly":
        anchor = Counter(d.weekday() for d in dates[-12:]).most_common(1)[0][0]
    else:
        anchor = _anchor_day(dates[-13:], rule)
    amount, cv = float(per_day.tail(3).median()), _cv(per_day.tail(6))
    idle_days = (as_of - last).days
    active = idle_days <= 1.5 * PERIOD_DAYS[period]
    name = g["counterparty_name"].dropna().mode()
    counterparty = str(name.iloc[0]) if len(name) else None

    if not active:
        explain = f"{counterparty or 'Контрагент'} не платит {idle_days} дн. — исключили из прогноза"
    elif period == "weekly":
        explain = f"Платёж по {_WEEKDAYS[anchor]} последние недели; сумма ≈ медиана 3 последних"
    else:
        explain = (f"Платите {anchor}-го последние {min(6, len(per_day))} мес.; "
                   "сумма ≈ медиана 3 последних выплат")

    seasonal = _seasonal_amounts(per_day) if category == "utilities" and period == "monthly" else None

    return RecurringStream(
        key=key, category=category, direction=CANDIDATES[category], period=period, anchor_day=anchor,
        shift_rule=rule, amount=amount, amount_cv=cv, last_date=last, active=active,
        confidence=float(np.clip(share * (1 - cv), 0.0, 1.0)), mandatory=TAXONOMY[category].mandatory,
        counterparty=counterparty, explain=explain, seasonal_amounts=seasonal, typical_delay_days=delay,
    )


def detect_streams(ops: pd.DataFrame, as_of: date) -> list[RecurringStream]:
    """Потоки по данным за 13 месяцев до as_of, без дней дыр и сторно-пар."""
    as_of_ts = pd.Timestamp(as_of)
    hist = ops[(ops["date"] <= as_of_ts) & (ops["date"] > as_of_ts - pd.DateOffset(months=HISTORY_MONTHS))]
    if "gap_adjacent" in hist:
        hist = hist[~hist["gap_adjacent"].astype(bool)]
    if "reversal_pair_id" in hist:
        hist = hist[hist["reversal_pair_id"].isna()]
    keys = stream_keys(hist)
    streams = []
    for key, g in hist[keys.notna()].groupby(keys[keys.notna()]):
        stream = _detect_one(str(key), g, as_of)
        if stream is not None:
            streams.append(stream)
    return streams


def _title(s: RecurringStream) -> str:
    if s.category == "salary":
        return SALARY_TITLE[s.key.split(":")[1]]
    if s.category == "client_payment":
        return f"Оплата от {s.counterparty}" if s.counterparty else TAXONOMY[s.category].title
    return TAXONOMY[s.category].title


def _stream_dates(s: RecurringStream, as_of: date, end: date) -> list[date]:
    if s.period == "weekly":
        out, d = [], s.last_date + timedelta(days=7)
        while d <= end:
            out.append(shift(d, "next"))
            d += timedelta(days=7)
        return [d for d in out if d > as_of]
    step = 1 if s.period == "monthly" else 3
    first = pd.Period(as_of, "M")
    if s.period == "quarterly":                           # месяцы платежей: месяц последнего + 3k
        offset = (first - pd.Period(s.last_date, "M")).n % 3
        first = first + ((3 - offset) % 3)
    months = pd.period_range(first, pd.Period(end, "M"), freq="M")[::step]
    skip_within = 0.3 * PERIOD_DAYS[s.period]
    out = []
    for p in months:
        d = shift(nominal_date(p.year, p.month, s.anchor_day), s.shift_rule)
        if as_of < d <= end and (d - s.last_date).days >= skip_within:   # платёж этого периода уже был
            out.append(d)
    return out


def _overdue_date(s: RecurringStream, as_of: date) -> date | None:
    """Обязательный месячный платёж этого месяца должен был пройти, но ещё не прошёл → ближайший рабочий день."""
    if s.period != "monthly" or not s.mandatory:
        return None
    due = shift(nominal_date(as_of.year, as_of.month, s.anchor_day), s.shift_rule)
    if due <= as_of and (as_of - due).days <= MAX_OVERDUE_DAYS and s.last_date < due - timedelta(days=5):
        return shift(as_of + timedelta(days=1), "next")
    return None


def expand_streams(streams: list[RecurringStream], as_of: date, horizon: int = 30) -> list[PlannedEvent]:
    """Активные потоки → плановые события в (as_of, as_of + horizon]."""
    end = as_of + timedelta(days=horizon)
    events = []
    for s in streams:
        if not s.active:
            continue
        dates = [(d, s.explain) for d in _stream_dates(s, as_of, end)]
        overdue = _overdue_date(s, as_of)
        if overdue is not None and overdue <= end:
            dates.append((overdue, "Платёж за этот месяц ещё не прошёл — ждём его в ближайшие дни"))
        for d, explain in dates:
            amount = s.amount
            if s.seasonal_amounts and d.month in s.seasonal_amounts:
                amount = s.seasonal_amounts[d.month]
            events.append(PlannedEvent(
                id=f"rec:{s.key}:{d.isoformat()}", direction=s.direction, amount=round(amount, 2), date=d,
                p_within_horizon=min(0.99, 0.5 + 0.5 * s.confidence), amount_cv=s.amount_cv,
                category=s.category, title=_title(s), source="recurring", mandatory=s.mandatory,
                counterparty=s.counterparty, explain=explain,
            ))
    return sorted(events, key=lambda e: (e.date, e.id))


def inactive_notes(streams: list[RecurringStream]) -> list[str]:
    """Сообщения о потоках, исключённых из прогноза: «ООО X не платит 60 дней»."""
    return [s.explain for s in streams if not s.active]
