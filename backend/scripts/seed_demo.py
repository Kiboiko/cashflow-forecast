"""Демо-данные: персоны (§15.2), их выписки до 31.07.2026 («машина времени»), контекст «со стороны».

Запуск из backend/:   uv run python scripts/seed_demo.py [--reset] [--raw ../data/raw]
В Docker:             make seed   (docker compose exec api python scripts/seed_demo.py)
Датасет нужен только для выписок; без него создаются персоны, а прогноз можно смотреть в режиме FORECAST_MODE=fixture.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.session import init_db, make_engine, make_session_factory
from app.engine.io import load_companies
from app.logging_setup import setup_logging
from app.services.personas import DEMO_PASSWORD, PERSONAS
from app.services.seed_service import ensure_personas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", type=Path, default=None, help="каталог с датасетом (по умолчанию <data>/raw)")
    parser.add_argument("--reset", action="store_true", help="пересоздать данные персон с нуля")
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()
    raw = args.raw or settings.data_dir / "raw"
    engine = make_engine(settings.database_url)
    init_db(engine)

    companies = load_companies(raw / "companies.csv") if (raw / "companies.csv").exists() else None
    if companies is None:
        print(f"Датасет не найден в {raw}: создаю персон без выписок (см. data/README.md).")
    with make_session_factory(engine)() as db:
        businesses = ensure_personas(db, companies)

    print("Демо-предприниматели:")
    for persona, business in zip(PERSONAS, businesses, strict=True):
        print(f"  {persona.display_name:8} {business.id}  {persona.email}  пароль: {DEMO_PASSWORD}  — {business.name}")


if __name__ == "__main__":
    main()
