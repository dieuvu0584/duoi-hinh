"""publishing_targets_and_voice

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('system_settings', sa.Column('facebook_page_id', sa.String(length=40), nullable=True))
    op.add_column('system_settings', sa.Column('youtube_channel_id', sa.String(length=40), nullable=True))
    op.add_column('system_settings', sa.Column('elevenlabs_voice_id', sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column('system_settings', 'elevenlabs_voice_id')
    op.drop_column('system_settings', 'youtube_channel_id')
    op.drop_column('system_settings', 'facebook_page_id')
