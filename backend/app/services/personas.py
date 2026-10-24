"""Демо-персоны (§15.2): вход одним нажатием и данные для карточек.

Олег — не C103, а C109: у C103 нет ни одного документа клиентов, а для «Вам должны» и «клиент задержит оплату»
нужны B2B-оплаты по счетам. Название, отрасль и форму собственности `seed_demo` берёт из `companies.csv`.
"""
from __future__ import annotations

from dataclasses import dataclass

DEMO_PASSWORD = "demo1234"
DEMO_AS_OF = "2026-07-31"                    # «машина времени»: данные персон видны до этой даты
JURY_EMAIL_PREFIX = "jury-"                  # режим жюри: остальные компании датасета, на карточках входа их нет


def jury_email(company_id: str) -> str:
    return f"{JURY_EMAIL_PREFIX}{company_id.lower()}@example.com"


@dataclass(frozen=True)
class Persona:
    email: str
    display_name: str
    company_id: str
    fallback_name: str                       # пока датасет не загружен
    fallback_industry: str
    fallback_legal_form: str
    note: str
    accounting_source: str | None = None     # откуда счета: demo_accounting — «учётная система» из выписки


PERSONAS: tuple[Persona, ...] = (
    Persona("irina@example.com", "Ирина", "C064", "ИП Образовательный центр для детей",
            "Образовательный центр для детей", "ИП",
            "Летом занятий меньше, а зарплата и аренда прежние. Хватит ли денег до осени?"),
    Persona("oleg@example.com", "Олег", "C109", "Грузоперевозки", "Грузоперевозки", "ООО",
            "Клиенты платят по счетам с задержкой. Кто сколько должен и что будет, если один из них задержит оплату?",
            accounting_source="demo_accounting"),
    Persona("coffee@example.com", "Анна", "C107", "Кофейня", "Кофейня", "ООО",
            "Выручка каждый день по картам и СБП, аренда раз в месяц. Какой запас нужен перед платежами?"),
)
