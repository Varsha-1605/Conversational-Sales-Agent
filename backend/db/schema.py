from sqlalchemy import (
    MetaData, Table, Column, String, Text, Integer, Boolean, Date, DateTime, Numeric,
    ForeignKey, PrimaryKeyConstraint, UniqueConstraint, CheckConstraint, Index, text,
)
from sqlalchemy.dialects.postgresql import JSONB

# DATA_SPEC 2.1, 2.2, 4 and 5: every table in our PostgreSQL (project_db).
# Values that name a SKU, store or staff member in SQL Server have no foreign key (DATA_SPEC 1);
# the domain core checks them. Status-like columns are text with a CHECK, not PostgreSQL ENUM types.

metadata = MetaData(naming_convention={
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})

CHANNELS = ("web", "app", "whatsapp", "kiosk", "voice")
AGE_BANDS = ("16-17", "18-24", "25-34", "35-40")
LOYALTY_TIERS = ("Base", "Silver", "Gold")


def one_of(column, values, name):
    quoted = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)


def timestamp(name, **kwargs):
    return Column(name, DateTime(timezone=True), **kwargs)


def json_list(name):
    return Column(name, JSONB, nullable=False, server_default=text("'[]'::jsonb"))


def json_map(name):
    return Column(name, JSONB, nullable=False, server_default=text("'{}'::jsonb"))


# 2.1 customers and 2.2 purchase_history (developer-side data)
customers = Table("customers", metadata,
                  Column("customer_id", String(20), primary_key=True),
                  Column("full_name", String(100), nullable=False),
                  Column("email", String(200), nullable=False, unique=True),
                  Column("password_hash", String(255)), # filled when sign-in is built; the plain password is never loaded
                  Column("age_band", String(5)),
                  Column("gender", String(20)),
                  Column("loyalty_tier", String(10), nullable=False),
                  Column("points_balance", Integer, nullable=False),
                  Column("device_preference", String(10), nullable=False),
                  Column("preferred_store_id", String(20)), # SQL Server locations
                  Column("personalization_allowed", Boolean, nullable=False),
                  Column("save_preferences_allowed", Boolean, nullable=False, server_default=text("false")),
                  Column("saved_preferences", JSONB),
                  one_of("age_band", AGE_BANDS, "age_band"),
                  one_of("loyalty_tier", LOYALTY_TIERS, "loyalty_tier"),
                  one_of("device_preference", CHANNELS, "device_preference"),
                  CheckConstraint("points_balance >= 0", name="points_balance"))

purchase_history = Table("purchase_history", metadata,
                         Column("customer_id", String(20), ForeignKey("customers.customer_id"), nullable=False),
                         Column("sku", String(40), nullable=False), # SQL Server product_variants
                         Column("purchased_on", Date, nullable=False),
                         PrimaryKeyConstraint("customer_id", "sku", "purchased_on"))

# 4.1 journeys: one row per customer
journeys = Table("journeys", metadata,
                 Column("customer_id", String(20), ForeignKey("customers.customer_id"), primary_key=True),
                 Column("active_channel", String(10)), # empty before first use and after sign-out of the active channel
                 json_list("cart"),
                 json_list("shortlist"),
                 json_map("stated_preferences"),
                 json_list("viewed_products"),
                 json_list("declined_suggestions"),
                 Column("context_summary", Text, nullable=False, server_default=text("''")),
                 Column("escalation", JSONB),
                 one_of("active_channel", CHANNELS, "active_channel"))

# 5 test_payment_methods (development-team fixtures)
test_payment_methods = Table("test_payment_methods", metadata,
                             Column("method_ref", String(30), primary_key=True),
                             Column("customer_id", String(20), ForeignKey("customers.customer_id")),
                             Column("method_type", String(10), nullable=False),
                             Column("display_label", String(100), nullable=False),
                             Column("gift_card_code", String(40), unique=True),
                             Column("gift_card_balance", Numeric(12, 2)),
                             Column("simulated_outcome", String(10), nullable=False),
                             Column("simulated_failure_stage", String(15)),
                             one_of("method_type", ("saved_card", "gift_card"), "method_type"),
                             one_of("simulated_outcome", ("success", "declined", "failed", "timeout"), "simulated_outcome"),
                             one_of("simulated_failure_stage", ("authorization", "capture"), "simulated_failure_stage"),
                             CheckConstraint("gift_card_balance >= 0", name="gift_card_balance"))

# 4.2 orders
orders = Table("orders", metadata,
               Column("order_id", String(30), primary_key=True),
               Column("customer_id", String(20), ForeignKey("customers.customer_id"), nullable=False),
               Column("checkout_key", String(40), nullable=False, unique=True),
               Column("order_channel", String(10), nullable=False), # in-store POS orders use kiosk
               Column("fulfillment_method", String(15), nullable=False),
               Column("fulfillment_location_id", String(20), nullable=False), # SQL Server locations
               Column("delivery_slot", JSONB),
               Column("shipping_address", JSONB),
               Column("lines", JSONB, nullable=False),
               json_list("applied_offers"),
               Column("subtotal", Numeric(12, 2), nullable=False),
               Column("discount_total", Numeric(12, 2), nullable=False, server_default=text("0")),
               Column("points_redeemed", Integer, nullable=False, server_default=text("0")),
               Column("points_value", Numeric(12, 2), nullable=False, server_default=text("0")),
               Column("final_total", Numeric(12, 2), nullable=False),
               Column("net_amount_due", Numeric(12, 2), nullable=False),
               Column("order_status", String(20), nullable=False),
               Column("status_history", JSONB, nullable=False),
               json_list("changes"),
               Column("cancellation", JSONB),
               json_list("returns"),
               Column("feedback", JSONB),
               timestamp("confirmed_at", nullable=False),
               one_of("order_channel", CHANNELS, "order_channel"),
               one_of("fulfillment_method", ("ship_to_home", "click_collect", "in_store"), "fulfillment_method"),
               one_of("order_status", ("placed", "ready_for_pickup", "item_not_found", "shipped", "delivered", "collected", "cancelled"), "order_status"),
               CheckConstraint("net_amount_due >= 0", name="net_amount_due"),
               CheckConstraint("points_redeemed >= 0", name="points_redeemed"))

# 4.3 payment_attempts
payment_attempts = Table("payment_attempts", metadata,
                         Column("payment_id", String(30), primary_key=True),
                         Column("checkout_key", String(40), nullable=False),
                         Column("purpose", String(10), nullable=False),
                         Column("customer_id", String(20), ForeignKey("customers.customer_id"), nullable=False),
                         Column("order_id", String(30), ForeignKey("orders.order_id")),
                         Column("method_type", String(10), nullable=False),
                         Column("method_ref", String(30), ForeignKey("test_payment_methods.method_ref")),
                         Column("amount", Numeric(12, 2), nullable=False),
                         Column("outcome", String(10), nullable=False),
                         Column("failed_stage", String(15)),
                         timestamp("authorized_at"),
                         timestamp("captured_at"),
                         Column("actor_type", String(10), nullable=False),
                         Column("actor_id", String(30), nullable=False), # customer_id, or staff_id (SQL Server) for POS
                         Column("attempt_number", Integer, nullable=False),
                         timestamp("created_at", nullable=False),
                         UniqueConstraint("checkout_key", "attempt_number"),
                         one_of("purpose", ("checkout", "collection"), "purpose"),
                         one_of("method_type", ("saved_card", "upi", "gift_card", "pos"), "method_type"),
                         one_of("outcome", ("success", "declined", "failed", "timeout"), "outcome"),
                         one_of("failed_stage", ("authorization", "capture"), "failed_stage"),
                         one_of("actor_type", ("customer", "staff"), "actor_type"),
                         CheckConstraint("amount > 0", name="amount"),
                         CheckConstraint("attempt_number >= 1", name="attempt_number"),
                         # allowed combinations of outcome, failed stage and the two times (DATA_SPEC 4.3)
                         CheckConstraint(
                             "(outcome = 'success' AND failed_stage IS NULL AND authorized_at IS NOT NULL AND captured_at IS NOT NULL)"
                             " OR (outcome IN ('declined', 'failed', 'timeout') AND failed_stage = 'authorization'"
                             " AND authorized_at IS NULL AND captured_at IS NULL)"
                             " OR (outcome IN ('failed', 'timeout') AND failed_stage = 'capture'"
                             " AND authorized_at IS NOT NULL AND captured_at IS NULL)",
                             name="stage_combination"))

# 4.4 refunds
refunds = Table("refunds", metadata,
                Column("refund_id", String(30), primary_key=True),
                Column("order_id", String(30), ForeignKey("orders.order_id")),
                Column("payment_id", String(30), ForeignKey("payment_attempts.payment_id")),
                Column("source_type", String(20), nullable=False),
                Column("source_ref", String(60), nullable=False),
                Column("amount", Numeric(12, 2), nullable=False),
                Column("refund_status", String(10), nullable=False),
                Column("retry_of", String(30), ForeignKey("refunds.refund_id"), unique=True),
                timestamp("created_at", nullable=False),
                one_of("source_type", ("cancellation", "modification", "return", "stock_unavailable"), "source_type"),
                one_of("refund_status", ("initiated", "completed", "failed"), "refund_status"),
                CheckConstraint("amount > 0", name="amount"),
                CheckConstraint("order_id IS NOT NULL OR source_type = 'stock_unavailable'", name="order_or_stock_unavailable"),
                CheckConstraint("order_id IS NOT NULL OR payment_id IS NOT NULL", name="order_or_payment"))

# 4.5 stock_holds
stock_holds = Table("stock_holds", metadata,
                    Column("hold_id", String(30), primary_key=True),
                    Column("sku", String(40), nullable=False), # SQL Server product_variants
                    Column("location_id", String(20), nullable=False), # SQL Server locations
                    Column("quantity", Integer, nullable=False),
                    Column("reference_type", String(15), nullable=False),
                    Column("reference_id", String(60), nullable=False),
                    Column("order_line_id", String(20)),
                    Column("hold_status", String(10), nullable=False),
                    timestamp("expires_at"),
                    one_of("reference_type", ("checkout", "reservation", "order", "exchange"), "reference_type"),
                    one_of("hold_status", ("active", "committed", "released", "lapsed"), "hold_status"),
                    CheckConstraint("quantity > 0", name="quantity"),
                    Index("ix_stock_holds_availability", "sku", "location_id", "hold_status")) # availability sums

# 4.6 reservations
reservations = Table("reservations", metadata,
                     Column("reservation_id", String(30), primary_key=True),
                     Column("customer_id", String(20), ForeignKey("customers.customer_id"), nullable=False),
                     Column("location_id", String(20), nullable=False), # SQL Server locations
                     Column("slot", JSONB, nullable=False),
                     Column("items", JSONB, nullable=False),
                     Column("reservation_status", String(20), nullable=False),
                     json_list("status_history"),
                     timestamp("confirmed_at", nullable=False),
                     one_of("reservation_status", ("confirmed", "customer_arrived", "completed", "lapsed", "cancelled"), "reservation_status"))

# 4.9 agent_runs
agent_runs = Table("agent_runs", metadata,
                   Column("run_id", String(40), primary_key=True),
                   Column("parent_run_id", String(40), ForeignKey("agent_runs.run_id")),
                   Column("trace_id", String(40), nullable=False),
                   Column("customer_id", String(20), ForeignKey("customers.customer_id"), nullable=False),
                   Column("channel", String(10), nullable=False),
                   Column("agent", String(40), nullable=False),
                   Column("operation", String(60), nullable=False),
                   Column("status", String(10), nullable=False),
                   Column("error_code", String(40)),
                   json_map("decision"),
                   Column("llm_input_tokens", Integer, nullable=False, server_default=text("0")),
                   Column("llm_output_tokens", Integer, nullable=False, server_default=text("0")),
                   Column("elapsed_ms", Integer, nullable=False),
                   timestamp("started_at", nullable=False),
                   one_of("channel", CHANNELS, "channel"),
                   one_of("status", ("ok", "partial", "error"), "status"),
                   Index("ix_agent_runs_trace_id", "trace_id"))
