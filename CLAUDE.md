# Кассовый радар — правила для Claude Code

Спецификация: docs/ARCHITECTURE.md. Перед задачей прочитай разделы, на которые она ссылается (§14).

## Продукт
- Пользователь — предприниматель без финансового образования. Тексты интерфейса — простым языком, словарь §9.3.
- Главный вопрос продукта: хватит ли денег на обязательные платежи; если нет — когда, сколько, почему и что сделать.

## Зоны
- Человек A: backend/app/engine/**, backend/scripts/{backtest,eda_facts,make_demo_plans,golden_set}.py, backend/tests/engine/**, reports/**
- Человек B: backend/app/{api,db,services,integrations}/**, security.py, logging_setup.py, frontend/**, docker-compose.yml, Makefile, README.md
- Общая зона: backend/app/schemas.py (контракты §8) — менять только по договорённости.

## Правила кода
- engine/ — чистые функции без FastAPI и БД; модели учатся только на данных с датой ≤ as_of; детерминизм через seed.
- Новая функция engine — с unit-тестом. Перед коммитом: make test && make lint.
- Не логировать назначения платежей, ИНН, имена контрагентов, суммы. Не отправлять данные выписок во внешние API и LLM.
- Датасет: data/raw (не коммитить). CSV: sep=';', encoding='utf-8-sig', ИНН — строкой.

## Git
- Репозиторий: GitHub, Kiboiko/cashflow-forecast. Ветки `a/<задача>`, `b/<задача>`, маленькие мерджи в `main` после `make test`.
- Перед сном `main` зелёный и запускается.

## Дедлайн
- Сдача: воскресенье 25.10.2026, 12:00. К1 сб 01:30, К2 сб 16:00, К3 вс 00:00, К4 (заморозка функций) вс 08:30, К5 (заморозка кода) вс 11:30. Из плана ничего не вырезаем, включая P2 (§14.2).

## Команды
make data | make up | make seed | make test | make lint | make types | make backtest | make demo-reset
