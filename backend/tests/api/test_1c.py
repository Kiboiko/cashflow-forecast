"""«1С: Клиент-Банк»: разбор файла, выгрузка для демо и загрузка через API."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.engine.normalize import normalize
from app.integrations.client_bank_1c import OneCError, export_1c, parse_1c
from app.schemas import ImportResult
from tests.api.conftest import auth_headers, make_business, make_user
from tests.api.synthetic import synthetic_statement

SAMPLE = """1CClientBankExchange
ВерсияФормата=1.03
Кодировка=Windows
Отправитель=Бухгалтерия предприятия
ДатаСоздания=31.07.2026
РасчСчет=40702810000000000001
СекцияРасчСчет
ДатаНачала=01.07.2026
ДатаКонца=03.07.2026
РасчСчет=40702810000000000001
НачальныйОстаток=100000.00
КонецРасчСчет
СекцияДокумент=Платежное поручение
Номер=15
Дата=01.07.2026
Сумма=25000.50
ПлательщикСчет=40702810000000000001
ДатаСписано=01.07.2026
Плательщик=ИНН 0012345678 ООО Ромашка
ПлательщикИНН=0012345678
Плательщик1=ООО Ромашка
ПолучательСчет=40702810999999999999
Получатель=ИНН 0099999999 ООО Арендодатель
ПолучательИНН=0099999999
Получатель1=ООО Арендодатель
НазначениеПлатежа=Аренда помещения по договору № 5
КонецДокумента
СекцияДокумент=Платежное поручение
Номер=7
Дата=02.07.2026
Сумма=80000
ПлательщикСчет=40702810888888888888
Плательщик=ИНН 0077777777 ООО Клиент
ПлательщикИНН=0077777777
ПолучательСчет=40702810000000000001
ДатаПоступило=02.07.2026
Получатель1=ООО Ромашка
НазначениеПлатежа=Оплата по счету № 12 от 20.06.2026
КонецДокумента
СекцияДокумент=Платежное поручение
Номер=8
Дата=02.07.2026
Сумма=1000
ПлательщикСчет=40702810111111111111
ПолучательСчет=40702810222222222222
КонецДокумента
КонецФайла
"""


def test_parse_sample_direction_balance_and_parties():
    frame = parse_1c(SAMPLE.encode("cp1251"))
    assert list(frame["direction"]) == [-1, 1]                                   # чужой платёж пропущен
    assert list(frame["amount"]) == [25000.5, 80000.0]
    assert list(frame["balance_eod"]) == [74999.5, 154999.5]                    # 100 000 − 25 000,50, затем + 80 000
    assert frame.loc[0, "counterparty_inn"] == "0099999999" and frame.loc[0, "counterparty_name"] == "ООО Арендодатель"
    assert frame.loc[1, "counterparty_inn"] == "0077777777" and frame.loc[1, "counterparty_name"] == "ООО Клиент"   # название из «Плательщик» без ИНН
    assert frame.loc[1, "purpose"].startswith("Оплата по счету")
    assert frame["date"].tolist() == [date(2026, 7, 1), date(2026, 7, 2)]
    assert frame["external_id"].is_unique


def test_parse_dos_and_utf8_encodings():
    dos = SAMPLE.replace("Кодировка=Windows", "Кодировка=DOS").encode("cp866")
    assert len(parse_1c(dos)) == 2
    utf8 = SAMPLE.replace("Кодировка=Windows", "Кодировка=UTF-8").encode("utf-8-sig")
    assert parse_1c(utf8)["counterparty_name"].tolist() == ["ООО Арендодатель", "ООО Клиент"]


@pytest.mark.parametrize("text, fragment", [
    ("просто текст", "не выписка 1С"),
    (SAMPLE.replace("НачальныйОстаток=100000.00\n", ""), "нет начального остатка"),
    (SAMPLE.replace("КонецРасчСчет", "КонецРасчСчет\nСекцияРасчСчет\nРасчСчет=40702810777777777777\nНачальныйОстаток=1\nКонецРасчСчет"), "несколько расчётных счетов"),
    (SAMPLE.replace("Сумма=25000.50", "Сумма=много"), "неверная сумма"),
    (SAMPLE.replace("Сумма=80000", "Сумма=0"), "больше нуля"),
    (SAMPLE.replace("ДатаСписано=01.07.2026", "ДатаСписано=вчера").replace("Дата=01.07.2026", "Дата=вчера"), "дата"),
])
def test_parse_errors_are_readable(text, fragment):
    with pytest.raises(OneCError) as error:
        parse_1c(text.encode("cp1251"))
    assert fragment in str(error.value)


def test_export_then_parse_round_trip():
    """Выписка датасета → файл 1С → разбор даёт те же операции и те же остатки по дням."""
    raw = synthetic_statement(start=date(2026, 3, 1), end=date(2026, 4, 30))
    ops = normalize(raw)
    parsed = parse_1c(export_1c(ops, "40702810000000000064", "ИП Тестовый", "0000000064"))
    assert len(parsed) == len(ops)
    assert parsed["amount"].round(2).tolist() == ops.sort_values("date", kind="stable")["amount"].round(2).tolist()
    by_day = ops.groupby("date")["balance_eod"].last()
    parsed_day = parsed.groupby(pd.to_datetime(parsed["date"]))["balance_eod"].last()
    assert (parsed_day - by_day).abs().max() < 0.01
    assert set(parsed["counterparty_inn"].dropna()) <= set(ops["counterparty_inn"].dropna())


def test_upload_1c_file_through_the_api(fresh_env):
    env = fresh_env
    raw = synthetic_statement(company="C064", start=date(2025, 1, 1), end=date(2026, 9, 15), seed=5)
    content = export_1c(normalize(raw), "40702810000000000064", "ИП Образовательный центр", "0000000064")
    response = env.client.post(env.url("/imports"), headers=env.headers, files={"file": ("1c.txt", content, "text/plain")})
    assert response.status_code == 200, response.text
    result = ImportResult.model_validate(response.json())
    assert result.format == "1c" and result.added > 0 and result.period[1] == date(2026, 9, 15)
    again = env.client.post(env.url("/imports"), headers=env.headers, files={"file": ("1c.txt", content, "text/plain")})
    assert again.json()["added"] == 0                                         # повторная загрузка ничего не задваивает
    forecast = env.client.get(env.url("/forecast"), headers=env.headers).json()
    assert forecast["as_of"] == "2026-09-15"


def test_upload_broken_1c_file_is_422(fresh_env):
    response = fresh_env.client.post(fresh_env.url("/imports"), headers=fresh_env.headers,
                                     files={"file": ("1c.txt", SAMPLE.replace("НачальныйОстаток=100000.00\n", "").encode("cp1251"), "text/plain")})
    assert response.status_code == 422 and "начального остатка" in response.json()["detail"]


def test_1c_import_isolated_from_other_users(fresh_env):
    env = fresh_env
    make_business(env.db, make_user(env.db, "other@example.com", "Другой"), "B-OTHER")
    other = auth_headers(env.client, "other@example.com")
    response = env.client.post(env.url("/imports"), headers=other, files={"file": ("1c.txt", SAMPLE.encode("cp1251"), "text/plain")})
    assert response.status_code == 404
