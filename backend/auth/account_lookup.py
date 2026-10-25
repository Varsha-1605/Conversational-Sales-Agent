from dataclasses import dataclass, field

from sqlalchemy import select

from backend.db.connect_sqlserver import client_connection
from backend.db.repositories import staff
from backend.db.schema import customers

# Who an email address belongs to. Customers are in our PostgreSQL, staff in the client's SQL Server (read only).
# An email belongs to one or the other, never both (AC-22). Emails are kept and compared in lower case.
# The client's database is opened only when a staff lookup is needed, so a customer's sign-in does not depend on it;
# when it is down, a staff lookup raises ClientDatabaseUnavailable.


@dataclass(frozen=True)
class Account:
    subject_id: str  # a customer_id or a staff_id
    role: str
    store_id: str | None  # the store an associate works at
    password_hash: str | None = field(repr=False)  # never printed


def normalize_email(email: str) -> str:
    return email.strip().lower()


def find_account(postgres, sqlserver_engine, email: str) -> Account | None:
    """The account with this email, with the hash to check a password against. `email` is already normalized."""
    customer = postgres.execute(
        select(customers.c.customer_id, customers.c.password_hash).where(customers.c.email == email)).first()
    if customer is not None:
        return Account(subject_id=customer.customer_id, role="customer", store_id=None,
                       password_hash=customer.password_hash)
    with client_connection(sqlserver_engine) as sqlserver:
        login = staff.staff_login(sqlserver, email)
    if login is not None:
        return Account(subject_id=login.member.staff_id, role=login.member.role, store_id=login.member.location_id,
                       password_hash=login.password_hash)
    return None


def email_is_taken(postgres, sqlserver_engine, email: str) -> bool:
    """Whether a customer or a staff member already uses this email. `email` is already normalized."""
    if postgres.execute(select(customers.c.customer_id).where(customers.c.email == email)).first() is not None:
        return True
    with client_connection(sqlserver_engine) as sqlserver:
        return staff.staff_by_email(sqlserver, email) is not None


def first_name_of(postgres, sqlserver_engine, role: str, subject_id: str) -> str:
    """The first word of the account's name, for greetings. Raises LookupError for an account that is gone."""
    if role == "customer":
        full_name = postgres.execute(
            select(customers.c.full_name).where(customers.c.customer_id == subject_id)).scalar_one_or_none()
    else:
        with client_connection(sqlserver_engine) as sqlserver:
            member = staff.staff_member(sqlserver, subject_id)
        full_name = member.name if member else None
    if not full_name:
        raise LookupError(f"unknown account {subject_id}")
    return full_name.split()[0]
