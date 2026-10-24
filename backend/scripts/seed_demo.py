"""Демо-данные: персоны (§15.2), их выписки до 31.07.2026 («машина времени»), контекст «со стороны».

Запуск из backend/:   uv run python scripts/seed_demo.py [--reset] [--raw ../data/raw] [--as-of 2026-07-31]
В Docker:             make seed   (docker compose exec api python scripts/seed_demo.py)
Выписка каждой персоны загружается целиком, но видна до --as-of; остальные дни подгружает «машина времени».
Без датасета создаются только персоны (прогноз можно смотреть в режиме FORECAST_MODE=fixture).
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Business, Transaction
from app.db.session import init_db, make_engine, make_session_factory
from app.engine.categorize import categorize
from app.engine.context import build_context
from app.engine.enrich import enrich
from app.engine.io import load_companies, load_operations
from app.engine.normalize import normalize
from app.logging_setup import setup_logging
from app.services.forecast_service import ForecastService
from app.services.personas import DEMO_AS_OF, DEMO_PASSWORD, PERSONAS
from app.services.seed_service import ensure_personas, seed_business


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", type=Path, default=None, help="каталог с датасетом (по умолчанию <data>/raw)")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.fromisoformat(DEMO_AS_OF),
                        help="до какой даты видна выписка (по умолчанию 2026-07-31)")
    parser.add_argument("--reset", action="store_true", help="загрузить данные персон заново, даже если они уже есть (старые стираются)")
    args = parser.parse_args()

    setup_logging("WARNING")
    settings = get_settings()
    raw = args.raw or settings.data_dir / "raw"
    engine = make_engine(settings.database_url)
    init_db(engine)
    service = ForecastService(settings)
    factory = make_session_factory(engine)

    have_data = (raw / "companies.csv").exists() and (raw / "all_operations.csv").exists()
    companies = load_companies(raw / "companies.csv") if (raw / "companies.csv").exists() else None
    with factory() as db:
        if not have_data:
            print(f"Датасет не найден в {raw}: создаю персон без выписок (см. data/README.md).")
            ensure_personas(db, companies)
            _print_accounts([])
            return

        # Что нужно загрузить: без --reset уже загруженных персон не трогаем (повторный `make seed` быстрый и безопасный)
        todo = []
        for persona in PERSONAS:
            statement = raw / "statements" / f"{persona.company_id}.csv"
            if not statement.exists():
                print(f"  {persona.company_id}: нет файла {statement.name} — пропускаю")
            elif not args.reset and _already_seeded(db, persona.company_id):
                print(f"  {persona.display_name:8} {persona.company_id}: уже загружено (чтобы начать заново: make demo-reset)")
            else:
                todo.append(persona)

        reports = []
        if todo:
            started = time.perf_counter()
            print("Читаю датасет и считаю контекст «со стороны» (отраслевая сезонность, ЕНП, задержки клиентов)…")
            contexts = _build_contexts(raw, companies, args.as_of, [p.company_id for p in todo])
            for persona in todo:
                statement = raw / "statements" / f"{persona.company_id}.csv"
                report = seed_business(db, service, persona, statement.read_bytes(), args.as_of, companies,
                                       contexts.get(persona.company_id))
                reports.append(report)
                print(f"  {persona.display_name:8} {persona.company_id}: {report.rows} операций, "
                      f"риск {report.risk_level} ({report.gap_probability:.0%}), предупреждений {report.alerts}")
            print(f"Готово за {time.perf_counter() - started:.0f} с.")
        _ask_api_to_warm_up()
        _print_accounts(reports)


def _already_seeded(db, company_id: str) -> bool:
    business = db.get(Business, company_id)
    return business is not None and business.data_as_of is not None and (
        db.scalar(select(func.count()).select_from(Transaction).where(Transaction.business_id == company_id)) or 0) > 0


def _build_contexts(raw: Path, companies, as_of: date, company_ids: list[str]) -> dict:
    """Контекст «со стороны» для каждой персоны. Операции всех компаний (222 тыс. строк) обрабатываются один раз и
    освобождаются сразу после расчёта: в контейнере с 4 ГБ памяти держать несколько копий таблицы нельзя."""
    ops = enrich(categorize(normalize(load_operations(raw / "all_operations.csv"))))   # ENGINE_API §3: после categorize и enrich
    present = set(ops["company_id"].unique())
    contexts = {cid: build_context(ops, companies, cid, as_of) for cid in company_ids if cid in present}
    del ops
    gc.collect()
    return contexts


def _ask_api_to_warm_up() -> None:
    """Работающий API живёт в другом процессе и про новые данные не знает: просим его прогреть кэш (не обязательно)."""
    url = os.environ.get("API_URL", "http://localhost:8000") + "/api/v1/demo/warm-up"
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=3)   # адрес — свой API
        print("API прогревает прогнозы демо-бизнесов в фоне.")
    except OSError:
        print("API сейчас недоступен — первый вход будет чуть медленнее (холодный старт).")


def _print_accounts(reports: list) -> None:
    print("\nДемо-предприниматели (вход одним нажатием на экране входа или по паролю):")
    for persona in PERSONAS:
        print(f"  {persona.display_name:8} {persona.company_id}  {persona.email}  пароль: {DEMO_PASSWORD}")
    if reports:
        print("\nСценарий демо — README, раздел «Показ за 4 минуты».")


if __name__ == "__main__":
    main()
