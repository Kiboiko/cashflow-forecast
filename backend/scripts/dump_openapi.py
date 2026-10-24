"""Печатает OpenAPI-схему сервиса без запуска сервера — из неё `make types` собирает типы фронта.

Запуск из backend/:  uv run python scripts/dump_openapi.py [файл]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import AppSettings
from app.main import create_app


def main() -> None:
    app = create_app(AppSettings(database_url="sqlite://", forecast_mode="fixture"))
    text = json.dumps(app.openapi(), ensure_ascii=False, indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
