"""Пошаговый журнал расчёта прогноза: что модель увидела в данных и как получила ответ (для проверки и показа жюри).

Чистая функция над готовой моделью и результатом: ничего не пересчитывает, кроме сырой доли сценариев с разрывом.
Без ИНН и названий контрагентов — только категории, даты и округлённые суммы.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from app.engine.categorize.taxonomy import TAXONOMY, UNKNOWN
from app.engine.explain import dm, rub
from app.engine.model import Model
from app.engine.risk import assess_risk
from app.engine.types import ForecastResult

SOURCE_TITLES = {"user": "правила предпринимателя", "rule": "правила по назначению", "name_rule": "правила по имени",
                 "memory": "память контрагента", "ml": "ML-добивка", "unknown": "не распознано"}
MAX_LIST = 8


def _title(code: str) -> str:
    return TAXONOMY[code].title if code in TAXONOMY else code


def trace_forecast(model: Model, result: ForecastResult) -> list[str]:
    """Строки журнала по шагам конвейера (§6.0): данные → качество → категории → потоки → симуляция → риск → советы."""
    ops, q, s = model.ops, model.quality, model.settings
    out: list[str] = []

    out.append(f"Шаг 1. Данные: {len(ops)} операций с {ops['date'].min():%d.%m.%Y} по {ops['date'].max():%d.%m.%Y}, "
               f"прогноз на {model.horizon} дн. после {model.as_of:%d.%m.%Y}. Остаток на начало: {rub(model.start_balance)}.")

    gaps = ", ".join(f"{g.start_date:%d.%m}–{g.end_date:%d.%m} ({rub(g.unexplained_amount)})" for g in q.gaps[:MAX_LIST])
    history = {"full": "полная (больше года)", "no_full_year": "без полного года", "short_history": "короткая"}[q.history]
    out.append(f"Шаг 2. Качество данных: убрано подтверждённых дублей — {q.duplicates_removed}; пар «платёж — возврат» — "
               f"{q.reversal_pairs}; пропусков в выгрузке — {len(q.gaps)}{': ' + gaps if gaps else ''}; история {history}"
               f"{'; операций давно не было — прогноз не строим' if q.inactive else ''}.")

    known = ops[~ops["category"].isin(UNKNOWN)]
    sources = Counter(ops["category_source"])
    by_source = "; ".join(f"{SOURCE_TITLES.get(k, k)} — {v}" for k, v in sources.most_common())
    top = known.groupby("category")["amount"].sum().sort_values(ascending=False).head(MAX_LIST)
    out.append(f"Шаг 3. Категоризация: распознано {len(known) / max(len(ops), 1):.1%} операций ({by_source}). "
               "Крупнейшие категории по сумме: " + ", ".join(f"{_title(c)} {rub(v)}" for c, v in top.items()) + ".")

    active = [st for st in model.streams if st.active]
    lines = [f"{_title(st.category)} — {'еженедельно' if st.period == 'weekly' else 'ежеквартально' if st.period == 'quarterly' else 'ежемесячно'}"
             f" ({'день недели ' + str(st.anchor_day + 1) if st.period == 'weekly' else str(st.anchor_day) + '-го'}), "
             f"≈{rub(st.amount)}, регулярность {st.regularity:.0%}" for st in active[:MAX_LIST]]
    out.append(f"Шаг 4. Регулярные платежи и поступления: найдено {len(active)} активных"
               f"{', ещё ' + str(len(model.streams) - len(active)) + ' прекратились' if len(model.streams) > len(active) else ''}"
               + (": " + "; ".join(lines) if lines else "") + ".")

    taxes = [e for e in model.events if e.source == "tax_calendar"]
    out.append("Шаг 5. Налоговый календарь (ЕНП 28-го, авансы УСН в пиковые месяцы): "
               + ("; ".join(f"{dm(e.date)} ≈{rub(e.amount)}" for e in taxes) if taxes else "платежей ЕНП в горизонте нет") + ".")

    if model.revenue is not None:
        parts = []
        for ch in model.revenue.channels:
            lo, hi = np.percentile(ch.level_ratios, [10, 90])
            parts.append(f"{'карты и СБП' if ch.name == 'card' else 'наличные'}: продажи ≈{rub(float(np.mean(ch.s_hat)))} в день, "
                         f"разброс уровня P10–P90 ×{lo:.2f}…×{hi:.2f}")
        out.append(f"Шаг 6. Выручка (метод {model.revenue.method.upper()}: уровень × день недели × сезон, зачисление по дате продажи): "
                   + "; ".join(parts) + f"; ожидается за горизонт {rub(float(model.revenue.expected().sum()))}.")
    else:
        out.append("Шаг 6. Выручка по картам, СБП и наличным: таких продаж в истории нет.")

    rates = []
    for r in model.rates:
        cats = ", ".join(_title(c) for c, _ in sorted(r.category_share.items(), key=lambda kv: -kv[1])[:3])
        rates.append(f"{'приходы' if r.direction > 0 else 'расходы'} ≈{rub(float(r.weekly_sums.mean()))} в неделю "
                     f"по {r.weeks_used} неделям ({cats})")
    out.append("Шаг 7. Нерегулярные потоки (недельный бутстрэп): " + ("; ".join(rates) if rates else "нет") + ".")

    sources_ev = Counter(e.source for e in result.events)
    invoices = f"; счета и договоры: {len(model.invoices)} открытых документов" if model.invoices is not None else ""
    out.append(f"Шаг 8. План событий: {len(result.events)} — " + ", ".join(f"{k}: {v}" for k, v in sources_ev.most_common())
               + invoices + ".")

    sim = result.simulation
    raw = assess_risk(sim, model.as_of, s, result.events, calibrated=False).gap_probability if sim is not None else None
    out.append(f"Шаг 9. Монте-Карло: {result.model_info['n_paths']} сценариев (seed {result.model_info['seed']}), "
               f"компоненты: {', '.join(result.model_info['components'])}. Остаток через {model.horizon} дн.: "
               f"P10 {rub(float(result.p10[-1]))}, P50 {rub(float(result.p50[-1]))}, P90 {rub(float(result.p90[-1]))}.")

    r = result.risk
    when = r.possible_gap_date or r.likely_gap_date
    out.append(f"Шаг 10. Риск: доля сценариев с разрывом {raw:.1%} → откалиброванный шанс {r.gap_probability:.0%} "
               f"(порог жёлтого {s.risk_yellow:.0%}, красного {s.risk_red:.0%}) → уровень «{r.level}». "
               + (f"Возможная дата {dm(when)}, нехватка ≈{rub(r.shortfall_p50)} (в плохом сценарии {rub(r.shortfall_p90)}), "
                  f"подушка {rub(r.safe_buffer)}." if when else "Разрыва в горизонте не ожидается." if r.level == "green"
                  else f"Явной даты нет: ни в один день шанс нехватки не доходит до 20%; в плохом сценарии не хватит до "
                       f"{rub(r.shortfall_p90)}.") if raw is not None else
               f"Шаг 10. Риск: шанс {r.gap_probability:.0%}, уровень «{r.level}».")
    cover = [f"{c.title} {dm(c.date)} — {c.covered_probability:.0%}" for c in r.mandatory_coverage[:MAX_LIST]]
    if cover:
        out.append("Шаг 11. Обязательные платежи, хватит ли денег: " + "; ".join(cover) + ".")
    out.append("Шаг 12. Объяснение: " + r.headline)
    if result.recommendations:
        out.append("Шаг 13. Что сделать (эффект на тех же сценариях): " + "; ".join(
            f"{rec.title} → шанс {rec.effect.gap_probability_after:.0%}, нехватка ≈{rub(rec.effect.shortfall_p50_after)}"
            for rec in result.recommendations) + ".")
    return out


def compare_with_fact(result: ForecastResult, fact: pd.Series) -> str:
    """Строка «прогноз против факта» для горизонта: средняя ошибка медианы и попадание в коридор P10–P90."""
    days = [d for d in result.dates if pd.Timestamp(d) in fact.index]
    if not days:
        return "Факта после даты прогноза нет."
    idx = [result.dates.index(d) for d in days]
    actual = fact.loc[[pd.Timestamp(d) for d in days]].to_numpy(dtype=float)
    err = np.abs(result.p50[idx] - actual)
    inside = (actual >= result.p10[idx]) & (actual <= result.p90[idx])
    return (f"Сверка с фактом за {len(days)} дн.: средняя ошибка ожидаемого остатка {rub(float(err.mean()))}, "
            f"факт внутри коридора P10–P90 в {inside.mean():.0%} дней.")
