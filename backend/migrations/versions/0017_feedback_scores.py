"""feedback_scores

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('video_feedback', sa.Column(
        'scores', postgresql.JSONB(), nullable=False, server_default='{}'))


def downgrade() -> None:
    op.drop_column('video_feedback', 'scores')
