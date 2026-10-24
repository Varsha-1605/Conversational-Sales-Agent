import re

from config import PROJECT_ROOT

# ARCHITECTURE 5.6, PROJECT_STRUCTURE 5 rule 1: only backend/domain/ opens a write transaction. The loader and seed
# scripts and the tests are outside the application and are not checked.
APPLICATION_FOLDERS = ("backend", "agents")
DOMAIN = PROJECT_ROOT / "backend" / "domain"
OPENS_A_TRANSACTION = re.compile(r"\.(begin|begin_nested|commit)\(")


def test_only_the_domain_core_opens_a_write_transaction():
    offenders = []
    for folder in APPLICATION_FOLDERS:
        for path in (PROJECT_ROOT / folder).rglob("*.py"):
            if DOMAIN in path.parents:
                continue
            for number, source_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if OPENS_A_TRANSACTION.search(source_line):
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}")
    assert offenders == []


def test_the_check_would_notice_a_transaction():
    assert OPENS_A_TRANSACTION.search("with engine.begin() as connection:")
    assert OPENS_A_TRANSACTION.search("connection.commit()")
    assert not OPENS_A_TRANSACTION.search("context.begin_transaction()")  # Alembic's own, in migrations/env.py
