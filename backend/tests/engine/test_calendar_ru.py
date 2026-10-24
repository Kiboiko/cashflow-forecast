from datetime import date

from app.engine.calendar_ru import is_workday, nominal_date, shift, workdays_between
from tests.helpers import RAW_DIR, needs_dataset


def test_holidays_and_working_saturday():
    assert not is_workday(date(2026, 1, 9))      # перенос после новогодних праздников
    assert not is_workday(date(2026, 12, 31))
    assert not is_workday(date(2026, 11, 4))
    assert is_workday(date(2025, 11, 1))         # рабочая суббота
    assert not is_workday(date(2025, 11, 2))     # обычное воскресенье


def test_2027_holidays_that_follow_from_the_labour_code():
    # горизонт 90 дней с конца 2026 года заходит в 2027-й
    assert not is_workday(date(2027, 1, 1)) and not is_workday(date(2027, 1, 8))
    assert not is_workday(date(2027, 5, 3))      # 1 мая — суббота → понедельник
    assert not is_workday(date(2027, 6, 14))     # 12 июня — суббота → понедельник


def test_salary_from_saturday_moves_to_friday():
    assert shift(date(2026, 8, 22), "prev") == date(2026, 8, 21)


def test_enp_from_sunday_moves_to_monday():
    assert date(2025, 12, 28).weekday() == 6
    assert shift(date(2025, 12, 28), "next") == date(2025, 12, 29)


def test_workday_is_not_shifted():
    assert shift(date(2026, 8, 21), "next") == date(2026, 8, 21)
    assert shift(date(2026, 8, 21), "prev") == date(2026, 8, 21)


def test_shift_skips_holiday_block():
    # 31.12.2025 и 01–09.01.2026 нерабочие (09.01 — перенос): первый рабочий день — 12.01
    assert shift(date(2025, 12, 31), "next") == date(2026, 1, 12)
    assert shift(date(2026, 1, 1), "prev") == date(2025, 12, 30)


def test_nominal_date_clips_to_month_end():
    assert nominal_date(2026, 2, 31) == date(2026, 2, 28)
    assert nominal_date(2026, 4, 31) == date(2026, 4, 30)
    assert nominal_date(2026, 8, 25) == date(2026, 8, 25)


def test_workdays_between():
    assert workdays_between(date(2026, 8, 21), date(2026, 8, 24)) == 1   # пт → пн: только пн
    assert workdays_between(date(2026, 8, 24), date(2026, 8, 24)) == 0


@needs_dataset
def test_calendar_matches_dataset_operation_dates():
    """Даты операций в датасете точно следуют производственному календарю."""
    from app.engine.io import load_operations

    dates = load_operations(RAW_DIR / "all_operations.csv")["operation_date"].dt.date.unique()
    bad = [d for d in dates if not is_workday(d)]
    assert bad == []
