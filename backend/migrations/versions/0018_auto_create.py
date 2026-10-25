"""auto_create

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('system_settings', sa.Column(
        'auto_create_per_day', sa.Integer(), nullable=False, server_default='0'))
    op.create_check_constraint(
        op.f('ck_system_settings_auto_create_range'), 'system_settings',
        'auto_create_per_day BETWEEN 0 AND 10')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_system_settings_auto_create_range'), 'system_settings')
    op.drop_column('system_settings', 'auto_create_per_day')
