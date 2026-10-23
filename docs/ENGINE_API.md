# Движок прогноза: как подключить к сервису

Для человека B. Движок — чистые функции в `backend/app/engine/` (pandas и numpy, без FastAPI и БД). Все контракты ответов — в
`backend/app/schemas.py`; пример готового ответа — `backend/tests/fixtures/forecast_example.json` (C064, 31.07.2026).
Запуск тестов: `cd backend && uv run pytest -q` (211 тестов, ≈12 с).

## 1. Данные на входе

Операции одного бизнеса — `pandas.DataFrame` с колонками датасета (`company_id, operation_id, operation_date, amount,
direction` = «Поступление»/«Списание», `counterparty_inn, counterparty_name, payment_purpose, balance_eod`):

```python
from app.engine.normalize import normalize
ops = normalize(df)                       # из БД: те же колонки; из CSV: app.engine.io.load_operations(path)
```

Дальше везде работаем с `ops` после `normalize`. Даты строго `≤ as_of`: движок сам отрежет лишнее.

## 2. Главный сценарий: состояние и прогноз

```python
from app.engine.state import EngineState
from app.engine.response import to_response
from app.engine.types import Settings

state = EngineState.build(ops, as_of, Settings(**business.settings), user_rules={cp_key: category}, context=context)
result = state.forecast(planned=[...], horizon=30)          # ForecastResult, ≈0,1 с
response = to_response(result, business_id)                  # ForecastResponse (контракт §8) → отдать как JSON
```

- `EngineState.build` — полная сборка (качество данных, категоризация, обогащение): **при импорте выписки**, смене правил
  категорий (`user_rules`), раз в 30 дней. Состояние можно держать в памяти или собирать из БД на запрос (≈0,1–0,2 с).
- `Settings` — поля `Business.settings`: `horizon_days, threshold, overdraft_limit, risk_yellow, risk_red, other_accounts,
  include_other_accounts, include_owner_withdrawals`.
- `user_rules` — `CounterpartyRule`: `{cp_key (ИНН или нормализованное имя): код категории}`.
- Детерминизм: `seed` по умолчанию — хэш (бизнес, as_of); один и тот же вход даёт тот же ответ. Для кэша ключ:
  `(business_id, версия данных, hash(settings), hash(planned/invoices), as_of, horizon)`.
- Перед кэшированием/сериализацией в БД: `result.simulation = None` (там матрицы сценариев, они нужны только рекомендациям
  и сценариям внутри вызова).

Без состояния: `from app.engine.pipeline import run_forecast; run_forecast(ops, as_of, settings, planned, horizon=30)`.

## 3. Контекст «со стороны» (сезонность отрасли, индекс ЕНП, задержки по отрасли)

```python
from app.engine.context import build_context
context = build_context(enrich(categorize(ops_all)), companies_df, company_id, as_of)   # по ВСЕМ компаниям ≤ as_of
```

Считается один раз на дату (несколько секунд) — **в `make seed`**, дальше хранить и передавать в `EngineState.build`.
`ops_all` должны быть после `categorize` и `enrich`. Без контекста прогноз работает (хуже на коротких историях).

## 4. План предпринимателя и счета

```python
from app.engine.plan import manual_event
from app.engine.receivables import invoices_from_frame, restore_open_documents

planned = [manual_event(EventCreate(...), event_id)]                       # «клиент обещал 300 000 ₽ 20-го»
invoices = invoices_from_frame(csv_df)                                     # CSV счетов из §13 или 1С
invoices = restore_open_documents(enrich(categorize(ops_full)), as_of)    # демо «учётная система» (подписывать честно)
result = state.forecast(planned=planned, invoices=invoices)
```

`invoices=None` — источник счетов не подключён (прогноз по недельному бутстрэпу оплат клиентов). Любой список (даже пустой) —
подключён: оплаты клиентов и поставщиков ведут явные счета и модель «выставление × задержка». Источник виден в
`event.source` (`csv`, `1c`, `demo_accounting`, `manual`, `contract_detected`, `recurring`, `tax_calendar`).

«Вам должны»: `state.receivables(invoices, "in")` — по клиентам: сколько должны, обычная задержка («18±6 дн.»), просрочка по
меркам клиента, ожидаемые даты оплаты. «Вы должны» — `"out"`.

## 5. «Что если»

```python
from app.engine.pipeline import run_scenario
from app.engine.response import to_scenario_response

model = state.model(planned, invoices=invoices)
base, scenario = run_scenario(model, req.shocks, n_paths=1000)            # на общих случайных числах
return to_scenario_response(base, scenario, req.shocks)                  # ScenarioResponse + текст итога
```

`req.shocks` — `ScenarioRequest.shocks` из `schemas.py` (`client_delay`, `revenue_drop`, `one_off_expense`,
`fixed_cost_increase`). Рекомендации в сценарии не считаются.

## 6. План-факт, машина времени и предупреждения

Снимок прогноза хранится в `ForecastSnapshot.payload` (JSON): `make_snapshot(result, business_id)`.

Машина времени (`POST /demo/.../replay/advance`):

```python
from app.engine.planfact import make_snapshot, plan_fact
from app.engine.alerts import build_alerts

prev = make_snapshot(result)                                   # снимок до шага
state = state.update(new_ops, new_as_of)                       # дозагрузка: категоризируются только новые операции
result = state.forecast(planned=planned, invoices=invoices)    # новый прогноз
fact, matches = plan_fact(prev, state.ops, new_as_of)          # PlanFactResponse: что пришло, что нет
alerts = build_alerts(result, state.ops, prev["risk"], matches)   # черновики с ключом `key` для дедупликации за 24 ч
```

`AlertDraft`: `type, severity, key, title, body, payload` (типы из контракта `Alert`). Хранение, дедупликацию по `key` за 24 часа
и отправку делает сервис.

Скорость: подготовка данных после дозагрузки 0,20 → 0,09 с, весь путь «новые данные → готовый прогноз с рекомендациями»
0,39 → 0,28 с (12 самых длинных выписок; `reports/update_timing.md`).

## 7. Отчёт бэктеста для страницы «Как мы считаем»

`reports/backtest_<дата>.json` (берите самый свежий файл) — ключи: `cutoffs`, `test.approaches.<подход>.horizons.<7|14|30>`
(`rel_mae_median`, `interval_coverage`, `detection.{roc_auc,pr_auc,precision,recall}`, `lead_days_median`,
`date_error_days_median`), `test.reliability_30d` и `test.reliability_30d_calibrated` (диаграмма надёжности: «говорим 30% —
бывает 30%»), `test.external_ablation`, `test.flows_wape_30d`, `test.by_industry`. Таблица для слайда — `reports/backtest.md`,
картинки — `reports/figures/*.png`. Пересчёт: `uv run python scripts/backtest.py` (≈2,5 мин).

## 8. Что важно помнить

- Шанс разрыва откалиброван (`engine/calibration.json`): «Шанс — высокий (56%)» — это настоящая частота, а не доля сценариев.
- Даты разрыва и покрытие платежей — «сырые» доли сценариев (на бэктесте они хорошо работают, но не калибровались).
- Объяснения строятся шаблонами (`engine/explain.py`), без LLM.
- Все модели учатся только на данных `≤ as_of`; бэктест проверяет отсутствие утечек.
- Ещё нет: ML-добивка категоризации (P2), бустинг выручки (P2). Интеграции (1С, Telegram, реестры) — на стороне сервиса.
