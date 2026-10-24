"""feedback_lessons

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'video_feedback',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('content_item_id', sa.Uuid(), sa.ForeignKey('content_items.id'), nullable=False),
        sa.Column('revision_id', sa.Uuid(), sa.ForeignKey('content_revisions.id')),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('labels', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('note', sa.String(500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('rating BETWEEN 1 AND 5', name=op.f('ck_video_feedback_rating_valid')),
        sa.UniqueConstraint('content_item_id', name=op.f('uq_video_feedback_content_item_id')),
    )
    op.create_table(
        'lessons',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('text', sa.String(300), nullable=False),
        sa.Column('lane', sa.String(10), nullable=False, server_default='all'),
        sa.Column('status', sa.String(10), nullable=False, server_default='proposed'),
        sa.Column('source', sa.String(10), nullable=False, server_default='auto'),
        sa.Column('reason', sa.String(300), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('proposed', 'approved', 'rejected')",
                           name=op.f('ck_lessons_status_valid')),
        sa.CheckConstraint("lane IN ('all', 'daily_life', 'rebus', 'news')",
                           name=op.f('ck_lessons_lane_valid')),
    )


def downgrade() -> None:
    op.drop_table('lessons')
    op.drop_table('video_feedback')
