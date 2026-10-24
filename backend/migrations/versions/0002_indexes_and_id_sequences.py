"""indexes, the two partial unique indexes and the ID sequences (DATABASE_ISSUES.md DB-38, DB-27, DB-36)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24 18:29:01.881175
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

# one sequence per generated ID (backend/domain/ids.py); customers continue after the 12 seeded ones.
# Alembic does not compare sequences, so they are written here by hand and must match schema.ID_SEQUENCES.
SEQUENCES = {
    'customer': 13, 'order': 1, 'checkout': 1, 'collection': 1, 'payment': 1, 'refund': 1,
    'hold': 1, 'reservation': 1, 'change': 1, 'return': 1, 'run': 1,
}


def upgrade():
    # ordinary indexes, each serving a known query (DATABASE.md section 6)
    op.create_index('ix_orders_customer_id_confirmed_at', 'orders', ['customer_id', 'confirmed_at'], unique=False)
    op.create_index('ix_orders_fulfillment_location_id_order_status', 'orders', ['fulfillment_location_id', 'order_status'], unique=False)
    op.create_index('ix_payment_attempts_order_id', 'payment_attempts', ['order_id'], unique=False)
    op.create_index('ix_refunds_order_id', 'refunds', ['order_id'], unique=False)
    op.create_index('ix_reservations_customer_id', 'reservations', ['customer_id'], unique=False)
    op.create_index('ix_reservations_location_id_reservation_status', 'reservations', ['location_id', 'reservation_status'], unique=False)
    op.create_index('ix_stock_holds_reference', 'stock_holds', ['reference_type', 'reference_id'], unique=False)

    # partial unique indexes: one successful payment per payment operation; one refund chain root per cause
    op.create_index('uq_payment_attempts_one_success', 'payment_attempts', ['checkout_key'], unique=True,
                    postgresql_where=sa.text("outcome = 'success'"))
    op.create_index('uq_refunds_chain_root', 'refunds', ['order_id', 'source_type', 'source_ref'], unique=True,
                    postgresql_where=sa.text('retry_of IS NULL'), postgresql_nulls_not_distinct=True)

    for kind, start in SEQUENCES.items():
        op.execute(sa.schema.CreateSequence(sa.Sequence(f'{kind}_id_seq', start=start)))


def downgrade():
    for kind in SEQUENCES:
        op.execute(sa.schema.DropSequence(sa.Sequence(f'{kind}_id_seq')))
    op.drop_index('uq_refunds_chain_root', table_name='refunds')
    op.drop_index('uq_payment_attempts_one_success', table_name='payment_attempts')
    op.drop_index('ix_stock_holds_reference', table_name='stock_holds')
    op.drop_index('ix_reservations_location_id_reservation_status', table_name='reservations')
    op.drop_index('ix_reservations_customer_id', table_name='reservations')
    op.drop_index('ix_refunds_order_id', table_name='refunds')
    op.drop_index('ix_payment_attempts_order_id', table_name='payment_attempts')
    op.drop_index('ix_orders_fulfillment_location_id_order_status', table_name='orders')
    op.drop_index('ix_orders_customer_id_confirmed_at', table_name='orders')
