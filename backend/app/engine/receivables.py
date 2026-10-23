"""Счета, договоры и задержки клиентов (§6.8): «Вам должны», «Вы должны», внешние данные для прогноза.

Профиль задержки — эмпирическая выборка «дата оплаты − дата документа» по клиенту (≥5 оплат), иначе по всем клиентам
компании, иначе по отрасли, иначе по умолчанию. Открытый счёт — событие с РАСПРЕДЕЛЕНИЕМ даты оплаты: берутся
задержки клиента, которые больше возраста счёта (+ шум ±2 дня). Если счёт старше всех известных задержек клиента,
он «просрочен по меркам клиента»: шанс оплаты в горизонте 0,5, дата равномерно в ближайшие 30 дней.

Модель «выставление × задержка»: счета, которые ещё будут выставлены в горизонте и успеют оплатиться, — случайные
выставления (средняя выручка по документам, где уже всё оплачено) с задержкой из профиля.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.engine.calendar_ru import is_workday, shift
from app.engine.explain import dm, rub
from app.engine.simulate import lognormal_factor
from app.engine.types import PlannedEvent

CLIENT_CATEGORIES = ("client_payment", "client_prepayment")
SUPPLIER_CATEGORIES = ("suppliers", "contractors", "logistics", "marketing", "services", "telecom_it")
MIN_CLIENT_PAYMENTS = 5
MIN_COMPANY_DOCS = 10
SETTLED_DAYS = 75                   # документы старше — почти все оплачены, их задержки почти без цензурирования
DEFAULT_DELAYS = {1: [5, 10, 14, 21, 28, 35, 45, 60, 90], -1: [3, 6, 9, 12, 16, 21, 30]}
SMOOTH_DAYS = 2
OVERDUE_PAY_PROB = 0.5
OVERDUE_SPREAD_DAYS = 30
INVOICE_CV = 0.05
ISSUANCE_WINDOW_DAYS = 180
MIN_ISSUED_DOCS = 10
ISSUANCE_CV = 0.5
DEFAULT_PREPAID_SHARE = 0.57
MIN_CONTRACT_PAIRS = 5


@dataclass
class Invoice:
    """Открытый документ из учётной системы, CSV, 1С или ручного ввода. direction: in — должны нам, out — мы должны."""
    doc_no: str
    doc_date: date
    direction: str                          # "in" | "out"
    amount: float
    counterparty_inn: str | None = None
    counterparty_name: str | None = None
    doc_type: str = "invoice"
    due_date: date | None = None
    source: str = "csv"


@dataclass
class DelayProfile:
    delays: np.ndarray
    partial_share: float = 0.0
    level: str = "client"                   # client | company | industry | default
    n: int = 0

    @property
    def median(self) -> float:
        return float(np.median(self.delays))

    @property
    def spread(self) -> float:
        """Разброс «±»: половина межквартильного размаха."""
        q1, q3 = np.percentile(self.delays, [25, 75])
        return float((q3 - q1) / 2)


@dataclass
class Profiles:
    party: dict[tuple[int, str], DelayProfile] = field(default_factory=dict)
    company: dict[int, DelayProfile] = field(default_factory=dict)
    industry: dict[int, DelayProfile] = field(default_factory=dict)

    def get(self, direction: int, key: str | None) -> DelayProfile:
        """Клиент → компания → отрасль → по умолчанию."""
        if key is not None and (direction, key) in self.party:
            return self.party[direction, key]
        if direction in self.company:
            return self.company[direction]
        if direction in self.industry:
            return self.industry[direction]
        d = np.array(DEFAULT_DELAYS[direction], dtype=float)
        return DelayProfile(d, 0.0, "default", len(d))


def paid_documents(ops: pd.DataFrame, direction: int) -> pd.DataFrame:
    """Оплаченные документы: по (контрагент, номер документа) сумма, первая оплата, число частей, задержка."""
    cats = CLIENT_CATEGORIES if direction == 1 else SUPPLIER_CATEGORIES
    paid = ops[(ops["direction"] == direction) & ops["category"].isin(cats) & ops["doc_no"].notna()
               & ops["doc_date"].notna()]
    if "reversal_pair_id" in paid:
        paid = paid[paid["reversal_pair_id"].isna()]
    docs = paid.groupby(["cp_key", "doc_no", "doc_date"], as_index=False).agg(
        amount=("amount", "sum"), first_payment=("date", "min"), parts=("amount", "size"))
    docs["delay"] = (docs["first_payment"] - docs["doc_date"]).dt.days
    return docs[docs["delay"] >= 0]


def _profile(docs: pd.DataFrame, level: str) -> DelayProfile:
    return DelayProfile(docs["delay"].to_numpy(dtype=float), float((docs["parts"] > 1).mean()), level, len(docs))


def industry_profiles(ops_all: pd.DataFrame, companies: pd.DataFrame, as_of: date) -> dict[str, dict[int, DelayProfile]]:
    """Отрасль → {направление: профиль} по всем компаниям на данных ≤ as_of."""
    from app.engine.enrich import enrich

    industry_of = companies.set_index("company_id")["industry"]
    ops = ops_all[ops_all["date"] <= pd.Timestamp(as_of)]
    if "doc_no" not in ops:                                       # нужны номера и даты документов
        ops = enrich(ops)
    out: dict[str, dict[int, DelayProfile]] = defaultdict(dict)
    for direction in (1, -1):
        docs = paid_documents(ops.assign(cp_key=ops["company_id"] + "|" + ops["cp_key"]), direction)
        docs = docs[docs["doc_date"] <= pd.Timestamp(as_of - timedelta(days=SETTLED_DAYS))]
        docs["industry"] = docs["cp_key"].str.split("|").str[0].map(industry_of)
        for industry, g in docs.groupby("industry"):
            if len(g) >= MIN_COMPANY_DOCS:
                out[industry][direction] = _profile(g, "industry")
    return dict(out)


def build_profiles(ops: pd.DataFrame, as_of: date, industry: dict[int, DelayProfile] | None = None) -> Profiles:
    """Профили задержек по операциям ≤ as_of (категоризированным и обогащённым)."""
    ops = ops[ops["date"] <= pd.Timestamp(as_of)]
    profiles = Profiles(industry=dict(industry or {}))
    for direction in (1, -1):
        docs = paid_documents(ops, direction)
        settled = docs[docs["doc_date"] <= pd.Timestamp(as_of - timedelta(days=SETTLED_DAYS))]
        base = settled if len(settled) >= MIN_COMPANY_DOCS else docs
        if len(base) >= MIN_COMPANY_DOCS:
            profiles.company[direction] = _profile(base, "company")
        for key, g in base.groupby("cp_key"):
            if len(g) >= MIN_CLIENT_PAYMENTS:
                profiles.party[direction, str(key)] = _profile(g, "client")
    return profiles


# ---------- открытые счета ----------
def _smooth(day: date, weight: float, as_of: date) -> list[tuple[date, float]]:
    """Шум ±2 дня вокруг даты; платёж с рабочего дня переносится вперёд; не раньше завтрашнего дня."""
    out = []
    for k in range(-SMOOTH_DAYS, SMOOTH_DAYS + 1):
        d = shift(max(day + timedelta(days=k), as_of + timedelta(days=1)), "next")
        out.append((d, weight / (2 * SMOOTH_DAYS + 1)))
    return out


def payment_pmf(inv: Invoice, profile: DelayProfile, as_of: date) -> tuple[list[tuple[date, float]], bool]:
    """Распределение даты оплаты открытого счёта и признак «просрочен по меркам клиента»."""
    age = (as_of - inv.doc_date).days
    if inv.direction == "out" and inv.due_date:                       # счёт поставщика со сроком оплаты
        return _smooth(inv.due_date, 1.0, as_of)[: 2 * SMOOTH_DAYS + 1], False
    later = profile.delays[profile.delays > age]
    if len(later) == 0:                                               # старше всех известных задержек клиента
        days = [d for d in (as_of + timedelta(days=k) for k in range(1, OVERDUE_SPREAD_DAYS + 1)) if is_workday(d)]
        return [(d, OVERDUE_PAY_PROB / len(days)) for d in days], True
    pmf: dict[date, float] = defaultdict(float)
    for delay in later:
        for d, w in _smooth(inv.doc_date + timedelta(days=int(delay)), 1.0 / len(later), as_of):
            pmf[d] += w
    return sorted(pmf.items()), False


def _quantile_date(pmf: list[tuple[date, float]], q: float) -> date:
    total, acc = sum(p for _, p in pmf), 0.0
    for d, p in pmf:
        acc += p
        if acc >= q * total:
            return d
    return pmf[-1][0]


def invoice_events(invoices: list[Invoice], profiles: Profiles, as_of: date, horizon: int = 30) -> list[PlannedEvent]:
    """Открытые счета → плановые события с распределением даты оплаты."""
    end = as_of + timedelta(days=horizon)
    events = []
    for inv in invoices:
        direction = 1 if inv.direction == "in" else -1
        profile = profiles.get(direction, inv.counterparty_inn)
        pmf, overdue = payment_pmf(inv, profile, as_of)
        p_in = sum(p for d, p in pmf if d <= end)
        who = inv.counterparty_name or inv.counterparty_inn or "контрагента"
        lo, hi = _quantile_date(pmf, 0.1), _quantile_date(pmf, 0.9)
        if direction == 1:
            explain = (f"Счёт просрочен по меркам клиента: обычно платит через {profile.median:.0f}±{profile.spread:.0f} дн."
                       if overdue else
                       f"Оплата от {who} по {inv.doc_no} ≈{rub(inv.amount)} скорее всего придёт {dm(lo)}–{dm(hi)}: "
                       f"этот клиент обычно платит через {profile.median:.0f}±{profile.spread:.0f} дн.")
        else:
            explain = f"Счёт поставщика: оплата ожидается {dm(lo)}–{dm(hi)}"
        events.append(PlannedEvent(
            id=f"inv:{inv.direction}:{inv.doc_no}", direction=direction, amount=inv.amount,
            date=_quantile_date(pmf, 0.5), date_pmf=pmf, p_within_horizon=round(p_in, 3), amount_cv=INVOICE_CV,
            category="client_payment" if direction == 1 else "suppliers",
            title=(f"Оплата от {who} по {inv.doc_no}" if direction == 1 else f"Счёт поставщика {who}, {inv.doc_no}"),
            source=inv.source, mandatory=False, counterparty=inv.counterparty_name, explain=explain,
            counterparty_inn=inv.counterparty_inn,
        ))
    return events


# ---------- выставление × задержка ----------
@dataclass
class FutureInvoices:
    """Счета, которые будут выставлены в горизонте и успеют оплатиться: выставление × задержка из профиля."""
    direction: int
    dates: list[date]
    per_workday: float                      # средняя сумма выставлений за рабочий день
    delays: np.ndarray

    @property
    def key(self) -> str:
        return "future_invoices:in" if self.direction > 0 else "future_invoices:out"

    def expected(self) -> np.ndarray:
        """Ожидаемые платежи по дням горизонта (без случайности)."""
        out = np.zeros(len(self.dates))
        index = {d: i for i, d in enumerate(self.dates)}
        for d in self.dates:
            if is_workday(d):
                for delay in self.delays:
                    pay = shift(d + timedelta(days=int(delay)), "next")
                    if pay in index:
                        out[index[pay]] += self.per_workday / len(self.delays)
        return self.direction * out

    def sample(self, dates: list[date], n_paths: int, rng: np.random.Generator) -> np.ndarray:
        if list(dates) != self.dates:
            raise ValueError("Модель выставлений построена на другом горизонте")
        index = {d: i for i, d in enumerate(dates)}
        unique = np.unique(self.delays.astype(int))
        flows = np.zeros((n_paths, len(dates)))
        for d in dates:
            amount = self.per_workday * lognormal_factor(rng, ISSUANCE_CV, n_paths)       # случайные числа — всегда
            delay = rng.choice(self.delays, size=n_paths).astype(int)
            if not is_workday(d):
                continue
            pay_index = np.array([index.get(shift(d + timedelta(days=int(k)), "next"), -1) for k in unique])
            pay_idx = pay_index[np.searchsorted(unique, delay)]
            ok = pay_idx >= 0
            np.add.at(flows, (np.nonzero(ok)[0], pay_idx[ok]), amount[ok])
        return self.direction * flows


def _document_gross_up(ops: pd.DataFrame, as_of: date, direction: int) -> float:
    """Часть оплат идёт без номера документа (≈11% у клиентов): поднимаем выставления на их долю за последний год."""
    cats = CLIENT_CATEGORIES if direction == 1 else SUPPLIER_CATEGORIES
    year = ops[(ops["direction"] == direction) & ops["category"].isin(cats)
               & (ops["date"] > pd.Timestamp(as_of - timedelta(days=365))) & (ops["date"] <= pd.Timestamp(as_of))]
    documented = float(year.loc[year["doc_no"].notna(), "amount"].sum())
    return float(np.clip(year["amount"].sum() / documented, 1.0, 1.5)) if documented > 0 else 1.0


def fit_future_invoices(ops: pd.DataFrame, as_of: date, horizon: int, profiles: Profiles,
                        direction: int) -> FutureInvoices | None:
    """Средние выставления: документы за 180 дней до (as_of − 75 дней), когда почти все уже оплачены."""
    docs = paid_documents(ops[ops["date"] <= pd.Timestamp(as_of)], direction)
    hi = as_of - timedelta(days=SETTLED_DAYS)
    window = docs[(docs["doc_date"] <= pd.Timestamp(hi))
                  & (docs["doc_date"] > pd.Timestamp(hi - timedelta(days=ISSUANCE_WINDOW_DAYS)))]
    if len(window) < MIN_ISSUED_DOCS:
        return None
    workdays = sum(is_workday(hi - timedelta(days=k)) for k in range(ISSUANCE_WINDOW_DAYS))
    profile = profiles.get(direction, None)
    per_workday = float(window["amount"].sum()) / max(workdays, 1) * _document_gross_up(ops, as_of, direction)
    return FutureInvoices(direction, [as_of + timedelta(days=i) for i in range(1, horizon + 1)], per_workday,
                          profile.delays)


# ---------- демо «учётная система» ----------
def restore_open_documents(ops_full: pd.DataFrame, as_of: date) -> list[Invoice]:
    """Имитация 1С/ЭДО (§6.8): документы с датой ≤ as_of, первая оплата по которым позже as_of; сумма — все оплаты.

    Модель видит номер, дату и сумму документа, но НЕ дату оплаты. Подписывать как «демо-данные учётной
    системы, восстановлены из выписки» — это абляция «+внешние данные».
    """
    invoices = []
    for direction, label in ((1, "in"), (-1, "out")):
        cats = CLIENT_CATEGORIES if direction == 1 else SUPPLIER_CATEGORIES
        docs = ops_full[(ops_full["direction"] == direction) & ops_full["category"].isin(cats)
                        & ops_full["doc_no"].notna() & ops_full["doc_date"].notna()]
        g = docs.groupby(["cp_key", "doc_no", "doc_date"], as_index=False).agg(
            amount=("amount", "sum"), first_payment=("date", "min"), name=("counterparty_name", "first"),
            doc_type=("doc_type", "first"))
        g = g[(g["doc_date"] <= pd.Timestamp(as_of)) & (g["first_payment"] > pd.Timestamp(as_of))]
        for r in g.itertuples():
            invoices.append(Invoice(
                doc_no=str(r.doc_no), doc_date=r.doc_date.date(), direction=label, amount=float(r.amount),
                counterparty_inn=str(r.cp_key), counterparty_name=r.name, doc_type=r.doc_type if isinstance(r.doc_type, str) else "invoice",
                source="demo_accounting"))
    return invoices


def invoices_from_frame(df: pd.DataFrame, source: str = "csv") -> list[Invoice]:
    """Счета из таблицы (CSV §13): doc_type;doc_no;doc_date;direction;counterparty_inn;counterparty_name;amount;due_date."""
    out = []
    for r in df.to_dict("records"):
        due = pd.to_datetime(r.get("due_date"), errors="coerce")
        out.append(Invoice(
            doc_no=str(r["doc_no"]), doc_date=pd.to_datetime(r["doc_date"]).date(), direction=str(r["direction"]),
            amount=float(r["amount"]), counterparty_inn=None if pd.isna(r.get("counterparty_inn")) else str(r["counterparty_inn"]),
            counterparty_name=None if pd.isna(r.get("counterparty_name")) else str(r["counterparty_name"]),
            doc_type=str(r.get("doc_type") or "invoice"), due_date=None if pd.isna(due) else due.date(), source=source))
    return out


# ---------- договоры с предоплатой ----------
def contract_events(ops: pd.DataFrame, as_of: date, horizon: int = 30) -> list[PlannedEvent]:
    """Есть «Предоплата по договору № K», нет «Окончательный расчёт по договору № K» → ожидаемый расчёт.

    Сумма = предоплата × (1 − s) / s, s — медианная доля предоплаты компании; дата — по лагам компании при условии,
    что с предоплаты уже прошло столько-то дней.
    """
    ops = ops[(ops["date"] <= pd.Timestamp(as_of)) & ops["contract_no"].notna() & (ops["direction"] == 1)]
    pre = ops[ops["purpose_norm"].str.contains("предоплат")].groupby("contract_no").agg(
        amount=("amount", "sum"), date=("date", "min"), inn=("cp_key", "first"), name=("counterparty_name", "first"))
    fin = ops[ops["purpose_norm"].str.contains("окончательн")].groupby("contract_no").agg(
        amount=("amount", "sum"), date=("date", "max"))
    pairs = pre.merge(fin, left_index=True, right_index=True, how="inner", suffixes=("", "_final"))
    if len(pairs) < MIN_CONTRACT_PAIRS:
        return []
    share = float(np.median(pairs["amount"] / (pairs["amount"] + pairs["amount_final"])))
    lags = (pairs["date_final"] - pairs["date"]).dt.days.to_numpy(dtype=float)
    events = []
    for contract_no, r in pre[~pre.index.isin(fin.index)].iterrows():
        age = (as_of - r["date"].date()).days
        later = lags[lags > age]
        if len(later) == 0:
            continue
        pmf: dict[date, float] = defaultdict(float)
        for lag in later:
            for d, w in _smooth(r["date"].date() + timedelta(days=int(lag)), 1.0 / len(later), as_of):
                pmf[d] += w
        pmf_list = sorted(pmf.items())
        amount = float(r["amount"]) * (1 - share) / (share or DEFAULT_PREPAID_SHARE)
        events.append(PlannedEvent(
            id=f"contract:{contract_no}", direction=1, amount=round(amount, 2), date=_quantile_date(pmf_list, 0.5),
            date_pmf=pmf_list, p_within_horizon=round(sum(p for d, p in pmf_list if d <= as_of + timedelta(days=horizon)), 3),
            amount_cv=0.15, category="client_payment", title=f"Окончательный расчёт по договору № {contract_no}",
            source="contract_detected", counterparty=r["name"], counterparty_inn=str(r["inn"]),
            explain=f"Предоплата {rub(r['amount'])} получена {age} дн. назад; расчёт обычно приходит через {np.median(lags):.0f} дн."))
    return events


# ---------- «Вам должны» ----------
def receivables_summary(invoices: list[Invoice], profiles: Profiles, as_of: date, direction: str = "in") -> list[dict]:
    """По клиентам: открытые счета, сколько должны, обычная задержка и просрочка по меркам клиента."""
    d = 1 if direction == "in" else -1
    by_party: dict[str, list[Invoice]] = defaultdict(list)
    for inv in invoices:
        if inv.direction == direction:
            by_party[inv.counterparty_inn or inv.counterparty_name or "?"].append(inv)
    rows = []
    for items in by_party.values():
        profile = profiles.get(d, items[0].counterparty_inn)
        details = []
        for inv in sorted(items, key=lambda i: i.doc_date):
            age = (as_of - inv.doc_date).days
            pmf, overdue = payment_pmf(inv, profile, as_of)
            details.append({"doc_no": inv.doc_no, "doc_date": inv.doc_date, "amount": inv.amount, "age_days": age,
                            "expected_from": _quantile_date(pmf, 0.1), "expected_to": _quantile_date(pmf, 0.9),
                            "overdue_by_client_standard": overdue})
        rows.append({"counterparty_inn": items[0].counterparty_inn, "counterparty": items[0].counterparty_name,
                     "open_amount": sum(i.amount for i in items), "typical_delay_days": profile.median,
                     "delay_spread_days": profile.spread, "profile_level": profile.level, "invoices": details,
                     "overdue_amount": sum(x["amount"] for x in details if x["overdue_by_client_standard"])})
    return sorted(rows, key=lambda r: -r["open_amount"])
