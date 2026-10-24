"""Настройки сервиса из окружения и `.env` (человек B). Секретов в репозитории нет: см. `.env.example`."""
from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent                  # в контейнере backend лежит в /srv, данные и отчёты — в /data и /reports


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore")

    database_url: str = f"sqlite:///{BACKEND_DIR / 'kassa.db'}"
    jwt_secret: str = "dev-only-secret-change-me-in-env-0123456789"
    jwt_ttl_hours: int = 12
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    forecast_mode: Literal["engine", "fixture"] = "engine"      # fixture — отдавать готовый ответ (К1, тесты)
    demo_mode: bool = True                                      # вход персоны одним нажатием, машина времени
    login_attempts_per_minute: int = 5
    trust_proxy_headers: bool = False                           # True за nginx: адрес клиента из X-Forwarded-For
    data_dir: Path = ROOT / "data"
    reports_dir: Path = ROOT / "reports"
    upload_dir: Path = Path(tempfile.gettempdir()) / "kassa-uploads"
    fixture_path: Path = BACKEND_DIR / "tests" / "fixtures" / "forecast_example.json"
    telegram_bot_token: str = ""                                # пусто — сообщения только пишем в журнал без текста
    cbr_key_rate_fallback: float = 0.21                         # запасная ключевая ставка, если ЦБ недоступен

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
