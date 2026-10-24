"""create the ten PostgreSQL tables (DATABASE_ISSUES.md DB-11)

Revision ID: 0001
Revises: 
Create Date: 2026-10-24 12:50:21.808334
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('customers',
    sa.Column('customer_id', sa.String(length=20), nullable=False),
    sa.Column('full_name', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('age_band', sa.String(length=5), nullable=True),
    sa.Column('gender', sa.String(length=20), nullable=True),
    sa.Column('loyalty_tier', sa.String(length=10), nullable=False),
    sa.Column('points_balance', sa.Integer(), nullable=False),
    sa.Column('device_preference', sa.String(length=10), nullable=False),
    sa.Column('preferred_store_id', sa.String(length=20), nullable=True),
    sa.Column('personalization_allowed', sa.Boolean(), nullable=False),
    sa.Column('save_preferences_allowed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('saved_preferences', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("age_band IN ('16-17', '18-24', '25-34', '35-40')", name=op.f('ck_customers_age_band')),
    sa.CheckConstraint("device_preference IN ('web', 'app', 'whatsapp', 'kiosk', 'voice')", name=op.f('ck_customers_device_preference')),
    sa.CheckConstraint("loyalty_tier IN ('Base', 'Silver', 'Gold')", name=op.f('ck_customers_loyalty_tier')),
    sa.CheckConstraint('points_balance >= 0', name=op.f('ck_customers_points_balance')),
    sa.PrimaryKeyConstraint('customer_id', name=op.f('pk_customers')),
    sa.UniqueConstraint('email', name=op.f('uq_customers_email'))
    )
    op.create_table('stock_holds',
    sa.Column('hold_id', sa.String(length=30), nullable=False),
    sa.Column('sku', sa.String(length=40), nullable=False),
    sa.Column('location_id', sa.String(length=20), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('reference_type', sa.String(length=15), nullable=False),
    sa.Column('reference_id', sa.String(length=60), nullable=False),
    sa.Column('order_line_id', sa.String(length=20), nullable=True),
    sa.Column('hold_status', sa.String(length=10), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("hold_status IN ('active', 'committed', 'released', 'lapsed')", name=op.f('ck_stock_holds_hold_status')),
    sa.CheckConstraint("reference_type IN ('checkout', 'reservation', 'order', 'exchange')", name=op.f('ck_stock_holds_reference_type')),
    sa.CheckConstraint('quantity > 0', name=op.f('ck_stock_holds_quantity')),
    sa.PrimaryKeyConstraint('hold_id', name=op.f('pk_stock_holds'))
    )
    op.create_index('ix_stock_holds_availability', 'stock_holds', ['sku', 'location_id', 'hold_status'], unique=False)
    op.create_table('agent_runs',
    sa.Column('run_id', sa.String(length=40), nullable=False),
    sa.Column('parent_run_id', sa.String(length=40), nullable=True),
    sa.Column('trace_id', sa.String(length=40), nullable=False),
    sa.Column('customer_id', sa.String(length=20), nullable=False),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('agent', sa.String(length=40), nullable=False),
    sa.Column('operation', sa.String(length=60), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('error_code', sa.String(length=40), nullable=True),
    sa.Column('decision', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('llm_input_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('llm_output_tokens', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('elapsed_ms', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("channel IN ('web', 'app', 'whatsapp', 'kiosk', 'voice')", name=op.f('ck_agent_runs_channel')),
    sa.CheckConstraint("status IN ('ok', 'partial', 'error')", name=op.f('ck_agent_runs_status')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], name=op.f('fk_agent_runs_customer_id_customers')),
    sa.ForeignKeyConstraint(['parent_run_id'], ['agent_runs.run_id'], name=op.f('fk_agent_runs_parent_run_id_agent_runs')),
    sa.PrimaryKeyConstraint('run_id', name=op.f('pk_agent_runs'))
    )
    op.create_index('ix_agent_runs_trace_id', 'agent_runs', ['trace_id'], unique=False)
    op.create_table('journeys',
    sa.Column('customer_id', sa.String(length=20), nullable=False),
    sa.Column('active_channel', sa.String(length=10), nullable=True),
    sa.Column('cart', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('shortlist', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('stated_preferences', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('viewed_products', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('declined_suggestions', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('context_summary', sa.Text(), server_default=sa.text("''"), nullable=False),
    sa.Column('escalation', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("active_channel IN ('web', 'app', 'whatsapp', 'kiosk', 'voice')", name=op.f('ck_journeys_active_channel')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], name=op.f('fk_journeys_customer_id_customers')),
    sa.PrimaryKeyConstraint('customer_id', name=op.f('pk_journeys'))
    )
    op.create_table('orders',
    sa.Column('order_id', sa.String(length=30), nullable=False),
    sa.Column('customer_id', sa.String(length=20), nullable=False),
    sa.Column('checkout_key', sa.String(length=40), nullable=False),
    sa.Column('order_channel', sa.String(length=10), nullable=False),
    sa.Column('fulfillment_method', sa.String(length=15), nullable=False),
    sa.Column('fulfillment_location_id', sa.String(length=20), nullable=False),
    sa.Column('delivery_slot', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('shipping_address', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('lines', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('applied_offers', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('subtotal', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('discount_total', sa.Numeric(precision=12, scale=2), server_default=sa.text('0'), nullable=False),
    sa.Column('points_redeemed', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('points_value', sa.Numeric(precision=12, scale=2), server_default=sa.text('0'), nullable=False),
    sa.Column('final_total', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('net_amount_due', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('order_status', sa.String(length=20), nullable=False),
    sa.Column('status_history', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('changes', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('cancellation', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('returns', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('feedback', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("fulfillment_method IN ('ship_to_home', 'click_collect', 'in_store')", name=op.f('ck_orders_fulfillment_method')),
    sa.CheckConstraint("order_channel IN ('web', 'app', 'whatsapp', 'kiosk', 'voice')", name=op.f('ck_orders_order_channel')),
    sa.CheckConstraint("order_status IN ('placed', 'ready_for_pickup', 'item_not_found', 'shipped', 'delivered', 'collected', 'cancelled')", name=op.f('ck_orders_order_status')),
    sa.CheckConstraint('net_amount_due >= 0', name=op.f('ck_orders_net_amount_due')),
    sa.CheckConstraint('points_redeemed >= 0', name=op.f('ck_orders_points_redeemed')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], name=op.f('fk_orders_customer_id_customers')),
    sa.PrimaryKeyConstraint('order_id', name=op.f('pk_orders')),
    sa.UniqueConstraint('checkout_key', name=op.f('uq_orders_checkout_key'))
    )
    op.create_table('purchase_history',
    sa.Column('customer_id', sa.String(length=20), nullable=False),
    sa.Column('sku', sa.String(length=40), nullable=False),
    sa.Column('purchased_on', sa.Date(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], name=op.f('fk_purchase_history_customer_id_customers')),
    sa.PrimaryKeyConstraint('customer_id', 'sku', 'purchased_on', name=op.f('pk_purchase_history'))
    )
    op.create_table('reservations',
    sa.Column('reservation_id', sa.String(length=30), nullable=False),
    sa.Column('customer_id', sa.String(length=20), nullable=False),
    sa.Column('location_id', sa.String(length=20), nullable=False),
    sa.Column('slot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('items', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reservation_status', sa.String(length=20), nullable=False),
    sa.Column('status_history', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("reservation_status IN ('confirmed', 'customer_arrived', 'completed', 'lapsed', 'cancelled')", name=op.f('ck_reservations_reservation_status')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], name=op.f('fk_reservations_customer_id_customers')),
    sa.PrimaryKeyConstraint('reservation_id', name=op.f('pk_reservations'))
    )
    op.create_table('test_payment_methods',
    sa.Column('method_ref', sa.String(length=30), nullable=False),
    sa.Column('customer_id', sa.String(length=20), nullable=True),
    sa.Column('method_type', sa.String(length=10), nullable=False),
    sa.Column('display_label', sa.String(length=100), nullable=False),
    sa.Column('gift_card_code', sa.String(length=40), nullable=True),
    sa.Column('gift_card_balance', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('simulated_outcome', sa.String(length=10), nullable=False),
    sa.Column('simulated_failure_stage', sa.String(length=15), nullable=True),
    sa.CheckConstraint("method_type IN ('saved_card', 'gift_card')", name=op.f('ck_test_payment_methods_method_type')),
    sa.CheckConstraint("simulated_failure_stage IN ('authorization', 'capture')", name=op.f('ck_test_payment_methods_simulated_failure_stage')),
    sa.CheckConstraint("simulated_outcome IN ('success', 'declined', 'failed', 'timeout')", name=op.f('ck_test_payment_methods_simulated_outcome')),
    sa.CheckConstraint('gift_card_balance >= 0', name=op.f('ck_test_payment_methods_gift_card_balance')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], name=op.f('fk_test_payment_methods_customer_id_customers')),
    sa.PrimaryKeyConstraint('method_ref', name=op.f('pk_test_payment_methods')),
    sa.UniqueConstraint('gift_card_code', name=op.f('uq_test_payment_methods_gift_card_code'))
    )
    op.create_table('payment_attempts',
    sa.Column('payment_id', sa.String(length=30), nullable=False),
    sa.Column('checkout_key', sa.String(length=40), nullable=False),
    sa.Column('purpose', sa.String(length=10), nullable=False),
    sa.Column('customer_id', sa.String(length=20), nullable=False),
    sa.Column('order_id', sa.String(length=30), nullable=True),
    sa.Column('method_type', sa.String(length=10), nullable=False),
    sa.Column('method_ref', sa.String(length=30), nullable=True),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('outcome', sa.String(length=10), nullable=False),
    sa.Column('failed_stage', sa.String(length=15), nullable=True),
    sa.Column('authorized_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('actor_type', sa.String(length=10), nullable=False),
    sa.Column('actor_id', sa.String(length=30), nullable=False),
    sa.Column('attempt_number', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(outcome = 'success' AND failed_stage IS NULL AND authorized_at IS NOT NULL AND captured_at IS NOT NULL) OR (outcome IN ('declined', 'failed', 'timeout') AND failed_stage = 'authorization' AND authorized_at IS NULL AND captured_at IS NULL) OR (outcome IN ('failed', 'timeout') AND failed_stage = 'capture' AND authorized_at IS NOT NULL AND captured_at IS NULL)", name=op.f('ck_payment_attempts_stage_combination')),
    sa.CheckConstraint("actor_type IN ('customer', 'staff')", name=op.f('ck_payment_attempts_actor_type')),
    sa.CheckConstraint("failed_stage IN ('authorization', 'capture')", name=op.f('ck_payment_attempts_failed_stage')),
    sa.CheckConstraint("method_type IN ('saved_card', 'upi', 'gift_card', 'pos')", name=op.f('ck_payment_attempts_method_type')),
    sa.CheckConstraint("outcome IN ('success', 'declined', 'failed', 'timeout')", name=op.f('ck_payment_attempts_outcome')),
    sa.CheckConstraint("purpose IN ('checkout', 'collection')", name=op.f('ck_payment_attempts_purpose')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_payment_attempts_amount')),
    sa.CheckConstraint('attempt_number >= 1', name=op.f('ck_payment_attempts_attempt_number')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id'], name=op.f('fk_payment_attempts_customer_id_customers')),
    sa.ForeignKeyConstraint(['method_ref'], ['test_payment_methods.method_ref'], name=op.f('fk_payment_attempts_method_ref_test_payment_methods')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.order_id'], name=op.f('fk_payment_attempts_order_id_orders')),
    sa.PrimaryKeyConstraint('payment_id', name=op.f('pk_payment_attempts')),
    sa.UniqueConstraint('checkout_key', 'attempt_number', name=op.f('uq_payment_attempts_checkout_key_attempt_number'))
    )
    op.create_table('refunds',
    sa.Column('refund_id', sa.String(length=30), nullable=False),
    sa.Column('order_id', sa.String(length=30), nullable=True),
    sa.Column('payment_id', sa.String(length=30), nullable=True),
    sa.Column('source_type', sa.String(length=20), nullable=False),
    sa.Column('source_ref', sa.String(length=60), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('refund_status', sa.String(length=10), nullable=False),
    sa.Column('retry_of', sa.String(length=30), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("order_id IS NOT NULL OR source_type = 'stock_unavailable'", name=op.f('ck_refunds_order_or_stock_unavailable')),
    sa.CheckConstraint("refund_status IN ('initiated', 'completed', 'failed')", name=op.f('ck_refunds_refund_status')),
    sa.CheckConstraint("source_type IN ('cancellation', 'modification', 'return', 'stock_unavailable')", name=op.f('ck_refunds_source_type')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_refunds_amount')),
    sa.CheckConstraint('order_id IS NOT NULL OR payment_id IS NOT NULL', name=op.f('ck_refunds_order_or_payment')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.order_id'], name=op.f('fk_refunds_order_id_orders')),
    sa.ForeignKeyConstraint(['payment_id'], ['payment_attempts.payment_id'], name=op.f('fk_refunds_payment_id_payment_attempts')),
    sa.ForeignKeyConstraint(['retry_of'], ['refunds.refund_id'], name=op.f('fk_refunds_retry_of_refunds')),
    sa.PrimaryKeyConstraint('refund_id', name=op.f('pk_refunds')),
    sa.UniqueConstraint('retry_of', name=op.f('uq_refunds_retry_of'))
    )


def downgrade():
    op.drop_table('refunds')
    op.drop_table('payment_attempts')
    op.drop_table('test_payment_methods')
    op.drop_table('reservations')
    op.drop_table('purchase_history')
    op.drop_table('orders')
    op.drop_table('journeys')
    op.drop_index('ix_agent_runs_trace_id', table_name='agent_runs')
    op.drop_table('agent_runs')
    op.drop_index('ix_stock_holds_availability', table_name='stock_holds')
    op.drop_table('stock_holds')
    op.drop_table('customers')
