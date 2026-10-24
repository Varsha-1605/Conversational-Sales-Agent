from sqlalchemy import func, select

from agents.workers.inventory.contract import AvailabilityRequest
from agents.workers.inventory.stock_operations import availability
from agents.workers.loyalty_offers import pricing_rules
from agents.workers.loyalty_offers.contract import QuoteLine, QuoteReply, QuoteRequest, RejectedLine
from backend.db.repositories import catalogue
from backend.db.schema import customers, orders
from backend.domain.clock import to_ist
from backend.domain.consent import ConsentUse, Consent, is_allowed, load_consent
from backend.domain.customer_purchases import calculate_points_balance

# AGENTS 3.3: the only source of prices, discounts, coupons, points and savings (LO-05). It reads list prices from
# the client catalogue and the customer's facts from PostgreSQL, and never calls an LLM. A quote is advisory: the
# domain core repeats the coupon checks under lock_customer_coupons when it creates the order.
# `postgres` and `client` are open connections to the two databases; `now` comes from the caller's clock.


class UnknownSku(ValueError):
    """A quote line names a SKU the catalogue does not have."""


def has_prior_order(postgres, customer_id) -> bool:
    """Whether the customer has an order that is not cancelled. Cancelling an order gives back a first-order coupon."""
    return postgres.execute(
        select(orders.c.order_id).where(orders.c.customer_id == customer_id, orders.c.order_status != "cancelled")
        .limit(1)).first() is not None


def count_coupon_uses(postgres, customer_id, coupon_code) -> int:
    """How many orders that are not cancelled carry the coupon."""
    return postgres.execute(
        select(func.count()).select_from(orders).where(
            orders.c.customer_id == customer_id, orders.c.order_status != "cancelled",
            orders.c.applied_offers.contains([{"coupon_code": coupon_code}]))).scalar_one()


def _load_priced_lines(client, request_lines) -> list[pricing_rules.PricedLine]:
    variants = {variant.sku: variant for variant in catalogue.variants(client, [line.sku for line in request_lines])}
    unknown = [line.sku for line in request_lines if line.sku not in variants]
    if unknown:
        raise UnknownSku(f"unknown SKU: {', '.join(unknown)}")
    products = {product.product_id: product
                for product in catalogue.products(client, [variant.product_id for variant in variants.values()])}
    priced = []
    for line in request_lines:
        variant = variants[line.sku]
        product = products[variant.product_id]
        priced.append(pricing_rules.PricedLine(
            sku=line.sku, product_id=product.product_id, product_name=product.product_name,
            category=product.category, subcategory=product.subcategory, quantity=line.quantity,
            unit_price=variant.list_price))
    return priced


def _customer_facts(postgres, customer_id, coupon_code) -> pricing_rules.CustomerFacts:
    consent: Consent = load_consent(postgres, customer_id)  # raises LookupError for an unknown customer
    tier = postgres.execute(
        select(customers.c.loyalty_tier).where(customers.c.customer_id == customer_id)).scalar_one()
    return pricing_rules.CustomerFacts(
        loyalty_tier=tier, personalization_allowed=is_allowed(consent, ConsentUse.PERSONALIZED_OFFERS),
        points_balance=calculate_points_balance(postgres, customer_id),
        has_prior_order=has_prior_order(postgres, customer_id),
        times_coupon_used=count_coupon_uses(postgres, customer_id, coupon_code) if coupon_code else 0)


def quote(postgres, client, customer_id, request: QuoteRequest, now) -> QuoteReply:
    """Price a cart for a customer. A line whose stock at the fulfilment location is not confirmed is rejected and
    left out of every amount, so it cannot be bought (DR-08). `customer_id` is set by the server, never the caller.
    Raises UnknownSku, and LookupError for an unknown customer."""
    lines = _load_priced_lines(client, request.lines)
    rejected = []
    if request.fulfillment_location_id:
        for line in lines:
            stock = availability(postgres, client, AvailabilityRequest(
                sku=line.sku, location_id=request.fulfillment_location_id), now)
            if stock.state == "unconfirmed":
                rejected.append(RejectedLine(sku=line.sku, reason="stock_not_confirmed"))
    rejected_skus = {line.sku for line in rejected}
    buyable = [line for line in lines if line.sku not in rejected_skus]

    coupon_code = (request.coupon_code or "").strip().upper() or None  # a blank code means no coupon
    customer = _customer_facts(postgres, customer_id, coupon_code)
    pricing = pricing_rules.price_cart(buyable, customer, to_ist(now).date(), coupon_code, request.points_to_use)
    return QuoteReply(
        **pricing.model_dump(),
        lines=[QuoteLine(sku=line.sku, product_id=line.product_id, product_name=line.product_name,
                         quantity=line.quantity, unit_price=line.unit_price, line_total=line.line_total)
               for line in buyable],
        rejected_lines=rejected, quoted_at=now)
