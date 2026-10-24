from dataclasses import dataclass

from sqlalchemy import text

from backend.db.health import check

# Read-only access to the client's staff records (DB-40). The password column is never selected here;
# sign-in reads the password hash through its own function once hashing is in place (DB-07).
TABLES = ("staff_data",)

_COLUMNS = "staff_id, name, email, role, location_id"


@dataclass(frozen=True)
class StaffMember:
    staff_id: str
    name: str
    email: str
    role: str  # store_associate or administrator
    location_id: str | None  # empty for administrators


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


def health(engine):
    return check(engine, *(f"SELECT TOP 1 1 FROM dbo.{table}" for table in TABLES))
