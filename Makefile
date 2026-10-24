# Команды проекта. Нужны: make, uv, Docker (для `make up`), Node 22 (для `make types` и `make lint`).
# Рецепты написаны так, чтобы работать и в cmd.exe (Windows), и в sh: только `cd x && команда`.
.PHONY: data up down seed seed-local test lint types backtest demo-reset

# Архив датасета (его нет в git) → data/raw/{companies.csv, all_operations.csv, statements/}
DATASET ?= cashflow_dataset_participants.zip

data:
	cd backend && uv run python scripts/unpack_dataset.py ../$(DATASET) ../data/raw

up:
	docker compose up --build

down:
	docker compose down

# Демо-персоны и их выписки (в контейнере api; датасет — из ./data/raw)
seed:
	docker compose exec api python scripts/seed_demo.py

# То же без Docker: БД — backend/kassa.db (SQLite)
seed-local:
	cd backend && uv run python scripts/seed_demo.py

demo-reset:
	docker compose exec api python scripts/seed_demo.py --reset

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check .
	cd frontend && npm run lint

# Типы фронта из OpenAPI: схема собирается без запущенного сервера → frontend/src/api/types.gen.ts
types:
	cd backend && uv run python scripts/dump_openapi.py ../frontend/openapi.json
	cd frontend && npm run gen:types

backtest:
	cd backend && uv run python scripts/backtest.py
