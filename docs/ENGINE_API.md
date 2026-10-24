# Движок прогноза: как подключить к сервису

Для человека B. Движок — чистые функции в `backend/app/engine/` (pandas и numpy, без FastAPI и БД). Все контракты ответов — в
`backend/app/schemas.py`; пример готового ответа — `backend/tests/fixtures/forecast_example.json` (C064, 31.07.2026).
Запуск тестов: `cd backend && uv run pytest -q` (≈240 тестов движка, ≈20 с).

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
- `user_rules` — `CounterpartyRule`: `{cp_key (ИНН или нормализованное имя): код категории}`. Правило действует только на
  операции того же направления, что и категория (возврат от поставщика не станет «Поставщики»): категорию прихода на расход
  движок не поставит. В диалоге правки показывайте категории по направлению операции —
  `app.engine.categorize.taxonomy.category_direction(code)` (+1 приход, −1 расход).
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

Бустинг выручки (M2, `build_model(..., revenue_method="m2")` + `Context.revenue_m2`) в продукт **не включён**: по результатам
бэктеста основной остаётся декомпозиция M1 (см. `reports/backtest.md`, §7.1). `build_context` M2 не обучает.

## 4. План предпринимателя и счета

```python
from app.engine.io import load_invoices
from app.engine.plan import manual_event
from app.engine.receivables import invoices_from_frame, restore_open_documents

planned = [manual_event(EventCreate(...), event_id)]                       # «клиент обещал 300 000 ₽ 20-го»
invoices = invoices_from_frame(load_invoices(path))                        # CSV счетов из §13 (или DataFrame из 1С)
invoices = restore_open_documents(enrich(categorize(ops_full)), as_of)    # демо «учётная система» (подписывать честно)
result = state.forecast(planned=planned, invoices=invoices)
```

CSV счетов читайте через `load_invoices` (все столбцы строками), иначе ИНН вида `0044…` потеряют ведущие нули. Даты —
`2026-08-05` или `05.08.2026`, суммы — `150000.5`, `150 000,50`. На строке без даты документа или с направлением не `in`/`out`
`invoices_from_frame` бросает `ValueError("Строка N: …")` — покажите это пользователю как ошибку импорта.

Для B2B-демо («Вам должны», «попросите оплатить», «клиент задержит оплату») берите **C109** (грузоперевозки): на 31.07.2026
у него 23 открытых документа. У C103 в назначениях нет ни одного документа клиентов — эти экраны у него будут пустыми.
`scripts/make_demo_plans.py` пишет CSV счетов для C064, C107 и C109.

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
`fixed_cost_increase`). Рекомендации в сценарии не считаются. Сумма в `summary` («на 180 000 ₽ больше») — та же, что
`delta.shortfall_p50`; текст итога в коде не собирайте, берите `summary`.

Рекомендации (`result.recommendations`): `collect_receivable` («Попросите клиента оплатить документ № … до …», только при
подключённых счетах), `defer_supplier`, `postpone_withdrawal`, `move_own_funds`, `credit_line` — до четырёх, кредит всегда
последний.

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

Ручное событие считается выполненным, если в окне дат пришла операция того же направления: от контрагента с указанным
названием (`counterparty_name`, ±10% суммы) или, если название не указано, на ту же сумму (±1%). Иначе после допуска —
«не пришла оплата».

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
- ML-добивка категоризации — `EngineState.build(..., ml_model=ml_fallback.train(...))`, необязательна; бустинг выручки M2
  сравнён и в продукт не включён. Интеграции (1С, Telegram, реестры) — на стороне сервиса.
