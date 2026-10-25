"""auto_create_per_scan

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('system_settings', sa.Column(
        'auto_create_per_scan', sa.Integer(), nullable=False, server_default='3'))
    op.create_check_constraint(
        op.f('ck_system_settings_auto_scan_range'), 'system_settings',
        'auto_create_per_scan BETWEEN 1 AND 10')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_system_settings_auto_scan_range'), 'system_settings')
    op.drop_column('system_settings', 'auto_create_per_scan')
