from backend.db.repositories import catalogue, locations, staff

# Our PostgreSQL rows point at SKUs, locations and staff in the client's SQL Server. No database can enforce a
# reference across the two, so the domain core checks each one before it writes (DB-16, DATA_SPEC 1).


class UnknownReference(LookupError):
    """A record would point at something that does not exist in the client data."""

    def __init__(self, kind, ids):
        self.kind = kind
        self.ids = tuple(ids)
        super().__init__(f"unknown {kind}: {', '.join(self.ids)}")


def require_location(connection, location_id, *, store=False):
    """The location, which must be a store when store=True. Raises UnknownReference otherwise."""
    found = locations.location(connection, location_id)
    if found is None or (store and found.location_type != "store"):
        raise UnknownReference("store" if store else "location", [location_id])
    return found


def require_skus(connection, skus):
    """Raises UnknownReference naming every SKU that does not exist, so one check covers a whole cart."""
    wanted = list(dict.fromkeys(skus))
    found = {variant.sku for variant in catalogue.variants(connection, wanted)}
    missing = [sku for sku in wanted if sku not in found]
    if missing:
        raise UnknownReference("sku", missing)


def require_staff(connection, staff_id, *, store_id=None):
    """The staff member, who must work at store_id when one is given (associates act for their own store).
    Raises UnknownReference otherwise."""
    found = staff.staff_member(connection, staff_id)
    if found is None or (store_id is not None and found.location_id != store_id):
        raise UnknownReference("staff", [staff_id])
    return found
