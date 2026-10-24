import csv

import pytest
from sqlalchemy import text

from backend.config import PROJECT_ROOT

pytestmark = pytest.mark.integration

SEEDED = ("customers", "purchase_history")


def csv_rows(name):
    with open(PROJECT_ROOT / "data" / "developers" / f"{name}.csv", newline="", encoding="utf-8") as file:
        return sum(1 for _ in csv.DictReader(file))


def test_both_databases_answer(test_database, sql_server):
    with test_database.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    with sql_server.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM dbo.products")).scalar_one() > 0


def test_test_database_holds_only_the_seed(test_database, row_counts):
    counts = row_counts(test_database)
    for name in SEEDED:
        assert counts[name] == csv_rows(name)
    assert {name: n for name, n in counts.items() if name not in SEEDED and n} == {}


def test_real_database_is_untouched(settings, real_database, real_counts_before, test_database, row_counts):
    assert settings.test_database != settings.postgres_db
    assert row_counts(real_database) == real_counts_before
