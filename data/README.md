# Данные

Датасет организаторов в git не хранится (≈128 МБ, материалы кейса). Положите архив `cashflow_dataset_participants.zip`
в корень репозитория и выполните `make data` — файлы окажутся в `data/raw/`:

```
data/raw/companies.csv          # справочник: 150 компаний
data/raw/all_operations.csv     # 222 420 операций всех компаний
data/raw/statements/C001.csv …  # выписки по компаниям
```

Формат: UTF-8 с BOM, разделитель `;`, ИНН читать строкой.

`data/demo/` — сгенерированные счета и договоры демо-«учётной системы» (`backend/scripts/make_demo_plans.py`).
