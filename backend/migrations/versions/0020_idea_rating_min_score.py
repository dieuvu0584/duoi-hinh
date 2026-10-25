"""idea_rating_min_score

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('system_settings', sa.Column(
        'auto_create_min_score', sa.Integer(), nullable=False, server_default='70'))
    op.create_check_constraint(
        op.f('ck_system_settings_auto_score_range'), 'system_settings',
        'auto_create_min_score BETWEEN 0 AND 100')
    op.add_column('idea_candidates', sa.Column('owner_rating', sa.Integer()))
    op.create_check_constraint(
        op.f('ck_idea_candidates_owner_rating_valid'), 'idea_candidates',
        'owner_rating IS NULL OR owner_rating BETWEEN 1 AND 5')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_idea_candidates_owner_rating_valid'), 'idea_candidates')
    op.drop_column('idea_candidates', 'owner_rating')
    op.drop_constraint(op.f('ck_system_settings_auto_score_range'), 'system_settings')
    op.drop_column('system_settings', 'auto_create_min_score')
