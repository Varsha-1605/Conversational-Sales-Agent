"""sessions: the record behind the session cookie (DATABASE_ISSUES.md DB-42)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sessions',
        sa.Column('session_id', sa.String(length=40), nullable=False),
        sa.Column('subject_id', sa.String(length=30), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('store_id', sa.String(length=20), nullable=True),
        sa.Column('channel', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('customer', 'store_associate', 'administrator')", name=op.f('ck_sessions_role')),
        sa.CheckConstraint("channel IN ('web', 'app', 'whatsapp', 'kiosk', 'voice')", name=op.f('ck_sessions_channel')),
        sa.PrimaryKeyConstraint('session_id', name=op.f('pk_sessions')),
    )
    op.create_index('ix_sessions_last_activity_at', 'sessions', ['last_activity_at'], unique=False,
                    postgresql_where=sa.text('ended_at IS NULL'))


def downgrade():
    op.drop_index('ix_sessions_last_activity_at', table_name='sessions', postgresql_where=sa.text('ended_at IS NULL'))
    op.drop_table('sessions')
