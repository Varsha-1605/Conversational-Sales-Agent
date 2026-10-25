from dataclasses import dataclass, field

from sqlalchemy import text

from backend.db.health import check

# Read-only access to the client's staff records (DB-40). The lookups below never select the password hash;
# only staff_login, which sign-in calls, returns it.
TABLES = ("staff_data",)

_COLUMNS = "staff_id, name, email, role, location_id"


@dataclass(frozen=True)
class StaffMember:
    staff_id: str
    name: str
    email: str
    role: str  # store_associate or administrator
    location_id: str | None  # empty for administrators


@dataclass(frozen=True)
class StaffLogin:
    member: StaffMember
    password_hash: str = field(repr=False)  # never printed


def staff_member(connection, staff_id):
    row = connection.execute(
        text(f"SELECT {_COLUMNS} FROM dbo.staff_data WHERE staff_id = :staff_id"), {"staff_id": staff_id}
    ).mappings().first()
    return StaffMember(**row) if row else None


def staff_by_email(connection, email):
    # the server's default collation compares without case, as email addresses should be
    row = connection.execute(
        text(f"SELECT {_COLUMNS} FROM dbo.staff_data WHERE email = :email"), {"email": email}
    ).mappings().first()
    return StaffMember(**row) if row else None


def staff_login(connection, email):
    """The staff member with the stored argon2id hash of their password, for checking a sign-in; None for an
    unknown email. Nothing else may ask for the hash."""
    row = connection.execute(
        text(f"SELECT {_COLUMNS}, password_hash FROM dbo.staff_data WHERE email = :email"), {"email": email}
    ).mappings().first()
    if row is None:
        return None
    password_hash = row["password_hash"]
    return StaffLogin(member=StaffMember(**{name: row[name] for name in StaffMember.__dataclass_fields__}),
                      password_hash=password_hash)


def health(engine):
    return check(engine, *(f"SELECT TOP 1 1 FROM dbo.{table}" for table in TABLES))
