"""Прогноз для бизнеса: вызов движка, кэш, снимки (§4.1, ENGINE_API.md).

Режим `fixture` отдаёт готовый ответ `tests/fixtures/forecast_example.json` — для первого запуска, тестов API и
случая, когда датасет ещё не загружен. Режим `engine` — настоящий движок (подключается в B4).
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import AppSettings
from app.db.models import Business
from app.schemas import DataQuality, ForecastResponse


@lru_cache
def _load_fixture(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ForecastService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def forecast(self, db: Session, business: Business, horizon: int | None = None) -> ForecastResponse:
        if self.settings.forecast_mode == "fixture":
            data = copy.deepcopy(_load_fixture(str(self.settings.fixture_path)))
            data["business_id"] = business.id
            return ForecastResponse.model_validate(data)
        raise NotImplementedError("Режим engine подключается в B4")

    def data_quality(self, db: Session, business: Business) -> DataQuality | None:
        if self.settings.forecast_mode == "fixture":
            return ForecastResponse.model_validate(_load_fixture(str(self.settings.fixture_path))).data_quality
        return None
