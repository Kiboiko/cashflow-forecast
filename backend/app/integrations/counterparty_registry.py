"""Проверка контрагентов по ИНН (P2). ДЕМО-ЗАГЛУШКА вместо запроса к ЕГРЮЛ/ФНС: статусы условные.

Настоящему сервису хватило бы одного ИНН — ни сумм, ни назначений, ни названий отправлять не нужно. Здесь наружу ничего не уходит:
по умолчанию все контрагенты «действуют», а особые статусы задаются файлом `data/demo/registry_mock.json`:

    {"0030000001": {"status": "liquidating", "note": "Подана заявка на ликвидацию"}}

Так на демо можно показать предупреждение «клиент ликвидируется» и не выдавать выдуманный статус за настоящий.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger("kassa.registry")

Status = Literal["active", "liquidating", "bankrupt", "unreliable"]
TITLES: dict[str, str] = {
    "liquidating": "Идёт ликвидация",
    "bankrupt": "Признан банкротом",
    "unreliable": "Недостоверные сведения",
}


@dataclass(frozen=True)
class Check:
    inn: str
    status: Status
    note: str | None = None

    @property
    def title(self) -> str:
        return TITLES.get(self.status, "Действует")


class DemoRegistry:
    """Читает переопределения из файла при каждом обращении, если он изменился: правку на демо не нужно перезапускать."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._mtime: float | None = None
        self._overrides: dict[str, Check] = {}

    def _load(self) -> dict[str, Check]:
        if self._path is None or not self._path.is_file():
            self._mtime, self._overrides = None, {}
            return self._overrides
        mtime = self._path.stat().st_mtime
        if mtime == self._mtime:
            return self._overrides
        found: dict[str, Check] = {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8-sig"))
            for inn, item in raw.items():
                status = item.get("status") if isinstance(item, dict) else item
                if status in TITLES:
                    note = item.get("note") if isinstance(item, dict) else None
                    found[str(inn)] = Check(str(inn), status, note if isinstance(note, str) else None)
        except (OSError, ValueError, AttributeError):
            log.warning("registry mock unreadable")
        self._mtime, self._overrides = mtime, found
        return found

    def check(self, inn: str) -> Check:
        return self._load().get(inn.strip(), Check(inn.strip(), "active"))

    def flagged(self, inns: list[str]) -> list[Check]:
        """Из переданных ИНН — только те, у кого статус не «действует» (порядок как в запросе, без повторов)."""
        seen: set[str] = set()
        out = []
        for inn in inns:
            if inn in seen:
                continue
            seen.add(inn)
            result = self.check(inn)
            if result.status != "active":
                out.append(result)
        return out
