"""Бэктест прогноза (§7): сравнение подходов на отсечках по концам месяцев.

Настройка параметров — отсечки 31.10.2025 … 28.02.2026, итоговые цифры — 31.03 … 31.08.2026 (тест).
Запуск из backend/:  uv run python scripts/backtest.py [--companies N] [--paths 300] [--jobs -1]
Результат: reports/backtest_<дата>.json, reports/backtest.md, reports/figures/*.png
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from joblib import Parallel, delayed

from app.engine.backtest import (
    FLOW_GROUPS,
    HORIZONS,
    NAIVE,
    RED_PROB,
    RED_WITHIN_DAYS,
    SEASONAL_NAIVE,
    STRUCTURAL,
    STRUCTURAL_CAL,
    STRUCTURAL_EXT,
    STRUCTURAL_M0,
    STRUCTURAL_M2,
    YELLOW_PROB,
    detection_metrics,
    reliability,
    run_company,
)
from app.engine.calibration import CALIBRATION_PATH, apply_platt, fit_platt
from app.engine.categorize import categorize
from app.engine.context import Context, compute_industry_season
from app.engine.enrich import enrich
from app.engine.io import load_companies, load_operations
from app.engine.normalize import normalize
from app.engine.receivables import industry_profiles
from app.engine.revenue import sales_series, train_revenue_m2
from app.engine.taxes import compute_enp_index

ROOT = Path(__file__).resolve().parents[2]
TUNE = [date(2025, 10, 31), date(2025, 11, 30), date(2025, 12, 31), date(2026, 1, 31), date(2026, 2, 28)]
TEST = [date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30), date(2026, 7, 31),
        date(2026, 8, 31)]
TITLES = {NAIVE: "Наивный (остаток не меняется)", SEASONAL_NAIVE: "Сезонно-наивный (потоки прошлых недель)",
          STRUCTURAL_M0: "Структурный, выручка M0", STRUCTURAL: "Структурный, выручка M1",
          STRUCTURAL_CAL: "Структурный + калибровка шанса",
          STRUCTURAL_EXT: "+ внешние данные (демо-учётная система)",
          STRUCTURAL_M2: "Структурный + ML, выручка M2 (бустинг)"}
DEMO_POINTS = {("C064", date(2026, 7, 31)), ("C103", date(2026, 7, 31)), ("C107", date(2026, 7, 31))}


def add_calibrated(points: list[dict], tune_set: set[str], eps: float = 1e-3) -> tuple[float, float]:
    """Калибровка Платта: подбор на отсечках настройки (горизонт 30), применение ко всем точкам и горизонтам."""
    tune = [p for p in points if p["cutoff"] in tune_set]
    a, b = fit_platt([p["rows"][STRUCTURAL]["gap"][30] for p in tune], [p["y"][30] for p in tune], eps)
    for p in points:
        row = copy.deepcopy(p["rows"][STRUCTURAL])
        row["gap"] = {h: float(apply_platt(v, a, b, eps)) for h, v in row["gap"].items()}
        p["rows"][STRUCTURAL_CAL] = row
    return a, b


def train_m2_at(cat: pd.DataFrame, industry: pd.Series, codes: dict[str, int], cutoff: date):
    """Глобальный M2 на продажах по картам всех компаний до отсечки (без утечек)."""
    items = {}
    for cid, g in cat[cat["date"] <= pd.Timestamp(cutoff)].groupby("company_id"):
        series = sales_series(g, cutoff, "card")
        if series is not None:
            items[cid] = (series, codes.get(industry.get(cid), 0))
    return train_revenue_m2(items)


def build_contexts(ops_all: pd.DataFrame, companies: pd.DataFrame, cutoffs: list[date], with_m2: bool = True) -> dict:
    """{компания: {отсечка: Context}} на данных ≤ отсечки (отраслевая сезонность, индекс ЕНП)."""
    cat = enrich(categorize(ops_all))
    industry = companies.set_index("company_id")["industry"]
    codes = {name: i for i, name in enumerate(sorted(industry.unique()))}
    out: dict[str, dict] = {c: {} for c in companies["company_id"]}
    for cutoff in cutoffs:
        seasons = compute_industry_season(cat, companies, cutoff)
        enp = compute_enp_index(cat, cutoff)
        delays = industry_profiles(cat, companies, cutoff)
        m2 = train_m2_at(cat, industry, codes, cutoff) if with_m2 else None
        for cid in out:
            out[cid][cutoff] = Context(industry_season=seasons.get(industry.get(cid)), enp_index=enp,
                                       industry_delays=delays.get(industry.get(cid)), revenue_m2=m2,
                                       industry_code=codes.get(industry.get(cid), 0))
    return out


def _median(values):
    values = [v for v in values if v is not None]
    return float(np.median(values)) if values else None


def error_summary(errors: list[dict]) -> dict:
    """MAE, MSE, RMSE в рублях по точкам (среднее и медиана MAE) и WAPE = Σ|ошибка| / Σ|факт|."""
    if not errors:
        return {}
    mae = np.array([e["mae"] for e in errors])
    mse = np.array([e["mse"] for e in errors])
    actual = sum(e["abs_actual"] for e in errors)
    return {"mae_rub_mean": float(mae.mean()), "mae_rub_median": float(np.median(mae)), "mse_rub2": float(mse.mean()),
            "rmse_rub": float(np.sqrt(mse.mean())), "wape": float(mae.sum() / actual) if actual > 0 else None}


def aggregate(points: list[dict], names: list[str] | None = None, horizons: tuple[int, ...] = HORIZONS) -> dict:
    """Метрики по подходам и горизонтам для набора точек (компания × отсечка)."""
    result = {}
    for name in names or [n for n in TITLES if points and n in points[0]["rows"]]:
        per_h = {}
        for h in horizons:
            y = np.array([p["y"][h] for p in points])
            gap = np.array([p["rows"][name]["gap"][h] for p in points])
            caught = [p for p in points if p["y"][h] and p["rows"][name]["gap"][h] >= YELLOW_PROB]
            errors = [p["rows"][name]["errors"][h] for p in points if "errors" in p["rows"][name]]
            per_h[str(h)] = {
                **error_summary(errors),
                "rel_mae_median": _median([p["rows"][name]["rel_mae"][h] for p in points]),
                "rel_mae_mean": float(np.mean([p["rows"][name]["rel_mae"][h] for p in points])),
                "interval_coverage": float(np.mean([p["rows"][name]["coverage"][h] for p in points])),
                "detection": detection_metrics(y, gap),
                "detection_red": detection_metrics(y, gap, RED_PROB, pred=(gap >= RED_PROB) | np.array(
                    [(p["rows"][name].get("likely_day") or 99) <= RED_WITHIN_DAYS for p in points])),
                "lead_days_median": _median([p["first_gap"] for p in caught]),
                "date_error_days_median": _median([abs(p["rows"][name]["possible_day"] - p["first_gap"])
                                                   for p in caught if p["rows"][name]["possible_day"]]),
                "shortfall_error_rel_median": _median([abs(p["rows"][name]["shortfall_p50"] - p["max_shortfall"])
                                                       / p["scale"] for p in caught]),
            }
        result[name] = {"title": TITLES[name], "horizons": per_h}
    return result


def flows_wape(points: list[dict]) -> dict:
    """WAPE и смещение прогноза 30-дневных сумм по группам потоков: Σ|прогноз−факт| / Σфакт."""
    out = {}
    for g in FLOW_GROUPS:
        pred = np.array([p["flows"]["predicted"][g] for p in points])
        act = np.array([p["flows"]["actual"][g] for p in points])
        out[g] = {"wape": float(np.abs(pred - act).sum() / act.sum()) if act.sum() > 0 else None,
                  "bias": float((pred.sum() - act.sum()) / act.sum()) if act.sum() > 0 else None,
                  "actual_share": float(act.sum())}
    total = sum(v["actual_share"] for v in out.values())
    for v in out.values():
        v["actual_share"] = v["actual_share"] / total if total else None
    return out


def revenue_methods(points: list[dict]) -> dict:
    """Прогноз 30-дневной выручки (карты, СБП, наличные) тремя методами: WAPE, смещение, медианная ошибка по точкам.
    `card_companies` — точки, где M2 вообще работает (у бизнеса есть эквайринг); для остальных M2 равен M1."""
    out = {}
    subsets = {"all": points, "card_companies": [p for p in points
                                                  if p["revenue_pred"][STRUCTURAL_M2] != p["revenue_pred"][STRUCTURAL]]}
    for key, subset in subsets.items():
        subset = [p for p in subset if p["flows"]["actual"]["retail_revenue"] > 0]
        if not subset:
            continue
        act = np.array([p["flows"]["actual"]["retail_revenue"] for p in subset])
        out[key] = {"n_points": len(subset)}
        for name in (STRUCTURAL_M0, STRUCTURAL, STRUCTURAL_M2):
            pred = np.array([p["revenue_pred"][name] for p in subset])
            out[key][name] = {"wape": float(np.abs(pred - act).sum() / act.sum()),
                              "bias": float(pred.sum() / act.sum() - 1),
                              "median_error": float(np.median(np.abs(pred - act) / act))}
    return out


def external_ablation(points: list[dict]) -> dict:
    """«+внешние данные»: прирост качества там, где у бизнеса есть документы (иначе источнику нечего добавить)."""
    with_docs = [p for p in points if p.get("has_docs")]
    agg = aggregate(with_docs) if with_docs else {}
    flows = {}
    for group in ("client_in", "suppliers"):
        actual = np.array([p["flows"]["actual"][group] for p in with_docs])
        if not len(actual) or actual.sum() <= 0:
            continue
        predicted = {STRUCTURAL: np.array([p["flows"]["predicted"][group] for p in with_docs]),
                     STRUCTURAL_EXT: np.array([p["flows_external"][group] for p in with_docs])}
        flows[group] = {name: {"wape": float(np.abs(pred - actual).sum() / actual.sum()),
                               "bias": float(pred.sum() / actual.sum() - 1)} for name, pred in predicted.items()}
    return {"n_points": len(with_docs), "share_of_points": len(with_docs) / len(points) if points else 0.0,
            "approaches": {k: agg[k] for k in (STRUCTURAL, STRUCTURAL_EXT)} if agg else {}, "flows_wape": flows}


def by_industry(points: list[dict], industry: dict[str, str], horizon: int = 30) -> dict:
    rows = {}
    for p in points:
        rows.setdefault(industry.get(p["company"], "?"), []).append(p["rows"][STRUCTURAL]["rel_mae"][horizon])
    return {k: {"n": len(v), "rel_mae_median": float(np.median(v))} for k, v in sorted(rows.items())}


def rub0(v: float | None) -> str:
    return "—" if v is None else f"{v:,.0f} ₽".replace(",", " ")


def classification_table(m30: dict) -> list[str]:
    """Матрица ошибок и метрики классификации «будет ли разрыв в 30 дней» для жёлтого и красного уровня."""
    def f(v, pct=True):
        return "—" if v is None else (f"{v:.1%}" if pct else f"{v:.3f}")

    lines = ["| Уровень тревоги | TP | FP | FN | TN | Accuracy | Precision | Recall | Specificity | F1 | Balanced acc. |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, title in (("detection", f"жёлтый: шанс ≥{YELLOW_PROB:.0%}"),
                       ("detection_red", f"красный: шанс ≥{RED_PROB:.0%} или разрыв вероятен ≤{RED_WITHIN_DAYS} дн.")):
        d = m30[key]
        lines.append(f"| {title} | {d['tp']} | {d['fp']} | {d['fn']} | {d['tn']} | {f(d['accuracy'])} | {f(d['precision'])} | "
                     f"{f(d['recall'])} | {f(d['specificity'])} | {f(d['f1'], False)} | {f(d['balanced_accuracy'])} |")
    return lines


def draw_confusion(m30: dict, path: Path) -> None:
    """Две матрицы ошибок (жёлтый и красный уровень) с Accuracy, Precision, Recall, F1 в подписях."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, key, title in zip(axes, ("detection", "detection_red"),
                              (f"Жёлтый уровень (шанс ≥{YELLOW_PROB:.0%})",
                               f"Красный уровень (≥{RED_PROB:.0%} или ≤{RED_WITHIN_DAYS} дн.)"), strict=True):
        d = m30[key]
        matrix = np.array([[d["tn"], d["fp"]], [d["fn"], d["tp"]]])
        ax.imshow(matrix, cmap="Greens")
        for (i, j), v in np.ndenumerate(matrix):
            ax.text(j, i, str(v), ha="center", va="center", fontsize=16, color="white" if v > matrix.max() / 2 else "black")
        ax.set_xticks([0, 1], ["нет тревоги", "тревога"])
        ax.set_yticks([0, 1], ["разрыва не было", "разрыв был"])
        ax.set_title(f"{title}\nAccuracy {d['accuracy']:.0%} · Precision {d['precision'] or 0:.0%} · "
                     f"Recall {d['recall'] or 0:.0%} · F1 {d['f1'] or 0:.2f}", fontsize=9)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def markdown(report: dict) -> str:
    test_report = report["test"]
    test = report["test"]["approaches"]
    lines = [f"# Бэктест: сравнение подходов ({report['generated']})", "",
             f"Тест: отсечки {report['cutoffs']['test'][0]} … {report['cutoffs']['test'][-1]}, "
             f"{report['test']['n_points']} точек у {report['n_companies']} компаний; горизонт 30 дней. "
             "Цель — операционный остаток (без денег владельца и разовых операций).", "",
             "| Подход | Ошибка остатка, медиана rel-MAE | Покрытие P10–P90 | ROC-AUC | PR-AUC | Precision | Recall | "
             "За сколько дней знаем, медиана |",
             "|---|---|---|---|---|---|---|---|"]

    def fmt(v, pct=False):
        return "—" if v is None else (f"{v:.0%}" if pct else f"{v:.2f}")

    for name in (NAIVE, SEASONAL_NAIVE, STRUCTURAL_M0, STRUCTURAL, STRUCTURAL_M2, STRUCTURAL_CAL, STRUCTURAL_EXT):
        m = test[name]["horizons"]["30"]
        d = m["detection"]
        lead = m["lead_days_median"]
        lines.append(f"| {TITLES[name]} | {m['rel_mae_median']:.3f} | {fmt(m['interval_coverage'], True)} | "
                     f"{fmt(d['roc_auc'])} | {fmt(d['pr_auc'])} | {fmt(d['precision'], True)} | "
                     f"{fmt(d['recall'], True)} | {'—' if lead is None else f'{lead:.0f}'} |")
    s = test[STRUCTURAL_CAL]["horizons"]
    lines += ["", "## Итоговая модель (структурная + калибровка) по горизонтам", "",
              "| Горизонт | rel-MAE | Покрытие P10–P90 | ROC-AUC | Recall | Precision |", "|---|---|---|---|---|---|"]
    for h in HORIZONS:
        m = s[str(h)]
        d = m["detection"]
        lines.append(f"| {h} дн. | {m['rel_mae_median']:.3f} | {fmt(m['interval_coverage'], True)} | "
                     f"{fmt(d['roc_auc'])} | {fmt(d['recall'], True)} | {fmt(d['precision'], True)} |")
    m30 = s["30"]
    positives, n = m30["detection"]["positives"], m30["detection"]["n"]
    yellow, red = m30["detection"], m30["detection_red"]
    summary = (f"Разрывы в тесте: {positives} из {n} точек ({positives / n:.0%}); ошибка даты разрыва у пойманных — "
               f"медиана {m30['date_error_days_median']} дн.; ошибка суммы нехватки — "
               f"{fmt(m30['shortfall_error_rel_median'])} от масштаба остатка.")
    levels = (f"Уровни риска: жёлтый (шанс ≥{YELLOW_PROB:.0%}) — precision {fmt(yellow['precision'], True)}, "
              f"recall {fmt(yellow['recall'], True)}; красный (шанс ≥{RED_PROB:.0%} или разрыв вероятен в ближайшие "
              f"{RED_WITHIN_DAYS} дней — как в продукте) — "
              f"precision {fmt(red['precision'], True)}, recall {fmt(red['recall'], True)}.")
    ablation = test_report["external_ablation"]
    lines += ["", summary, "", levels, ""]
    s30 = test[STRUCTURAL_CAL]["horizons"]["30"]
    lines += ["## Матрица ошибок и метрики классификации (итоговая модель, разрыв в ближайшие 30 дней)", "",
              *classification_table(s30), "",
              f"ROC-AUC {fmt(s30['detection']['roc_auc'])}, Brier score {s30['detection'].get('brier', 0):.3f}. "
              f"Ошибка остатка в рублях: MAE {rub0(s30.get('mae_rub_mean'))} (медиана по компаниям "
              f"{rub0(s30.get('mae_rub_median'))}), RMSE {rub0(s30.get('rmse_rub'))}, WAPE {fmt(s30.get('wape'), True)}.",
              "", "![Матрицы ошибок](figures/confusion.png)", ""]
    lines += ["## Выручка: M0, M1 и M2 (бустинг)", "",
              "Ошибка прогноза 30-дневной выручки (WAPE и смещение прогноза к факту; меньше — лучше). Метод выбираем по отсечкам "
              "настройки, тест — проверка. Данные начинаются в октябре 2024: на отсечках настройки у бустинга "
              "меньше полутора лет истории, на тестовых — от полутора до двух.", "",
              "| Метод | WAPE, настройка | Смещение, настройка | WAPE, тест | Смещение, тест |", "|---|---|---|---|---|"]
    methods = {"tune": report["tune"].get("revenue_methods_30d", {}).get("card_companies"),
               "test": report["test"].get("revenue_methods_30d", {}).get("card_companies")}
    if methods["tune"] and methods["test"]:
        for name, title in ((STRUCTURAL_M0, "M0 — сезонно-наивный"), (STRUCTURAL, "M1 — декомпозиция (по умолчанию)"),
                            (STRUCTURAL_M2, "M2 — бустинг по картам")):
            lines.append(f"| {title} | {methods['tune'][name]['wape']:.3f} | {methods['tune'][name]['bias']:+.1%} | "
                         f"{methods['test'][name]['wape']:.3f} | {methods['test'][name]['bias']:+.1%} |")
        lines.append("")
    if ablation["approaches"]:
        lines += ["## Что дают внешние данные (счета и договоры из учётной системы)", "",
                  f"Там, где у бизнеса есть документы: {ablation['n_points']} точек "
                  f"({ablation['share_of_points']:.0%} теста). Демо-учётная система восстановлена из выписки: "
                  "модель видит номер, дату и сумму документа, но не дату оплаты.", "",
                  "| Подход | rel-MAE | Покрытие P10–P90 | ROC-AUC | Precision | Recall |", "|---|---|---|---|---|---|"]
        for name in (STRUCTURAL, STRUCTURAL_EXT):
            m = ablation["approaches"][name]["horizons"]["30"]
            d = m["detection"]
            lines.append(f"| {TITLES[name]} | {m['rel_mae_median']:.3f} | {fmt(m['interval_coverage'], True)} | "
                         f"{fmt(d['roc_auc'])} | {fmt(d['precision'], True)} | {fmt(d['recall'], True)} |")
        flows = ablation["flows_wape"]
        if flows:
            lines += ["", "Ошибка прогноза 30-дневных сумм (WAPE, меньше — лучше):", "",
                      "| Поток | Без внешних данных | С внешними данными |", "|---|---|---|"]
            names = {"client_in": "Оплаты клиентов", "suppliers": "Оплаты поставщикам"}
            for group, by_approach in flows.items():
                lines.append(f"| {names[group]} | {by_approach[STRUCTURAL]['wape']:.3f} | "
                             f"{by_approach[STRUCTURAL_EXT]['wape']:.3f} |")
        lines.append("")
    lines += ["![Сравнение подходов](figures/approaches.png)", "", "![Надёжность вероятностей](figures/reliability.png)"]
    return "\n".join(lines) + "\n"


def draw_figures(report: dict, points_test: list[dict], demo: list[dict], out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out.mkdir(parents=True, exist_ok=True)
    draw_confusion(report["test"]["approaches"][STRUCTURAL_CAL]["horizons"]["30"], out / "confusion.png")
    names = [NAIVE, SEASONAL_NAIVE, STRUCTURAL_M0, STRUCTURAL, STRUCTURAL_M2, STRUCTURAL_CAL, STRUCTURAL_EXT]
    labels = ["Наивный", "Сезонно-\nнаивный", "Структ.\n(M0)", "Структ.\n(M1)", "Структ.\n+ бустинг\n(M2)", "Структ.\n+калибр.",
              "+ внешние\nданные"]
    test = report["test"]["approaches"]
    fig, ax = plt.subplots(1, 3, figsize=(14, 3.8))
    for a, key, title in zip(ax, ("rel_mae_median", "roc_auc", "recall"),
                             ("Ошибка остатка на 30 дней (меньше — лучше)", "ROC-AUC обнаружения разрыва",
                              "Доля пойманных разрывов (recall)"), strict=True):
        vals = [(test[n]["horizons"]["30"][key] if key == "rel_mae_median"
                 else test[n]["horizons"]["30"]["detection"][key]) or 0 for n in names]
        a.bar(labels, vals, color=["#9aa5b1", "#7b8794", "#4f9fd6", "#1f6fb2", "#6a8caf", "#0f4c81", "#2a9d8f"])
        a.tick_params(axis="x", labelsize=7)
        a.set_title(title, fontsize=10)
        for i, v in enumerate(vals):
            a.text(i, v, f"{v:.2f}", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "approaches.png", dpi=150)
    plt.close(fig)

    fig, a = plt.subplots(figsize=(4.8, 4.4))
    a.plot([0, 1], [0, 1], "--", color="#9aa5b1", label="идеально")
    for key, label, color in (("reliability_30d", "до калибровки", "#d1495b"),
                              ("reliability_30d_calibrated", "после калибровки", "#1f6fb2")):
        rel = report["test"][key]
        a.plot([r["predicted"] for r in rel], [r["observed"] for r in rel], "o-", color=color, label=label)
    a.set_xlabel("Что мы говорим: шанс разрыва")
    a.set_ylabel("Как бывает на самом деле")
    a.set_title("Надёжность вероятностей (30 дней)", fontsize=10)
    a.legend()
    fig.tight_layout()
    fig.savefig(out / "reliability.png", dpi=150)
    plt.close(fig)

    for p in demo:
        d = p["paths"]
        days = pd.to_datetime(d["dates"])
        fig, a = plt.subplots(figsize=(8, 3.8))
        a.fill_between(days, d["p10"], d["p90"], color="#4f9fd6", alpha=0.25, label="коридор P10–P90")
        a.plot(days, d["p50"], color="#1f6fb2", label="ожидаемый остаток")
        a.plot(days, d["b_op"], color="#d1495b", label="факт (без денег владельца)")
        a.axhline(0, color="#52606d", lw=0.8)
        a.set_title(f"{p['company']}, прогноз на {p['cutoff']}", fontsize=10)
        a.legend(fontsize=8)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(out / f"example_{p['company']}_{p['cutoff']}.png", dpi=150)
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--companies", type=int, default=None, help="ограничить число компаний (для отладки)")
    ap.add_argument("--paths", type=int, default=300)
    ap.add_argument("--jobs", type=int, default=-1)
    ap.add_argument("--out", type=Path, default=ROOT / "reports")
    ap.add_argument("--write-calibration", action="store_true",
                    help="записать подобранные на tune параметры калибровки в app/engine/calibration.json")
    ap.add_argument("--dump-points", type=Path, default=None, help="сохранить сырые точки (pickle) для разбора")
    args = ap.parse_args()

    raw = ROOT / "data" / "raw"
    ops_all = normalize(load_operations(raw / "all_operations.csv"))
    companies = load_companies(raw / "companies.csv")
    ids = sorted(ops_all["company_id"].unique())
    if args.companies:
        ids = sorted(set(ids[: args.companies]) | {c for c, _ in DEMO_POINTS})
    cutoffs = TUNE + TEST
    t0 = time.time()
    contexts = build_contexts(ops_all, companies, cutoffs)
    print(f"контексты: {time.time() - t0:.0f} с")
    groups = {cid: g for cid, g in ops_all.groupby("company_id") if cid in ids}
    results = Parallel(n_jobs=args.jobs, verbose=5)(
        delayed(run_company)(g, cutoffs, contexts[cid], args.paths, 0, DEMO_POINTS) for cid, g in groups.items())
    points = [p for r in results for p in r]
    if args.dump_points:
        import pickle

        args.dump_points.write_bytes(pickle.dumps(points))
    print(f"точек: {len(points)}, время {time.time() - t0:.0f} с")

    tune_set, test_set = {d.isoformat() for d in TUNE}, {d.isoformat() for d in TEST}
    a, b = add_calibrated(points, tune_set)
    print(f"калибровка Платта (подбор на tune): a={a:.3f}, b={b:.3f}")
    if args.write_calibration:
        # проверка вне выборки (tune → test) выше; для продукта пересобираем на всех отсечках
        a_all, b_all = fit_platt([p["rows"][STRUCTURAL]["gap"][30] for p in points], [p["y"][30] for p in points])
        params = {"a": round(a_all, 4), "b": round(b_all, 4), "eps": 0.001,
                  "fitted_on": f"all cutoffs {TUNE[0]} .. {TEST[-1]}, structural, horizon 30 "
                               "(метрики в отчёте — подбор на tune, проверка на test)"}
        CALIBRATION_PATH.write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")
    tune = [p for p in points if p["cutoff"] in tune_set]
    test = [p for p in points if p["cutoff"] in test_set]
    industry = companies.set_index("company_id")["industry"].to_dict()
    report = {
        "generated": date.today().isoformat(), "n_companies": len({p["company"] for p in points}),
        "protocol": {"horizons": list(HORIZONS), "n_paths": args.paths, "yellow_threshold": YELLOW_PROB,
                     "target": "операционный остаток B_op", "models_see": "только данные ≤ отсечки"},
        "cutoffs": {"tune": [d.isoformat() for d in TUNE], "test": [d.isoformat() for d in TEST]},
        "tune": {"n_points": len(tune), "approaches": aggregate(tune) if tune else {},
                 "flows_wape_30d": flows_wape(tune) if tune else {},
                 "revenue_methods_30d": revenue_methods(tune) if tune else {}},
        "test": {"n_points": len(test), "approaches": aggregate(test), "by_industry": by_industry(test, industry),
                 "external_ablation": external_ablation(test),
                 "flows_wape_30d": flows_wape(test), "revenue_methods_30d": revenue_methods(test),
                 "reliability_30d": reliability(np.array([p["rows"][STRUCTURAL]["gap"][30] for p in test]),
                                                np.array([p["y"][30] for p in test])),
                 "reliability_30d_calibrated": reliability(
                     np.array([p["rows"][STRUCTURAL_CAL]["gap"][30] for p in test]),
                     np.array([p["y"][30] for p in test]))},
        "calibration": {"a": a, "b": b, "fitted_on": "tune cutoffs, structural, horizon 30"},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"backtest_{report['generated']}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "backtest.md").write_text(markdown(report), encoding="utf-8")
    draw_figures(report, test, [p for p in test if "paths" in p], args.out / "figures")
    print((args.out / "backtest.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
