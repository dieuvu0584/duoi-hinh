"""international_sources

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None

SOURCES = [
    ('BBC - Tin chính', 'https://feeds.bbci.co.uk/news/rss.xml', 'rss'),
    ('BBC - Khoa học và môi trường', 'https://feeds.bbci.co.uk/news/science_and_environment/rss.xml', 'rss'),
    ('BBC - Giải trí và nghệ thuật', 'https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml', 'rss'),
    ('BBC - Công nghệ', 'https://feeds.bbci.co.uk/news/technology/rss.xml', 'rss'),
    ('The Guardian - Đời sống', 'https://www.theguardian.com/lifeandstyle/rss', 'rss'),
    ('The Guardian - Công nghệ', 'https://www.theguardian.com/technology/rss', 'rss'),
    ('NPR - Tin chính', 'https://feeds.npr.org/1001/rss.xml', 'rss'),
    ('Good News Network - Tin vui', 'https://www.goodnewsnetwork.org/feed/', 'rss'),
    ('UPI - Chuyện lạ', 'https://rss.upi.com/news/odd_news.rss', 'rss'),
    ('Google News (Anh) - Tin chính', 'https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en', 'google_news'),
    ('Google News (Anh) - Chuyện vui lan truyền', 'https://news.google.com/rss/search?q=funny+viral&hl=en-US&gl=US&ceid=US:en', 'google_news'),
    ('Straits Times - Thế giới', 'https://www.straitstimes.com/news/world/rss.xml', 'rss'),
]


def upgrade() -> None:
    bind = op.get_bind()
    for name, url, kind in SOURCES:
        bind.execute(sa.text(
            "INSERT INTO feed_sources (id, name, url, kind, enabled, created_at) "
            "VALUES (gen_random_uuid(), :n, :u, :k, true, now()) ON CONFLICT DO NOTHING"),
            {"n": name, "u": url, "k": kind})


def downgrade() -> None:
    bind = op.get_bind()
    for _name, url, _kind in SOURCES:
        bind.execute(sa.text("DELETE FROM feed_sources WHERE url = :u"), {"u": url})
