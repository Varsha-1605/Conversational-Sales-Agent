import csv

import pytest
from sqlalchemy import text

from config import PROJECT_ROOT

pytestmark = pytest.mark.integration

SEED_FILES = {
    "customers": PROJECT_ROOT / "data" / "developers" / "customers.csv",
    "purchase_history": PROJECT_ROOT / "data" / "developers" / "purchase_history.csv",
    "test_payment_methods": PROJECT_ROOT / "fixtures" / "test_payment_methods.csv",
}
SEEDED = (*SEED_FILES, "journeys")


def csv_rows(name):
    with open(SEED_FILES[name], newline="", encoding="utf-8") as file:
        return sum(1 for _ in csv.DictReader(file))


def test_both_databases_answer(test_database, sql_server):
    with test_database.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
    with sql_server.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM dbo.products")).scalar_one() > 0


def test_test_database_holds_only_the_seed(test_database, row_counts):
    counts = row_counts(test_database)
    for name in SEED_FILES:
        assert counts[name] == csv_rows(name)
    assert counts["journeys"] == counts["customers"]  # every customer has a journey row (DB-35)
    assert {name: n for name, n in counts.items() if name not in SEEDED and n} == {}


def test_real_database_is_untouched(settings, real_database, real_counts_before, test_database, row_counts):
    assert settings.test_database != settings.postgres_db
    assert row_counts(real_database) == real_counts_before
