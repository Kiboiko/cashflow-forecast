from datetime import date, timedelta

import pandas as pd
import pytest

from app.engine.calendar_ru import nominal_date, shift
from app.engine.categorize import categorize
from app.engine.enrich import enrich
from app.engine.quality import run_quality
from app.engine.recurring import detect_streams, expand_streams, inactive_notes
from tests.helpers import RAW_DIR, make_statement, merge, monthly, needs_dataset, op


def prepare(days: dict, as_of: date):
    ops = make_statement(days)
    return enrich(categorize(run_quality(ops, pd.Timestamp(as_of)).ops))


def by_key(streams):
    return {s.key: s for s in streams}


def test_salary_two_payments_weekend_shift_prev():
    days = merge(
        monthly("2025-03", 13, 25, "prev", 200_000.0, "Выплата аванса по заработной плате", inn=None, name="Сотрудник"),
        monthly("2025-03", 13, 10, "prev", 300_000.0, "Окончательный расчет по заработной плате", inn=None,
                name="Сотрудник"),
    )
    as_of = date(2026, 3, 31)
    ops = prepare(days, as_of)
    streams = by_key(detect_streams(ops, as_of))
    adv, fin = streams["salary:advance"], streams["salary:final"]
    assert (adv.anchor_day, adv.shift_rule, adv.period) == (25, "prev", "monthly")
    assert (fin.anchor_day, fin.shift_rule) == (10, "prev")
    assert adv.mandatory and adv.active and adv.direction == -1
    events = {e.id: e for e in expand_streams(list(streams.values()), as_of, 30)}
    assert date(2026, 4, 24) in {e.date for e in events.values()}      # 25.04 — суббота → пятница
    assert events["rec:salary:advance:2026-04-24"].amount == 200_000.0
    assert events["rec:salary:final:2026-04-10"].title == "Расчёт по зарплате"


def test_salary_from_sunday_goes_to_friday():
    days = merge(monthly("2025-04", 13, 10, "prev", 300_000.0, "Окончательный расчет по заработной плате", inn=None))
    as_of = date(2026, 4, 30)
    events = expand_streams(detect_streams(prepare(days, as_of), as_of), as_of, 30)
    assert [e.date for e in events] == [date(2026, 5, 8)]               # 10.05.2026 — воскресенье


def test_discontinued_stream_is_inactive_and_not_forecast():
    days = monthly("2025-06", 9, 5, "next", 80_000.0, "Арендная плата за помещение")   # последний платёж — февраль
    as_of = date(2026, 6, 30)
    streams = detect_streams(prepare(days, as_of), as_of)
    assert len(streams) == 1 and not streams[0].active
    assert expand_streams(streams, as_of, 30) == []
    assert "не платит" in inactive_notes(streams)[0]


def test_weekly_stream():
    d0 = date(2026, 3, 3)                                                # вторник
    days = {d0 + timedelta(days=7 * k): [op(-1, 5_000.0, "Услуги уборки помещений", inn="0055")] for k in range(16)}
    as_of = date(2026, 6, 19)
    streams = detect_streams(prepare(days, as_of), as_of)
    assert len(streams) == 1 and streams[0].period == "weekly" and streams[0].anchor_day == 1
    dates = [e.date for e in expand_streams(streams, as_of, 30)]
    assert dates == [date(2026, 6, 23), date(2026, 6, 30), date(2026, 7, 7), date(2026, 7, 14)]


def test_incoming_operation_with_an_outflow_category_does_not_join_the_stream():
    from app.engine.recurring import stream_keys

    ops = pd.DataFrame({"category": ["salary", "salary", "rent", "rent"], "direction": [-1, 1, -1, 1],
                        "cp_key": ["a", "b", "0099", "0099"], "salary_kind": ["final", "final", None, None]})
    keys = stream_keys(ops)
    assert keys.notna().tolist() == [True, False, True, False]
    assert keys.dropna().tolist() == ["salary:final", "rent:0099"]


def test_delayed_salary_anchor_comes_from_register_date():
    def purpose(p, d):
        reg = shift(nominal_date(p.year, p.month, 10), "prev")
        return f"Перечисление заработной платы (окончательный расчет) по реестру № 5 от {reg:%d.%m.%Y}"

    days = {}
    for k in range(13):                                                  # план 10-го, платят 24-го
        p = pd.Period("2025-03", "M") + k
        days[shift(nominal_date(p.year, p.month, 24), "next")] = [op(-1, 300_000.0, purpose(p, None), inn=None)]
    as_of = date(2026, 3, 31)
    stream = detect_streams(prepare(days, as_of), as_of)[0]
    assert stream.key == "salary:final" and stream.anchor_day == 10
    assert 12 <= stream.typical_delay_days <= 16
    # в прогнозе — плановая дата + обычная задержка: ждём выплату около 24 апреля, а не 10-го
    events = expand_streams([stream], as_of, 30)
    assert len(events) == 1 and date(2026, 4, 22) <= events[0].date <= date(2026, 4, 27)
    assert "по факту — обычно на" in events[0].explain


def test_mandatory_payment_not_made_yet_is_scheduled_as_overdue():
    days = monthly("2025-05", 13, 5, "next", 80_000.0, "Арендная плата за помещение")   # последний платёж 05.05.2026
    as_of = date(2026, 6, 12)
    streams = detect_streams(prepare(days, as_of), as_of)
    assert streams[0].active
    events = expand_streams(streams, as_of, 30)
    assert [e.date for e in events] == [date(2026, 6, 15), date(2026, 7, 6)]
    assert "ещё не прошёл" in events[0].explain


def test_payment_already_made_this_period_is_not_duplicated():
    days = monthly("2025-04", 14, 5, "next", 80_000.0, "Арендная плата за помещение")   # последний платёж 05.05.2026
    as_of = date(2026, 5, 20)
    events = expand_streams(detect_streams(prepare(days, as_of), as_of), as_of, 30)
    assert [e.date for e in events] == [date(2026, 6, 5)]


def test_utilities_use_same_month_last_year_scaled_to_current_level():
    base = {7: 5000, 8: 5000, 9: 6000, 10: 8000, 11: 12000, 12: 20000, 1: 22000, 2: 20000, 3: 15000,
            4: 10000, 5: 7000, 6: 6000}
    days = monthly("2025-07", 13, 15, "next", lambda p: base[p.month] * (1.1 if p.year == 2026 else 1.0),
                   "Оплата электроэнергии")
    as_of = date(2026, 7, 31)
    streams = detect_streams(prepare(days, as_of), as_of)
    event = expand_streams(streams, as_of, 30)[0]
    assert event.date == date(2026, 8, 17)                               # 15.08 — суббота
    assert event.amount == pytest.approx(5500.0)                         # август прошлого года × рост 1,1


def test_noise_streams_are_not_detected():
    days = {date(2026, 1, 12): [op(-1, 10_000.0, "Оплата рекламных услуг", inn="0066")],
            date(2026, 3, 3): [op(-1, 50_000.0, "Оплата рекламных услуг", inn="0066")],
            date(2026, 3, 30): [op(-1, 7_000.0, "Оплата рекламных услуг", inn="0066")]}
    as_of = date(2026, 4, 30)
    assert detect_streams(prepare(days, as_of), as_of) == []


@needs_dataset
@pytest.mark.parametrize("company, expected", [
    ("C064", {"salary:advance": 25, "salary:final": 15}),
    ("C103", {"salary:advance": 20, "salary:final": 10}),
    ("C107", {"salary:advance": 25, "salary:final": 10}),
])
def test_demo_companies_salary_and_rent_found_with_right_days(company, expected):
    from app.engine.io import load_statement
    from app.engine.normalize import normalize

    as_of = date(2026, 7, 31)
    report = run_quality(normalize(load_statement(RAW_DIR, company)), pd.Timestamp(as_of))
    streams = by_key(detect_streams(enrich(categorize(report.ops)), as_of))
    for key, day in expected.items():
        assert streams[key].anchor_day == day and streams[key].shift_rule == "prev"
    if company in {"C064", "C107"}:
        assert any(k.startswith("rent:") for k in streams)
