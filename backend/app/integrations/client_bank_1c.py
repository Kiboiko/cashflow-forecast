"""1С: Клиент-Банк (формат обмена 1CClientBankExchange): чтение выписки и выгрузка для демо (§13).

Файл — текст в Windows-1251 (иногда DOS или UTF-8) со строками `Ключ=Значение` и секциями
`СекцияРасчСчет … КонецРасчСчет` (остатки по счёту) и `СекцияДокумент=… КонецДокумента` (платёжные документы).
Остаток по дням считаем от `НачальныйОстаток`. На выходе — та же таблица, что у CSV датасета (`import_service`).
"""
from __future__ import annotations

import hashlib
import re
import zlib
from datetime import date, datetime

import pandas as pd

HEADER = "1CClientBankExchange"
DATE_FORMAT = "%d.%m.%Y"
_INN_PREFIX = re.compile(r"^\s*ИНН\s*\d+\s*", re.IGNORECASE)


class OneCError(ValueError):
    """Файл 1С нельзя прочитать: сообщение показываем пользователю как есть."""


def decode(content: bytes) -> str:
    """Кодировка — та, в которой читаются служебные слова формата (`Секция…`): Windows-1251, DOS или UTF-8.

    Строке `Кодировка=…` доверять нельзя: сам ключ написан по-русски и в чужой кодировке не находится, а файлы
    в реальной жизни бывают с неверно указанной кодировкой.
    """
    for encoding in ("utf-8-sig", "cp1251", "cp866"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "Секция" in text:
            return text
    raise OneCError("Не удалось прочитать файл 1С: служебные слова не найдены ни в Windows-1251, ни в DOS, ни в UTF-8.")


def _text(value: object) -> str:
    """Значение ячейки как строка; пропуск (None, NaN, NA) — пустая строка."""
    return "" if value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value)) else str(value)


def _number(text: str | None, where: str) -> float:
    try:
        value = float((text or "").replace(" ", "").replace(",", "."))
    except ValueError:
        raise OneCError(f"{where}: неверная сумма «{text}».") from None
    return value


def _day(text: str | None) -> date | None:
    try:
        return datetime.strptime((text or "").strip(), DATE_FORMAT).date() if text and text.strip() else None
    except ValueError:
        return None


def _sections(text: str) -> tuple[dict[str, str], list[dict[str, str]], list[dict[str, str]]]:
    """(заголовок файла, секции расчётных счетов, документы). Неизвестные строки пропускаем."""
    header: dict[str, str] = {}
    accounts: list[dict[str, str]] = []
    documents: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == HEADER or line == "КонецФайла":
            continue
        key, _, value = line.partition("=")
        if key == "СекцияРасчСчет":
            current = {}
            accounts.append(current)
        elif key == "СекцияДокумент":
            current = {"Вид": value}
            documents.append(current)
        elif key in ("КонецРасчСчет", "КонецДокумента"):
            current = None
        elif current is not None:
            current[key] = value
        else:
            header.setdefault(key, value)
    return header, accounts, documents


def _party(doc: dict[str, str], side: str) -> tuple[str | None, str | None]:
    """ИНН и название стороны документа (`Плательщик` или `Получатель`)."""
    inn = (doc.get(f"{side}ИНН") or "").strip() or None
    name = (doc.get(f"{side}1") or "").strip()
    if not name:
        name = _INN_PREFIX.sub("", doc.get(side, "")).strip()
    return inn, name or None


def parse_1c(content: bytes) -> pd.DataFrame:
    """Файл 1С → таблица операций (`date, amount, direction, counterparty_inn, counterparty_name, purpose, balance_eod, external_id`)."""
    if HEADER.encode() not in content[:300]:                      # служебная строка ASCII — читается в любой кодировке
        raise OneCError("Это не выписка 1С: в начале файла нет строки 1CClientBankExchange.")
    header, accounts, documents = _sections(decode(content))
    own_accounts = {a.get("РасчСчет", "").strip() for a in accounts if a.get("РасчСчет")}
    if not own_accounts:
        own_accounts = {header["РасчСчет"].strip()} if header.get("РасчСчет") else set()
    if not own_accounts:
        raise OneCError("В файле 1С не указан расчётный счёт (РасчСчет).")
    if len(own_accounts) > 1:
        raise OneCError("В файле несколько расчётных счетов — загрузите выписку по одному счёту.")
    own = next(iter(own_accounts))
    opening = next((a.get("НачальныйОстаток") for a in accounts if a.get("НачальныйОстаток")), None)
    if opening is None:
        raise OneCError("В файле нет начального остатка (НачальныйОстаток): без него не посчитать остаток по дням.")
    balance = _number(opening, "НачальныйОстаток")

    rows = []
    for n, doc in enumerate(documents, 1):
        where = f"Документ {n}"
        payer_account = (doc.get("ПлательщикРасчСчет") or doc.get("ПлательщикСчет") or "").strip()
        receiver_account = (doc.get("ПолучательРасчСчет") or doc.get("ПолучательСчет") or "").strip()
        if payer_account == own:
            sign, side, day = -1, "Получатель", _day(doc.get("ДатаСписано")) or _day(doc.get("Дата"))
        elif receiver_account == own:
            sign, side, day = 1, "Плательщик", _day(doc.get("ДатаПоступило")) or _day(doc.get("Дата"))
        else:
            continue                                                  # платёж между чужими счетами в выписку не входит
        if day is None:
            raise OneCError(f"{where}: не указана или неверна дата (Дата, ДатаСписано, ДатаПоступило).")
        amount = _number(doc.get("Сумма"), where)
        if amount <= 0:
            raise OneCError(f"{where}: сумма должна быть больше нуля.")
        inn, name = _party(doc, side)
        rows.append({"date": day, "sign": sign, "amount": round(amount, 2), "counterparty_inn": inn,
                     "counterparty_name": name, "purpose": (doc.get("НазначениеПлатежа") or "").strip() or None,
                     "number": (doc.get("Номер") or "").strip()})
    if not rows:
        raise OneCError("В файле 1С нет операций по этому счёту.")

    frame = pd.DataFrame(rows).sort_values("date", kind="stable").reset_index(drop=True)
    frame["direction"] = frame["sign"]
    signed = frame["sign"] * frame["amount"]
    frame["balance_eod"] = (balance + signed.groupby(frame["date"]).transform("sum").where(~frame["date"].duplicated(), 0.0)
                            .cumsum()).round(2)
    frame["balance_eod"] = frame.groupby("date")["balance_eod"].transform("last")      # остаток один на весь день
    seen: dict[str, int] = {}
    ids = []
    for r in frame.itertuples():
        key = f"{r.number}|{r.date}|{r.sign}|{r.amount}|{r.counterparty_inn}"
        seen[key] = seen.get(key, 0) + 1
        ids.append("1c-" + hashlib.sha1(f"{key}#{seen[key]}".encode()).hexdigest()[:18])
    frame["external_id"] = ids
    return frame[["date", "amount", "direction", "counterparty_inn", "counterparty_name", "purpose", "balance_eod", "external_id"]]


def export_1c(rows: pd.DataFrame, own_account: str, company_name: str, company_inn: str) -> bytes:
    """Выписка датасета (нормализованная: `date, amount, direction±1, counterparty_*, purpose, balance_eod`) → файл 1С, Windows-1251.

    Нужен для демо: показать импорт 1С, не имея настоящего файла. Остаток начала — из остатка первого дня.
    """
    rows = rows.sort_values("date", kind="stable").reset_index(drop=True)
    first_day = rows["date"].iloc[0]
    first = rows[rows["date"] == first_day]
    opening = float(first["balance_eod"].iloc[0]) - float((first["direction"] * first["amount"]).sum())
    first_date, last_date = pd.Timestamp(rows["date"].min()).strftime(DATE_FORMAT), pd.Timestamp(rows["date"].max()).strftime(DATE_FORMAT)
    closing = float(rows["balance_eod"].iloc[-1])
    inflow = float(rows.loc[rows["direction"] > 0, "amount"].sum())
    outflow = float(rows.loc[rows["direction"] < 0, "amount"].sum())
    lines = [HEADER, "ВерсияФормата=1.03", "Кодировка=Windows", "Отправитель=Кассовый радар (демо)", "Получатель=",
             f"ДатаСоздания={pd.Timestamp.today().strftime(DATE_FORMAT)}", "ВремяСоздания=09:00:00",
             f"ДатаНачала={first_date}", f"ДатаКонца={last_date}", f"РасчСчет={own_account}",
             "СекцияРасчСчет", f"ДатаНачала={first_date}", f"ДатаКонца={last_date}", f"РасчСчет={own_account}",
             f"НачальныйОстаток={opening:.2f}", f"ВсегоПоступило={inflow:.2f}", f"ВсегоСписано={outflow:.2f}",
             f"КонечныйОстаток={closing:.2f}", "КонецРасчСчет"]
    for n, r in enumerate(rows.itertuples(), 1):
        day = pd.Timestamp(r.date).strftime(DATE_FORMAT)
        inn, name, purpose = _text(r.counterparty_inn), _text(r.counterparty_name), _text(r.purpose)
        other_acc = "40702810" + str(zlib.crc32((inn or name).encode()) % 10**12).zfill(12)
        outgoing = r.direction < 0
        lines += ["СекцияДокумент=Платежное поручение", f"Номер={n}", f"Дата={day}", f"Сумма={r.amount:.2f}",
                  f"ПлательщикСчет={own_account if outgoing else other_acc}",
                  f"ДатаСписано={day if outgoing else ''}",
                  f"Плательщик=ИНН {company_inn if outgoing else inn} {company_name if outgoing else name}",
                  f"ПлательщикИНН={company_inn if outgoing else inn}",
                  f"Плательщик1={company_name if outgoing else name}",
                  f"ПолучательСчет={other_acc if outgoing else own_account}",
                  f"ДатаПоступило={'' if outgoing else day}",
                  f"Получатель=ИНН {inn if outgoing else company_inn} {name if outgoing else company_name}",
                  f"ПолучательИНН={inn if outgoing else company_inn}",
                  f"Получатель1={name if outgoing else company_name}",
                  f"НазначениеПлатежа={purpose}", "КонецДокумента"]
    lines.append("КонецФайла")
    return ("\r\n".join(lines) + "\r\n").encode("cp1251", errors="replace")
