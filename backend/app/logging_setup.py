"""JSON-логи без чувствительных данных (§10).

Маскирование стоит на уровне фабрики записей: любое сообщение любого логгера (в том числе uvicorn и тесты)
проходит через `mask` до того, как попадёт в обработчик. Правило кода сверх маски: не логировать назначения
платежей, имена контрагентов и суммы — только id, количества и длительности.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sys

_ACCOUNT = re.compile(r"(?<!\d)\d{20}(?!\d)")                   # номер счёта
_INN = re.compile(r"(?<!\d)(?:\d{10}|\d{12})(?!\d)")            # ИНН юрлица и ИП
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_BOT_TOKEN = re.compile(r"(?i)\bbot\d{5,}:[\w-]{10,}")          # токен бота Telegram стоит прямо в адресе запроса
_QUERY = re.compile(r"\?\S*")

MASK = "***"
MSK = dt.timezone(dt.timedelta(hours=3))
_installed = False
_OWN_HANDLER_FLAG = "_kassa_json_handler"


def mask(text: str) -> str:
    """Скрывает номера счетов, ИНН, email и токены (входа и бота Telegram)."""
    text = _BEARER.sub(f"Bearer {MASK}", text)
    text = _BOT_TOKEN.sub(f"bot{MASK}", text)
    text = _ACCOUNT.sub(MASK, text)
    text = _INN.sub(MASK, text)
    return _EMAIL.sub(MASK, text)


def install_masking() -> None:
    """Подменяет фабрику записей журнала: сообщение маскируется при создании записи. Безопасно вызывать повторно."""
    global _installed
    if _installed:
        return
    original = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = original(*args, **kwargs)
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) > 2:
            args_list = list(record.args)
            args_list[2] = _QUERY.sub("", str(args_list[2]))       # параметры запроса (поиск q=…) в журнал не пишем
            record.args = tuple(args_list)
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — неверный формат сообщения не должен ронять запрос
            message = str(record.msg)
        record.msg, record.args = mask(message), ()
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": dt.datetime.fromtimestamp(record.created, MSK).isoformat(timespec="milliseconds"),
            "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            data.update({k: (mask(v) if isinstance(v, str) else v) for k, v in fields.items()})
        if record.exc_info:
            data["exc"] = mask(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    install_masking()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _OWN_HANDLER_FLAG, True)
    root = logging.getLogger()
    # Заменяем только свой прежний обработчик: чужие (например, перехват логов в pytest) остаются на месте.
    root.handlers[:] = [h for h in root.handlers if not getattr(h, _OWN_HANDLER_FLAG, False)] + [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")               # строку запроса пишет наш middleware (без параметров)
    access.handlers.clear()
    access.propagate = False
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)       # SQL с параметрами не пишем
    for noisy in ("httpx", "httpcore"):                                    # httpx пишет адрес запроса целиком, а в нём токен бота
        logging.getLogger(noisy).setLevel(logging.WARNING)
