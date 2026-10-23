"""youtube_platform

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('publishing_records', sa.Column(
        'platform', sa.String(length=10), nullable=False, server_default='facebook'))
    op.create_check_constraint(
        op.f('ck_publishing_records_platform_valid'), 'publishing_records',
        "platform IN ('facebook', 'youtube')")
    op.drop_index('uq_publishing_records_one_active', table_name='publishing_records')
    op.create_index(
        'uq_publishing_records_one_active', 'publishing_records', ['revision_id', 'platform'],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('FAILED', 'CANCELLED', 'SKIPPED_LATE', 'RETRACTED')"))
    op.add_column('system_settings', sa.Column(
        'youtube_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')))


def downgrade() -> None:
    op.drop_column('system_settings', 'youtube_enabled')
    op.drop_index('uq_publishing_records_one_active', table_name='publishing_records')
    op.create_index(
        'uq_publishing_records_one_active', 'publishing_records', ['revision_id'], unique=True,
        postgresql_where=sa.text("status NOT IN ('FAILED', 'CANCELLED', 'SKIPPED_LATE', 'RETRACTED')"))
    op.drop_constraint(op.f('ck_publishing_records_platform_valid'), 'publishing_records',
                       type_='check')
    op.drop_column('publishing_records', 'platform')
