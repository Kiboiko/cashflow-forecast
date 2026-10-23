"""План-факт, предупреждения и инкрементальное обновление состояния."""
import json
from datetime import date

import numpy as np
import pandas as pd

from app.engine.alerts import build_alerts, data_gap_alerts, needs_review_alerts
from app.engine.categorize import categorize
from app.engine.enrich import enrich
from app.engine.pipeline import run_forecast
from app.engine.planfact import make_snapshot, match_events, plan_fact
from app.engine.state import EngineState
from app.engine.types import Settings
from app.schemas import PlanFactResponse
from tests.helpers import RAW_DIR, make_statement, merge, monthly, needs_dataset, op

AS_OF = date(2026, 7, 31)
FACT_UNTIL = date(2026, 8, 21)


def history(balance_shift: float = 0.0):
    """Аренда 150 тыс. 5-го, зарплата 120 тыс. 25-го, поставщик 40 тыс. 12-го, платформа 270 тыс. 15-го."""
    return merge(
        monthly("2025-07", 13, 5, "next", 150_000.0, "Арендная плата за помещение", inn="0099"),
        monthly("2025-07", 13, 25, "prev", 120_000.0, "Выплата заработной платы за месяц", inn=None),
        monthly("2025-07", 13, 12, "next", 40_000.0, "Оплата рекламных услуг", inn="0055", name="ООО «Реклама»"),
        monthly("2025-07", 13, 15, "next", 270_000.0, "Вознаграждение по агентскому договору № 7", inn="0044",
                name="ООО «Платформа»", sign=1),
    )


def full_statement(future: dict | None = None, balance: float = 3_000_000.0):
    """Выписка с остатком `balance` на AS_OF (остаток на начало истории подбирается)."""
    days = merge(history(), future or {})
    net = sum(o["sign"] * o["amount"] for d, ops in days.items() if d <= AS_OF for o in ops)
    return make_statement(days, start=balance - net)


# ---------- план-факт ----------
def snapshot_for(balance: float = 3_000_000.0):
    ops = full_statement(balance=balance)
    res = run_forecast(ops[ops["date"] <= pd.Timestamp(AS_OF)], AS_OF, n_paths=200, seed=3)
    return make_snapshot(res, "T001"), res


def facts(rent: float | None = 150_000.0, supplier: float | None = 40_000.0, platform: float | None = None):
    future = {}
    if rent:
        future[date(2026, 8, 5)] = [op(-1, rent, "Арендная плата за помещение", inn="0099")]
    if supplier:
        future[date(2026, 8, 12)] = [op(-1, supplier, "Оплата рекламных услуг", inn="0055", name="ООО «Реклама»")]
    if platform:
        future[date(2026, 8, 17)] = [op(1, platform, "Вознаграждение по агентскому договору № 7", inn="0044")]
    ops = make_statement(merge(history(), future), start=1_000_000.0)
    return enrich(categorize(ops))


def statuses(matches):
    return {m.event["title"] + ":" + m.event["date"][5:]: m.status for m in matches}


def test_snapshot_is_json_serialisable_and_keeps_the_plan():
    snap, res = snapshot_for()
    again = json.loads(json.dumps(snap))
    assert again["as_of"] == "2026-07-31" and len(again["p50"]) == len(res.dates) == 30
    assert {e["title"] for e in again["events"]} >= {"Аренда", "Расчёт по зарплате"}
    assert all(e["grace_days"] >= 3 for e in again["events"])


def test_events_are_matched_by_party_amount_and_window():
    snap, _ = snapshot_for()
    m = {x.event["category"]: x for x in match_events(snap, facts(), FACT_UNTIL)}
    assert m["rent"].status == "fulfilled" and m["rent"].days_late == 0 and m["rent"].operation_id
    assert m["marketing"].status == "fulfilled" if "marketing" in m else True
    assert m["revenue_agent"].status == "overdue" and m["revenue_agent"].days_late == 4     # ждали 17-го, сегодня 21-е
    assert m["salary"].status == "planned"                                                   # срок 25-го ещё не наступил


def test_partial_payment_and_tolerance():
    snap, _ = snapshot_for()
    partial = {x.event["category"]: x for x in match_events(snap, facts(rent=90_000.0), FACT_UNTIL)}
    assert partial["rent"].status == "partially" and partial["rent"].amount == 90_000.0
    tiny = {x.event["category"]: x for x in match_events(snap, facts(rent=40_000.0), FACT_UNTIL)}
    assert tiny["rent"].status == "overdue"                                                  # меньше 30% — не оплата
    near = {x.event["category"]: x for x in match_events(snap, facts(rent=155_000.0), FACT_UNTIL)}
    assert near["rent"].status == "fulfilled"                                                # в пределах допуска 10%


def test_late_payment_inside_grace_is_still_matched_and_one_operation_is_used_once():
    snap, _ = snapshot_for()
    late = enrich(categorize(make_statement(merge(history(), {
        date(2026, 8, 17): [op(1, 270_000.0, "Вознаграждение по агентскому договору № 7", inn="0044")]}),
        start=1_000_000.0)))
    matches = {x.event["category"]: x for x in match_events(snap, late, FACT_UNTIL)}
    assert matches["revenue_agent"].status == "fulfilled"
    ids = [m.operation_id for m in match_events(snap, late, FACT_UNTIL) if m.operation_id]
    assert len(ids) == len(set(ids))


def test_plan_fact_response_follows_the_contract():
    snap, _ = snapshot_for()
    response, matches = plan_fact(snap, facts(), FACT_UNTIL, snapshot_id=12)
    assert isinstance(response, PlanFactResponse) and response.snapshot_id == 12
    assert response.summary.missed_events == sum(m.status == "overdue" for m in matches) >= 1
    known = [d for d in response.daily if d.date <= FACT_UNTIL]
    assert all(d.fact_balance is not None for d in known)
    assert all(d.fact_in is None for d in response.daily if d.date > FACT_UNTIL)
    assert response.summary.inflow_deviation_pct < 0                                         # платформа не заплатила
    PlanFactResponse.model_validate_json(response.model_dump_json())


# ---------- предупреждения ----------
def forecast_for_alerts(balance: float):
    ops = full_statement(balance=balance)
    ops = ops[ops["date"] <= pd.Timestamp(AS_OF)]
    return run_forecast(ops, AS_OF, n_paths=300, seed=3), enrich(categorize(ops))


def test_gap_risk_alert_for_a_new_red_forecast_and_not_again_when_unchanged():
    res, ops = forecast_for_alerts(50_000.0)
    alerts = build_alerts(res, ops)
    gap = [a for a in alerts if a.type == "gap_risk"]
    assert len(gap) == 1 and gap[0].severity == "critical" and "может не хватить" in gap[0].body
    assert any(a.type == "mandatory_at_risk" for a in alerts)
    same = {"level": res.risk.level, "gap_probability": res.risk.gap_probability}
    assert [a for a in build_alerts(res, ops, same) if a.type == "gap_risk"] == []


def test_gap_risk_alert_when_the_chance_grows():
    res, ops = forecast_for_alerts(50_000.0)
    prev = {"level": "red", "gap_probability": max(res.risk.gap_probability - 0.3, 0.0)}
    assert any(a.type == "gap_risk" and "Раньше шанс был" in a.body for a in build_alerts(res, ops, prev))


def test_no_alerts_for_a_comfortable_business():
    res, ops = forecast_for_alerts(5_000_000.0)
    quiet = build_alerts(res, ops)
    assert [a.type for a in quiet if a.type in {"gap_risk", "mandatory_at_risk"}] == []


def test_missed_inflow_alert_mentions_the_client_and_the_risk_change():
    snap, res = snapshot_for(balance=400_000.0)
    _, matches = plan_fact(snap, facts(), FACT_UNTIL)
    alerts = build_alerts(res, facts(), snap["risk"], matches)
    missed = [a for a in alerts if a.type in {"missed_inflow", "overdue_invoice"}]
    assert missed and "Не пришла оплата 270 000 ₽" in missed[0].title and "Ожидали к 17 августа" in missed[0].body
    assert missed[0].key.startswith("inflow:")


def test_data_gap_and_needs_review_alerts():
    ops = enrich(categorize(make_statement({date(2026, 7, 1): [op(1, 10.0, "Оплата по счету № 1 от 01.06.2026")],
                                            date(2026, 7, 2): [op(1, 5.0, "Загадочный платёж", inn=None)]})))
    assert data_gap_alerts(ops, date(2026, 7, 6)) == []
    gap = data_gap_alerts(ops, date(2026, 7, 20))
    assert gap[0].type == "data_gap" and "2 июля" in gap[0].body
    review = needs_review_alerts(ops, date(2026, 7, 6))
    assert review[0].type == "needs_review" and review[0].payload["count"] == 1


# ---------- инкрементальное обновление ----------
def test_update_categorizes_only_new_operations_and_keeps_history():
    ops = full_statement()
    cut = date(2026, 5, 31)
    old_state = EngineState.build(ops[ops["date"] <= pd.Timestamp(cut)], cut)
    new_state = old_state.update(ops[ops["date"] <= pd.Timestamp(AS_OF)], AS_OF)
    full_state = EngineState.build(ops[ops["date"] <= pd.Timestamp(AS_OF)], AS_OF)
    assert new_state.as_of == AS_OF and len(new_state.ops) == len(full_state.ops)
    key = ["operation_id", "category", "category_source", "doc_no", "salary_kind"]
    pd.testing.assert_frame_equal(new_state.ops[key].reset_index(drop=True), full_state.ops[key].reset_index(drop=True))
    assert old_state.as_of == cut and old_state.ops["date"].max() <= pd.Timestamp(cut)    # старое состояние не изменено


def test_update_is_idempotent_for_repeated_rows():
    ops = full_statement()
    state = EngineState.build(ops[ops["date"] <= pd.Timestamp(AS_OF)], AS_OF)
    again = state.update(ops[ops["date"] <= pd.Timestamp(AS_OF)], AS_OF)
    assert len(again.ops) == len(state.ops)


def test_forecast_after_update_equals_forecast_from_scratch():
    ops = full_statement()
    cut = date(2026, 5, 31)
    updated = EngineState.build(ops[ops["date"] <= pd.Timestamp(cut)], cut).update(
        ops[ops["date"] <= pd.Timestamp(AS_OF)], AS_OF)
    scratch = EngineState.build(ops[ops["date"] <= pd.Timestamp(AS_OF)], AS_OF)
    a, b = updated.forecast(n_paths=200, seed=1), scratch.forecast(n_paths=200, seed=1)
    np.testing.assert_allclose(a.p50, b.p50)
    assert a.risk.gap_probability == b.risk.gap_probability and a.start_balance == b.start_balance


def test_new_gap_in_extended_statement_is_detected_by_update():
    days = history()
    state_ops = make_statement(days, start=1_000_000.0)
    cut = date(2026, 6, 30)
    state = EngineState.build(state_ops[state_ops["date"] <= pd.Timestamp(cut)], cut)
    jump_day = date(2026, 7, 6)                                  # в этот день есть операции (аренда)
    shifted = make_statement(days, start=1_000_000.0, jumps={jump_day: -250_000.0})
    updated = state.update(shifted[shifted["date"] <= pd.Timestamp(AS_OF)], AS_OF)
    assert [g.end_date for g in updated.quality.gaps] == [jump_day]
    assert state.quality.gaps == []                                # исходное состояние не тронуто


@needs_dataset
def test_incremental_update_matches_full_rebuild_on_real_company():
    from app.engine.io import load_statement
    from app.engine.normalize import normalize

    ops = normalize(load_statement(RAW_DIR, "C064"))
    cut, later = date(2026, 6, 30), date(2026, 7, 31)
    state = EngineState.build(ops[ops["date"] <= pd.Timestamp(cut)], cut, Settings())
    updated = state.update(ops[ops["date"] <= pd.Timestamp(later)], later)
    full = EngineState.build(ops[ops["date"] <= pd.Timestamp(later)], later, Settings())
    same_category = (updated.ops["category"].values == full.ops["category"].values).mean()
    assert len(updated.ops) == len(full.ops) and same_category >= 0.995
    assert abs(updated.forecast(n_paths=200, seed=1).start_balance - full.forecast(n_paths=200, seed=1).start_balance) < 1e-6
