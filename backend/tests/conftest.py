"""Общие фикстуры тестов."""
import pytest

from tests.helpers import make_statement


@pytest.fixture
def statement():
    return make_statement
