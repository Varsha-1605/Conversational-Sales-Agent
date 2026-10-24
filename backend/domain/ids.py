from sqlalchemy import select

from backend.db.schema import ID_SEQUENCES

# DB-36: readable IDs for every record the product creates. Each kind has a prefix and its own sequence,
# so concurrent writers never get the same ID. Customers keep the seed's 4-digit format and continue after it;
# every other kind uses 6 digits, which can never equal the shorter example IDs in DATA_SPEC
# (ORD-20001, PAY-9002) that tests insert as fixtures. Order line IDs (L1, L2) are numbered within the order.
FORMATS = {
    "customer": ("CUST", 4),
    "order": ("ORD", 6),
    "checkout": ("CHK", 6),     # checkout_key of a checkout payment operation
    "collection": ("COL", 6),   # checkout_key of a collection payment operation
    "payment": ("PAY", 6),
    "refund": ("RFD", 6),
    "hold": ("HLD", 6),
    "reservation": ("RSV", 6),
    "change": ("CHG", 6),       # orders.changes[].change_id
    "return": ("RET", 6),       # orders.returns[].return_id
    "run": ("RUN", 6),          # agent_runs.run_id
}


def next_id(connection, kind):
    prefix, digits = FORMATS[kind]
    number = connection.execute(select(ID_SEQUENCES[kind].next_value())).scalar_one()
    return f"{prefix}-{number:0{digits}d}"
