"""Проверка на контрольном датасете (финальный чек-поинт): загрузка → прогноз → метрики → пошаговый журнал.

Запуск из backend/:
  uv run python scripts/evaluate.py --data ../control.zip              # zip, папка с all_operations.csv или выписка CSV
  uv run python scripts/evaluate.py --data ../data/raw --companies 20  # быстрый прогон на части компаний
  uv run python scripts/evaluate.py --data ../control.zip --publish    # и показать метрики на странице «Как мы считаем»

Результат — reports/control/<датасет>/:
  evaluation.md          метрики: Accuracy, Precision, Recall, F1, матрицы ошибок, MAE, MSE, RMSE, ROC-AUC, Brier…
  evaluation.json        те же цифры в формате отчёта бэктеста (его читает страница «Как мы считаем»)
  evaluation_log.txt     пошаговый журнал проверки: что загружено, как считали, сколько заняло
  trace_<компания>.txt   как построен прогноз одной компании, шаг за шагом
  forecast_daily.csv     прогноз остатка по дням (P10/P50/P90) на 30 дней от последней даты датасета
  forecast_summary.csv   по компаниям: уровень риска, шанс, дата и сумма возможного разрыва
  figures/*.png          матрицы ошибок, надёжность вероятностей, прогноз против факта, прогнозы от последней даты

Модель заморожена: параметры и калибровка шанса — из репозитория, на контрольном датасете ничего не подбирается.
Метрики считаются ретроспективно (как в бэктесте): на концах месяцев модель видит только прошлое, а прогноз сверяется
с тем, что было дальше. Цель — операционный остаток (без денег владельца и разовых операций), горизонт 30 дней.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest as bt
from joblib import Parallel, delayed

from app.engine.backtest import (
    HORIZONS,
    MIN_HISTORY_DAYS,
    NAIVE,
    RED_PROB,
    SEASONAL_NAIVE,
    STRUCTURAL,
    STRUCTURAL_CAL,
    STRUCTURAL_EXT,
    STRUCTURAL_M0,
    YELLOW_PROB,
    reliability,
    run_company,
)
from app.engine.calibration import apply_platt, load_params
from app.engine.categorize import categorize
from app.engine.categorize.taxonomy import UNKNOWN
from app.engine.normalize import normalize
from app.engine.pipeline import build_model, evaluate
from app.engine.trace import trace_forecast

ROOT = Path(__file__).resolve().parents[2]
HORIZON = 30
REQUIRED = ["operation_date", "amount", "direction", "balance_eod"]
TITLES = {NAIVE: "Наивный (остаток не меняется)", SEASONAL_NAIVE: "Сезонно-наивный (потоки прошлых недель)",
          STRUCTURAL_M0: "Структурный, выручка M0", STRUCTURAL: "Структурный, выручка M1",
          STRUCTURAL_CAL: "Структурный + калибровка шанса (итоговая модель)",
          STRUCTURAL_EXT: "+ внешние данные (демо-учётная система)"}
LEVELS = {"green": "зелёный", "yellow": "жёлтый", "red": "красный"}


class StepLog:
    """Журнал проверки: строки с отметкой времени — в консоль и в файл."""

    def __init__(self, path: Path) -> None:
        self.file = path.open("w", encoding="utf-8")
        self.t0 = time.time()

    def __call__(self, text: str) -> None:
        line = f"[{time.time() - self.t0:7.1f} с] {text}"
        print(line, flush=True)
        self.file.write(line + "\n")
        self.file.flush()


# ---------- 1. загрузка датасета в любом из видов ----------
def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype={"company_id": str, "operation_id": str,
                       "counterparty_inn": str, "counterparty_name": str, "payment_purpose": str, "direction": str},
                       parse_dates=["operation_date"])


def load_dataset(path: Path, workdir: Path) -> tuple[pd.DataFrame, pd.DataFrame | None, str]:
    """(операции в формате датасета, справочник компаний или None, описание источника)."""
    if path.suffix.lower() == ".zip":
        from unpack_dataset import unpack

        target = workdir / "dataset"
        n = unpack(path, target)
        source = f"архив {path.name} ({n} файлов)"
        path = target
    else:
        source = str(path)
    if path.is_file():
        ops = _read_csv(path)
        if "company_id" not in ops:
            ops["company_id"] = path.stem
        return ops, None, f"выписка {path.name}"
    all_ops = sorted(path.rglob("all_operations.csv"))
    companies = sorted(path.rglob("companies.csv"))
    if all_ops:
        ops = _read_csv(all_ops[0])
    else:
        statements = sorted(p for p in path.rglob("*.csv") if p.name != "companies.csv")
        if not statements:
            raise SystemExit(f"В {path} нет ни all_operations.csv, ни выписок *.csv")
        frames = []
        for p in statements:
            frame = _read_csv(p)
            if "company_id" not in frame:
                frame["company_id"] = p.stem
            frames.append(frame)
        ops = pd.concat(frames, ignore_index=True)
    table = pd.read_csv(companies[0], sep=";", encoding="utf-8-sig", dtype=str) if companies else None
    return ops, table, source


def complete_companies(ops: pd.DataFrame, table: pd.DataFrame | None) -> pd.DataFrame:
    """Справочник на каждую компанию выписки; без справочника отрасль неизвестна (сезонность — по всем компаниям)."""
    ids = sorted(ops["company_id"].astype(str).unique())
    known = table.set_index("company_id") if table is not None and "company_id" in table else pd.DataFrame()
    rows = []
    for cid in ids:
        row = known.loc[cid] if cid in known.index else {}
        rows.append({"company_id": cid, "company_name": row.get("company_name", cid) if len(row) else cid,
                     "industry": row.get("industry", "Не указана") if len(row) else "Не указана"})
    return pd.DataFrame(rows)


DATASET_COLUMNS = ["company_id", "operation_id", "operation_date", "amount", "direction", "counterparty_inn",
                   "counterparty_name", "payment_purpose", "balance_eod"]


def write_service_dataset(raw: pd.DataFrame, table: pd.DataFrame | None, target: Path) -> Path:
    """Датасет в раскладке сервиса (режим жюри открывает любую компанию): <target>/raw/{companies.csv, all_operations.csv,
    statements/<компания>.csv}. Справочник дополняется датами и числом операций, если их нет."""
    raw_dir = target / "raw"
    (raw_dir / "statements").mkdir(parents=True, exist_ok=True)
    ops = raw.copy()
    ops["company_id"] = ops["company_id"].astype(str)
    for column in DATASET_COLUMNS:
        if column not in ops:
            ops[column] = None
    if ops["operation_id"].isna().any():
        ops["operation_id"] = ops["company_id"] + "-" + ops.groupby("company_id").cumcount().add(1).astype(str).str.zfill(6)
    ops["operation_date"] = pd.to_datetime(ops["operation_date"]).dt.strftime("%Y-%m-%d")
    ops = ops[DATASET_COLUMNS].sort_values(["company_id", "operation_date", "operation_id"], kind="stable")
    ops.to_csv(raw_dir / "all_operations.csv", sep=";", index=False, encoding="utf-8-sig")
    for cid, g in ops.groupby("company_id"):
        g.to_csv(raw_dir / "statements" / f"{cid}.csv", sep=";", index=False, encoding="utf-8-sig")
    known = table.set_index("company_id") if table is not None and "company_id" in table else pd.DataFrame()
    rows = []
    for cid, g in ops.groupby("company_id"):
        info = known.loc[cid] if cid in known.index else pd.Series(dtype=object)
        rows.append({"company_id": cid, "company_name": info.get("company_name", f"Компания {cid}"),
                     "company_inn": info.get("company_inn", ""), "legal_form": info.get("legal_form", ""),
                     "industry": info.get("industry", "Не указана"), "first_operation_date": g["operation_date"].min(),
                     "last_operation_date": g["operation_date"].max(), "operations_count": len(g)})
    pd.DataFrame(rows).to_csv(raw_dir / "companies.csv", sep=";", index=False, encoding="utf-8-sig")
    return raw_dir


# ---------- 2. отсечки ----------
def month_end_cutoffs(ops: pd.DataFrame, max_cutoffs: int) -> list[date]:
    """Концы месяцев, где есть ≥90 дней истории и 30 дней факта после; если таких нет — одна отсечка за 30 дней до конца."""
    first, last = ops["date"].min(), ops["date"].max()
    start, end = first + pd.Timedelta(days=MIN_HISTORY_DAYS), last - pd.Timedelta(days=HORIZON)
    cutoffs = [d.date() for d in pd.date_range(start, end, freq="ME")]
    if not cutoffs and end >= start:
        cutoffs = [end.date()]
    return cutoffs[-max_cutoffs:]


def calibrate_points(points: list[dict]) -> dict:
    """Итоговая модель = структурная + калибровка шанса из репозитория (без подбора на контрольных данных)."""
    params = load_params()
    for p in points:
        row = copy.deepcopy(p["rows"][STRUCTURAL])
        row["gap"] = {h: float(apply_platt(v, params["a"], params["b"], params["eps"])) for h, v in row["gap"].items()}
        p["rows"][STRUCTURAL_CAL] = row
    return params


# ---------- 3. прогноз от последней даты ----------
def forecast_company(ops: pd.DataFrame, context, n_paths: int, with_trace: bool) -> dict:
    cid = str(ops["company_id"].iloc[0])
    as_of = ops["date"].max().date()
    try:
        model = build_model(ops, as_of, context=context)
        result = evaluate(model, n_paths)
    except Exception as exc:  # noqa: BLE001 — нетипичная компания не должна ронять всю проверку
        return {"company_id": cid, "error": f"{type(exc).__name__}: {exc}"}
    r = result.risk
    out = {
        "company_id": cid, "as_of": as_of.isoformat(), "level": r.level, "gap_probability": r.gap_probability,
        "possible_gap_date": str(r.possible_gap_date or r.likely_gap_date or ""),
        "shortfall_p50": r.shortfall_p50, "shortfall_p90": r.shortfall_p90, "start_balance": result.start_balance,
        "min_p50": float(result.p50.min()), "headline": r.headline,
        "recommendation": result.recommendations[0].title if result.recommendations else "",
        "history": result.data_quality.history,
        "daily": [(d.isoformat(), float(a), float(b), float(c), float(pb)) for d, a, b, c, pb in
                  zip(result.dates, result.p10, result.p50, result.p90, result.prob_below, strict=True)],
        "balance_tail": [(d.date().isoformat(), float(v)) for d, v in
                         ops.groupby("date")["balance_eod"].last().tail(60).items()],
    }
    if with_trace:
        out["trace"] = trace_forecast(model, result)
    return out


# ---------- 4. отчёт ----------
def fmt(v, pct: bool = False, rub: bool = False) -> str:
    if v is None:
        return "—"
    if pct:
        return f"{v:.1%}"
    if rub:
        return f"{v:,.0f} ₽".replace(",", " ")
    return f"{v:.3f}"


def markdown(report: dict, summary: pd.DataFrame, traced: list[str]) -> str:
    ctl = report["control"]
    test = report["test"]["approaches"]
    final = test[STRUCTURAL_CAL]["horizons"]
    lines = [f"# Проверка на контрольном датасете ({report['generated']})", "",
             f"Источник: {ctl['source']}. {ctl['n_operations']} операций, {report['n_companies']} компаний, "
             f"период {ctl['period'][0]} … {ctl['period'][1]}. Ретроспективная проверка: отсечки "
             f"{report['cutoffs']['test'][0]} … {report['cutoffs']['test'][-1]} ({len(report['cutoffs']['test'])}), "
             f"{report['test']['n_points']} точек «компания × отсечка», горизонт {HORIZON} дней, "
             f"разрывов в факте: {final['30']['detection']['positives']}.", "",
             "Модель заморожена: параметры и калибровка шанса — из репозитория, на этих данных ничего не подбиралось. "
             "Цель — операционный остаток (без денег владельца и разовых операций).", "",
             "## Обнаружение кассового разрыва в ближайшие 30 дней (итоговая модель)", "",
             *bt.classification_table(final["30"]), "",
             f"ROC-AUC {fmt(final['30']['detection']['roc_auc'])}, PR-AUC {fmt(final['30']['detection']['pr_auc'])}, "
             f"Brier score {fmt(final['30']['detection']['brier'])} (меньше — лучше). "
             f"Заблаговременность у пойманных разрывов — медиана {final['30']['lead_days_median']} дн., ошибка даты — "
             f"{final['30']['date_error_days_median']} дн., ошибка суммы нехватки — "
             f"{fmt(final['30']['shortfall_error_rel_median'])} от масштаба остатка.", "",
             "![Матрицы ошибок](figures/confusion.png)", "",
             "## Точность прогноза остатка (30 дней) и сравнение подходов", "",
             "| Подход | MAE, ₽ (среднее) | MAE, ₽ (медиана) | RMSE, ₽ | MSE, ₽² | WAPE | rel-MAE (медиана) | Покрытие P10–P90 | ROC-AUC |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name in (NAIVE, SEASONAL_NAIVE, STRUCTURAL_M0, STRUCTURAL, STRUCTURAL_CAL, STRUCTURAL_EXT):
        if name not in test:
            continue
        m = test[name]["horizons"]["30"]
        lines.append(f"| {TITLES[name]} | {fmt(m.get('mae_rub_mean'), rub=True)} | {fmt(m.get('mae_rub_median'), rub=True)} | "
                     f"{fmt(m.get('rmse_rub'), rub=True)} | {m.get('mse_rub2', 0):.3e} | {fmt(m.get('wape'), True)} | "
                     f"{fmt(m['rel_mae_median'])} | {fmt(m['interval_coverage'], True)} | {fmt(m['detection']['roc_auc'])} |")
    lines += ["", "WAPE = Σ|прогноз − факт| / Σ|факт|; rel-MAE — средняя ошибка, делённая на обычный размер остатка компании "
              "(сравнимо между малыми и крупными бизнесами); покрытие — доля дней, когда факт попал в коридор P10–P90 (цель ≈80%).",
              "", "## По горизонтам (итоговая модель)", "",
              "| Горизонт | MAE, ₽ | RMSE, ₽ | rel-MAE | Покрытие | ROC-AUC | Accuracy | Precision | Recall | F1 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for h in HORIZONS:
        m = final[str(h)]
        d = m["detection"]
        lines.append(f"| {h} дн. | {fmt(m.get('mae_rub_mean'), rub=True)} | {fmt(m.get('rmse_rub'), rub=True)} | "
                     f"{fmt(m['rel_mae_median'])} | {fmt(m['interval_coverage'], True)} | {fmt(d['roc_auc'])} | "
                     f"{fmt(d['accuracy'], True)} | {fmt(d['precision'], True)} | {fmt(d['recall'], True)} | {fmt(d['f1'])} |")
    rel = report["test"]["reliability_30d_calibrated"]
    lines += ["", "## Надёжность вероятности", "", "| Мы говорили (шанс) | Бывало на самом деле | Случаев |", "|---|---|---|"]
    lines += [f"| {r['predicted']:.0%} | {r['observed']:.0%} | {r['n']} |" for r in rel]
    lines += ["", "![Надёжность](figures/reliability.png)", "",
              f"## Прогноз на {HORIZON} дней от последней даты датасета", ""]
    ok = summary[summary["error"].isna()] if "error" in summary else summary
    counts = ok["level"].value_counts()
    lines.append("Уровни риска: " + ", ".join(f"{LEVELS[k]} — {int(counts.get(k, 0))}" for k in ("red", "yellow", "green"))
                 + f". Компаний без прогноза: {len(summary) - len(ok)}.")
    lines += ["", "| Компания | Дата прогноза | Уровень | Шанс разрыва | Возможная дата | Нехватка (P50) | Что сказали предпринимателю |",
              "|---|---|---|---|---|---|---|"]
    for r in ok.sort_values("gap_probability", ascending=False).head(15).itertuples():
        lines.append(f"| {r.company_id} | {r.as_of} | {LEVELS[r.level]} | {r.gap_probability:.0%} | {r.possible_gap_date or '—'} | "
                     f"{fmt(r.shortfall_p50, rub=True)} | {r.headline} |")
    lines += ["", "Полная таблица — `forecast_summary.csv`, остатки по дням — `forecast_daily.csv`.", "",
              "## Как считается прогноз (пошаговый журнал)", "",
              "Для компаний " + ", ".join(traced) + " — файлы `trace_<компания>.txt`; журнал всей проверки — "
              "`evaluation_log.txt`.", ""]
    lines += [f"![Прогноз {c}](figures/forecast_{c}.png)" for c in traced]
    lines += ["", "Прогноз против факта на прошлых отсечках:", ""]
    lines += [f"![{p}](figures/{p})" for p in report["control"]["example_figures"]]
    return "\n".join(lines) + "\n"


# ---------- 5. картинки ----------
def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _rub_axis(ax) -> None:
    from matplotlib.ticker import FuncFormatter

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1e6:.1f} млн ₽" if abs(v) >= 1e6 else f"{v / 1e3:.0f} тыс. ₽"))


def draw_quality(report: dict, points: list[dict], out: Path) -> list[str]:
    """Матрицы ошибок, надёжность вероятностей и «прогноз против факта» на прошлых отсечках; возвращает имена картинок примеров."""
    plt = _plt()
    out.mkdir(parents=True, exist_ok=True)
    bt.draw_confusion(report["test"]["approaches"][STRUCTURAL_CAL]["horizons"]["30"], out / "confusion.png")

    fig, ax = plt.subplots(figsize=(4.8, 4.4))
    ax.plot([0, 1], [0, 1], "--", color="#9aa5b1", label="идеально")
    rel = report["test"]["reliability_30d_calibrated"]
    ax.plot([r["predicted"] for r in rel], [r["observed"] for r in rel], "o-", color="#1f6fb2", label="итоговая модель")
    ax.set_xlabel("Что мы говорим: шанс разрыва")
    ax.set_ylabel("Как бывает на самом деле")
    ax.set_title("Надёжность вероятностей (30 дней)", fontsize=10)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "reliability.png", dpi=150)
    plt.close(fig)

    # прогноз против факта: пойманный разрыв, пропущенный разрыв и спокойная точка
    examples, names = [], []
    with_paths = [p for p in points if "paths" in p]
    caught = [p for p in with_paths if p["y"][30] and p["rows"][STRUCTURAL_CAL]["gap"][30] >= YELLOW_PROB]
    missed = [p for p in with_paths if p["y"][30] and p["rows"][STRUCTURAL_CAL]["gap"][30] < YELLOW_PROB]
    calm = [p for p in with_paths if not p["y"][30]]
    for group in (caught, missed, calm):
        if group:
            examples.append(max(group, key=lambda p: p["scale"]))
    for p in examples:
        d = p["paths"]
        days = pd.to_datetime(d["dates"])
        fig, ax = plt.subplots(figsize=(8, 3.6))
        ax.fill_between(days, d["p10"], d["p90"], color="#4f9fd6", alpha=0.25, label="коридор P10–P90")
        ax.plot(days, d["p50"], color="#1f6fb2", label="ожидаемый остаток (P50)")
        ax.plot(days, d["b_op"], color="#d1495b", label="факт (без денег владельца)")
        ax.axhline(0, color="#52606d", lw=0.8)
        _rub_axis(ax)
        ax.set_title(f"{p['company']}, прогноз от {p['cutoff']}: шанс разрыва "
                     f"{p['rows'][STRUCTURAL_CAL]['gap'][30]:.0%}, разрыв {'был' if p['y'][30] else 'не был'}", fontsize=10)
        ax.legend(fontsize=8)
        fig.autofmt_xdate()
        fig.tight_layout()
        name = f"backtest_{p['company']}_{p['cutoff']}.png"
        fig.savefig(out / name, dpi=150)
        plt.close(fig)
        names.append(name)
    return names


def draw_forecasts(forecasts: list[dict], out: Path) -> None:
    """Графики прогноза от последней даты (история остатка + коридор P10–P90) для компаний с пошаговым журналом."""
    plt = _plt()
    out.mkdir(parents=True, exist_ok=True)
    for f in forecasts:
        if "trace" not in f:
            continue
        hist = pd.DataFrame(f["balance_tail"], columns=["date", "balance"])
        daily = pd.DataFrame(f["daily"], columns=["date", "p10", "p50", "p90", "prob_below"])
        fig, ax = plt.subplots(figsize=(8, 3.8))
        ax.plot(pd.to_datetime(hist["date"]), hist["balance"], color="#52606d", label="остаток (история)")
        ax.fill_between(pd.to_datetime(daily["date"]), daily["p10"], daily["p90"], color="#4f9fd6", alpha=0.25,
                        label="коридор P10–P90")
        ax.plot(pd.to_datetime(daily["date"]), daily["p50"], color="#1f6fb2", label="ожидаемый остаток")
        ax.axhline(0, color="#d1495b", lw=0.8)
        _rub_axis(ax)
        ax.set_title(f"{f['company_id']}: {LEVELS[f['level']]} уровень, шанс разрыва {f['gap_probability']:.0%}", fontsize=10)
        ax.legend(fontsize=8)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(out / f"forecast_{f['company_id']}.png", dpi=150)
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "raw", help="zip, папка датасета или выписка CSV")
    ap.add_argument("--out", type=Path, default=None, help="папка отчёта (по умолчанию reports/control/<датасет>)")
    ap.add_argument("--companies", type=int, default=None, help="ограничить число компаний (быстрый прогон)")
    ap.add_argument("--paths", type=int, default=300, help="сценариев на точку ретроспективной проверки")
    ap.add_argument("--forecast-paths", type=int, default=1000, help="сценариев в прогнозе от последней даты")
    ap.add_argument("--max-cutoffs", type=int, default=12)
    ap.add_argument("--trace", nargs="*", default=None, help="компании для пошагового журнала (по умолчанию 3 разных)")
    ap.add_argument("--external", action="store_true", help="добавить абляцию «+внешние данные» (в 2 раза дольше)")
    ap.add_argument("--jobs", type=int, default=-1)
    ap.add_argument("--publish", action="store_true", help="положить JSON в reports/ для страницы «Как мы считаем»")
    ap.add_argument("--prepare-service", type=Path, default=None,
                    help="разложить датасет для сервиса в <папка>/raw (режим жюри: открыть любую компанию в интерфейсе)")
    args = ap.parse_args()

    name = args.data.stem if args.data.suffix else args.data.name
    out = args.out or ROOT / "reports" / "control" / name
    out.mkdir(parents=True, exist_ok=True)
    log = StepLog(out / "evaluation_log.txt")
    workdir = Path(tempfile.mkdtemp(prefix="kassa-eval-"))
    try:
        log(f"Шаг 1. Загрузка датасета: {args.data}")
        raw, table, source = load_dataset(args.data.resolve(), workdir)
        missing = [c for c in REQUIRED if c not in raw.columns]
        if missing:
            raise SystemExit(f"В выписке нет обязательных столбцов: {missing}")
        log(f"  прочитано {len(raw)} строк ({source}); столбцы: {', '.join(raw.columns)}")
        if args.prepare_service:
            target = write_service_dataset(raw, table, args.prepare_service.resolve())
            log(f"  датасет для интерфейса разложен в {target} (DATA_DIR={args.prepare_service})")
        ops_all = normalize(raw)
        companies = complete_companies(ops_all, table)
        ids = sorted(ops_all["company_id"].unique())
        if args.companies:
            ids = ids[: args.companies]
            ops_all = ops_all[ops_all["company_id"].isin(ids)]
            companies = companies[companies["company_id"].isin(ids)]
        log(f"  после нормализации: {len(ops_all)} операций, {len(ids)} компаний, период "
            f"{ops_all['date'].min():%d.%m.%Y} … {ops_all['date'].max():%d.%m.%Y}; справочник компаний: "
            f"{'есть' if table is not None else 'нет — отрасль неизвестна'}")

        log("Шаг 2. Категоризация (правила по назначению, по имени, память контрагента)")
        cat = categorize(ops_all)
        known = ~cat["category"].isin(UNKNOWN)
        log(f"  распознано {known.mean():.1%} операций; по источникам: "
            + ", ".join(f"{k} {v}" for k, v in cat["category_source"].value_counts().items()))

        cutoffs = month_end_cutoffs(ops_all, args.max_cutoffs)
        log(f"Шаг 3. Отсечки ретроспективной проверки: {len(cutoffs)} — "
            + (", ".join(c.isoformat() for c in cutoffs) if cutoffs else "нет (истории меньше 90+30 дней)"))
        points: list[dict] = []
        if cutoffs:
            log("Шаг 4. Данные «со стороны» на каждую отсечку (сезонность отрасли, индекс ЕНП, задержки клиентов) — только ≤ отсечки")
            contexts = bt.build_contexts(ops_all, companies, cutoffs, with_m2=False)
            methods = ((STRUCTURAL, "m1"), (STRUCTURAL_M0, "m0"))
            log(f"Шаг 5. Прогнозы на отсечках: подходы {', '.join(TITLES[m] for m, _ in methods)}, наивный и сезонно-наивный"
                f"{', + внешние данные' if args.external else ''}; {args.paths} сценариев на точку")
            groups = {cid: g for cid, g in ops_all.groupby("company_id")}
            keep = {(cid, c) for cid in groups for c in cutoffs}
            results = Parallel(n_jobs=args.jobs)(
                delayed(run_company)(g, cutoffs, contexts[cid], args.paths, 0, keep, methods, args.external)
                for cid, g in groups.items())
            points = [p for r in results for p in r]
            log(f"  точек «компания × отсечка»: {len(points)} (остальные пропущены: мало истории, дыра в данных "
                "или нет 30 дней факта)")
        if not points:
            log("  метрики посчитать не на чем: нужно ≥90 дней истории и 30 дней факта после отсечки")
        else:
            params = calibrate_points(points)
            log(f"Шаг 6. Калибровка шанса — замороженные параметры из репозитория: a={params['a']}, b={params['b']}")
            names = [n for n in (NAIVE, SEASONAL_NAIVE, STRUCTURAL_M0, STRUCTURAL, STRUCTURAL_CAL, STRUCTURAL_EXT)
                     if n in points[0]["rows"]]
            bt.TITLES.update(TITLES)
            approaches = bt.aggregate(points, names)
            m30 = approaches[STRUCTURAL_CAL]["horizons"]["30"]
            d, red = m30["detection"], m30["detection_red"]
            log(f"Шаг 7. Метрики (итоговая модель, 30 дней): разрывов в факте {d['positives']} из {d['n']}; "
                f"жёлтый: Accuracy {d['accuracy']:.1%}, Precision {d['precision'] or 0:.1%}, Recall {d['recall'] or 0:.1%}, "
                f"F1 {d['f1'] or 0:.3f} (TP {d['tp']}, FP {d['fp']}, FN {d['fn']}, TN {d['tn']}); красный: Accuracy "
                f"{red['accuracy']:.1%}, Precision {red['precision'] or 0:.1%}, Recall {red['recall'] or 0:.1%}, F1 "
                f"{red['f1'] or 0:.3f}; ROC-AUC {d['roc_auc'] or 0:.3f}, Brier {d['brier']:.3f}; MAE "
                f"{m30.get('mae_rub_mean', 0):,.0f} ₽, RMSE {m30.get('rmse_rub', 0):,.0f} ₽, rel-MAE {m30['rel_mae_median']:.3f}, "
                f"покрытие P10–P90 {m30['interval_coverage']:.1%}")

        log(f"Шаг 8. Прогноз на {HORIZON} дней от последней даты по каждой компании ({args.forecast_paths} сценариев)")
        last = ops_all["date"].max().date()
        live_context = bt.build_contexts(ops_all, companies, [last], with_m2=False)
        groups = {cid: g for cid, g in ops_all.groupby("company_id")}
        forecasts = Parallel(n_jobs=args.jobs)(delayed(forecast_company)(g, live_context[cid][last], args.forecast_paths, False)
                                               for cid, g in groups.items())
        good = [f for f in forecasts if "error" not in f]
        if args.trace:
            traced = [c for c in args.trace if c in groups]
        else:                                                      # разные случаи: красный, жёлтый (или ближе к нему), зелёный
            picks = [max(good, key=lambda f: f["gap_probability"]),
                     min(good, key=lambda f: abs(f["gap_probability"] - 0.2)),
                     min(good, key=lambda f: f["gap_probability"])] if good else []
            traced = list(dict.fromkeys(f["company_id"] for f in picks))
        log(f"  готово: {len(good)} прогнозов, без прогноза {len(forecasts) - len(good)}; пошаговый журнал для "
            f"{', '.join(traced)}")
        detailed = [forecast_company(groups[c], live_context[c][last], args.forecast_paths, True) for c in traced]
        for f in detailed:
            if "trace" in f:
                (out / f"trace_{f['company_id']}.txt").write_text(
                    f"Компания {f['company_id']}, прогноз от {f['as_of']} на {HORIZON} дней\n\n" + "\n\n".join(f["trace"]) + "\n",
                    encoding="utf-8")
                log(f"  {f['company_id']}: " + f["trace"][9 if len(f["trace"]) > 9 else -1])
        summary = pd.DataFrame([{k: v for k, v in f.items() if k not in ("daily", "balance_tail", "trace")} for f in forecasts])
        summary.to_csv(out / "forecast_summary.csv", sep=";", index=False, encoding="utf-8-sig")
        daily = pd.DataFrame([(f["company_id"], *row) for f in good for row in f["daily"]],
                             columns=["company_id", "date", "p10", "p50", "p90", "prob_below"])
        daily.round(2).to_csv(out / "forecast_daily.csv", sep=";", index=False, encoding="utf-8-sig")

        report = {
            "generated": date.today().isoformat(), "n_companies": len(groups),
            "protocol": {"horizons": list(HORIZONS), "n_paths": args.paths, "yellow_threshold": YELLOW_PROB,
                         "red_threshold": RED_PROB, "target": "операционный остаток B_op",
                         "models_see": "только данные ≤ отсечки", "frozen_model": True},
            "cutoffs": {"test": [c.isoformat() for c in cutoffs]},
            "test": {"n_points": len(points)},
            "control": {"source": source, "dataset": name, "n_operations": len(ops_all),
                        "period": [ops_all["date"].min().date().isoformat(), last.isoformat()],
                        "forecast_levels": summary["level"].value_counts().to_dict() if "level" in summary else {},
                        "traced": traced, "example_figures": []},
        }
        if points:
            y = np.array([p["y"][30] for p in points])
            report["test"].update({
                "approaches": approaches, "by_industry": bt.by_industry(points, companies.set_index("company_id")["industry"].to_dict()),
                "flows_wape_30d": bt.flows_wape(points),
                "reliability_30d": reliability(np.array([p["rows"][STRUCTURAL]["gap"][30] for p in points]), y),
                "reliability_30d_calibrated": reliability(np.array([p["rows"][STRUCTURAL_CAL]["gap"][30] for p in points]), y),
            })
            if args.external:
                report["test"]["external_ablation"] = bt.external_ablation(points)
            report["control"]["example_figures"] = draw_quality(report, points, out / "figures")
            (out / "evaluation.md").write_text(markdown(report, summary, traced), encoding="utf-8")
        draw_forecasts(detailed, out / "figures")
        (out / "evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if args.publish and points:
            target = ROOT / "reports" / f"backtest_{datetime.now():%Y-%m-%d_%H%M}_control.json"
            shutil.copyfile(out / "evaluation.json", target)
            log(f"  опубликовано для страницы «Как мы считаем»: {target.name}")
        log(f"Готово. Отчёт: {out / 'evaluation.md'}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
