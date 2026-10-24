from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import bindparam, text

from backend.db.health import check

# Read-only access to the client catalogue in SQL Server (DB-40). Every query is fixed and parameterised;
# no other module runs SQL against SQL Server, except the Catalogue Query agent's validated queries.
TABLES = ("products", "product_variants", "product_recommendations")

_PRODUCT_COLUMNS = ("product_id, product_name, category, subcategory, description, "
                    "fabric, material, fit, occasion, segment, season_tags, image_urls")
_VARIANT_COLUMNS = "sku, product_id, size, color, barcode, list_price"


@dataclass(frozen=True)
class Variant:
    sku: str
    product_id: str
    size: str
    color: str
    barcode: str
    list_price: Decimal  # the list price only; the price a customer pays comes from Loyalty and Offers (LO-05)


@dataclass(frozen=True)
class Product:
    product_id: str
    product_name: str
    category: str
    subcategory: str
    description: str
    fabric: str | None
    material: str | None
    fit: str | None
    occasion: str | None
    segment: str | None
    season_tags: tuple[str, ...]
    image_urls: tuple[str, ...]
    variants: tuple[Variant, ...]


@dataclass(frozen=True)
class Recommendation:
    product_id: str
    recommended_product_id: str
    reason: str  # pairs_with or alternative


def _split(value):
    # season_tags and image_urls hold "|"-separated values (DATA_SPEC 2.3)
    return tuple(part for part in value.split("|") if part) if value else ()


def _in(sql, name):
    return text(sql).bindparams(bindparam(name, expanding=True))


def _variants(connection, column, values):
    rows = connection.execute(
        _in(f"SELECT {_VARIANT_COLUMNS} FROM dbo.product_variants WHERE {column} IN :values ORDER BY product_id, sku",
            "values"),
        {"values": values},
    ).mappings()
    return [Variant(**row) for row in rows]


def products(connection, product_ids):
    """The products with their variants, in the order asked; unknown IDs are left out."""
    wanted = list(dict.fromkeys(product_ids))
    if not wanted:
        return []
    rows = connection.execute(
        _in(f"SELECT {_PRODUCT_COLUMNS} FROM dbo.products WHERE product_id IN :ids", "ids"), {"ids": wanted}
    ).mappings().all()
    variants_by_product = {}
    for variant in _variants(connection, "product_id", wanted):
        variants_by_product.setdefault(variant.product_id, []).append(variant)
    found = {
        row["product_id"]: Product(
            **{**row, "season_tags": _split(row["season_tags"]), "image_urls": _split(row["image_urls"])},
            variants=tuple(variants_by_product.get(row["product_id"], ())),
        )
        for row in rows
    }
    return [found[product_id] for product_id in wanted if product_id in found]


def product(connection, product_id):
    found = products(connection, [product_id])
    return found[0] if found else None


def variants(connection, skus):
    wanted = list(dict.fromkeys(skus))
    return _variants(connection, "sku", wanted) if wanted else []


def variant(connection, sku):
    found = variants(connection, [sku])
    return found[0] if found else None


def variant_by_barcode(connection, barcode):
    found = _variants(connection, "barcode", [barcode])
    return found[0] if found else None


def recommendations(connection, product_id, reason=None):
    """Catalogue pairings for a product: pairs_with (cross-sell) or alternative (out of stock)."""
    sql = ("SELECT product_id, recommended_product_id, reason FROM dbo.product_recommendations "
           "WHERE product_id = :product_id")
    parameters = {"product_id": product_id}
    if reason is not None:
        sql += " AND reason = :reason"
        parameters["reason"] = reason
    rows = connection.execute(text(sql + " ORDER BY recommended_product_id"), parameters).mappings()
    return [Recommendation(**row) for row in rows]


def health(engine):
    return check(engine, *(f"SELECT TOP 1 1 FROM dbo.{table}" for table in TABLES))
