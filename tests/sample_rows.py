"""Rows for the runtime tables, with valid defaults, so each test states only the fields it is about."""
from datetime import datetime
from decimal import Decimal

from backend.domain.clock import IST

AT = datetime(2026, 10, 24, 12, 0, tzinfo=IST)


def order_row(order_id, *, checkout_key=None, customer_id="CUST-0002", status="placed", method="ship_to_home",
              location_id="LOC-W01", net_amount_due="999.00", lines=(), returns=(), points_redeemed=0,
              delivery_slot=None, confirmed_at=AT):
    return {
        "order_id": order_id, "customer_id": customer_id, "checkout_key": checkout_key or f"CHK-{order_id}",
        "order_channel": "web", "fulfillment_method": method, "fulfillment_location_id": location_id,
        "delivery_slot": delivery_slot, "lines": list(lines), "returns": list(returns),
        "subtotal": Decimal(net_amount_due), "final_total": Decimal(net_amount_due),
        "net_amount_due": Decimal(net_amount_due), "points_redeemed": points_redeemed,
        "order_status": status, "status_history": [], "confirmed_at": confirmed_at,
    }


def line(line_id, sku, status="active", unit_price="999.00"):
    return {"line_id": line_id, "sku": sku, "quantity": 1, "unit_price": unit_price, "line_status": status}


def payment_row(payment_id, *, checkout_key, amount, order_id=None, outcome="success", attempt_number=1,
                customer_id="CUST-0002", at=AT):
    success = outcome == "success"
    return {
        "payment_id": payment_id, "checkout_key": checkout_key, "purpose": "checkout", "customer_id": customer_id,
        "order_id": order_id, "method_type": "upi", "method_ref": None, "amount": Decimal(amount),
        "outcome": outcome, "failed_stage": None if success else "authorization",
        "authorized_at": at if success else None, "captured_at": at if success else None,
        "actor_type": "customer", "actor_id": customer_id, "attempt_number": attempt_number, "created_at": at,
    }


def refund_row(refund_id, *, amount, status, order_id=None, payment_id=None, source_type="cancellation",
               source_ref=None, retry_of=None, at=AT):
    return {
        "refund_id": refund_id, "order_id": order_id, "payment_id": payment_id, "source_type": source_type,
        "source_ref": source_ref or order_id, "amount": Decimal(amount), "refund_status": status,
        "retry_of": retry_of, "created_at": at,
    }


def hold_row(hold_id, *, sku, location_id, status="committed", quantity=1, reference_type="order",
             reference_id="ORD-T1", order_line_id=None, expires_at=None):
    return {
        "hold_id": hold_id, "sku": sku, "location_id": location_id, "quantity": quantity,
        "reference_type": reference_type, "reference_id": reference_id, "order_line_id": order_line_id,
        "hold_status": status, "expires_at": expires_at,
    }


def reservation_row(reservation_id, *, status="confirmed", items=(), customer_id="CUST-0004", location_id="LOC-S01"):
    return {
        "reservation_id": reservation_id, "customer_id": customer_id, "location_id": location_id,
        "slot": {"date": "2026-10-25", "start": "11:00:00", "end": "11:30:00"}, "items": list(items),
        "reservation_status": status, "status_history": [], "confirmed_at": AT,
    }
