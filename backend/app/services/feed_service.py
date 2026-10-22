"""Reading RSS/Atom feeds safely.

Everything in a feed is untrusted: the XML is parsed with defusedxml (no entity or DTD tricks),
the download is capped in size and time, only public https URLs are fetched, redirects are not
followed to other hosts, and titles/summaries are reduced to short plain text.
"""

import asyncio
import hashlib
import html
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import defusedxml.ElementTree as SafeET
import httpx

from app.core.urlsafety import is_safe_public_https_url

MAX_BYTES = 3 * 1024 * 1024
TIMEOUT_S = 15.0
USER_AGENT = "Mozilla/5.0 (compatible; NHDBot/0.1; owner-run idea scan)"
MAX_ITEMS_PER_FEED = 80
Fetcher = Callable[[str], Awaitable[bytes]]

_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")


class FeedError(Exception):
    """A feed could not be read; the message is short and safe to store/show."""


@dataclass(frozen=True)
class RawItem:
    title: str
    summary: str
    url: str
    published_at: datetime | None


def clean_text(text: str | None, limit: int) -> str:
    """Plain text only: drop tags, decode entities, collapse whitespace, cut to `limit`."""
    if not text:
        return ""
    plain = html.unescape(_TAG.sub(" ", html.unescape(text)))
    plain = _SPACE.sub(" ", plain).strip()
    return plain[:limit]


def url_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(node, name: str) -> str | None:
    for child in node:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return None


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_feed(data: bytes) -> list[RawItem]:
    """RSS 2.0 `<item>` and Atom `<entry>` into plain items. Items without a usable https link
    or title are skipped."""
    try:
        root = SafeET.fromstring(data, forbid_dtd=True)
    except Exception as exc:  # parse errors, forbidden DTD/entities
        raise FeedError("unreadable_feed") from exc
    items: list[RawItem] = []
    for node in root.iter():
        kind = _local(node.tag)
        if kind not in {"item", "entry"}:
            continue
        title = clean_text(_child_text(node, "title"), 300)
        link = _child_text(node, "link") or ""
        if not link:  # Atom: <link href="..."/>
            for child in node:
                if _local(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        link = link.strip()
        if not title or not link.startswith("https://") or len(link) > 500:
            continue
        summary = clean_text(
            _child_text(node, "description") or _child_text(node, "summary")
            or _child_text(node, "content"), 600)
        published = _parse_date(_child_text(node, "pubDate") or _child_text(node, "published")
                                or _child_text(node, "updated"))
        items.append(RawItem(title, summary, link, published))
        if len(items) >= MAX_ITEMS_PER_FEED:
            break
    return items


def _site(host: str | None) -> str:
    """Host without a leading www., so tuoitre.vn <-> www.tuoitre.vn is the same site."""
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


async def http_fetch(url: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    """Download one feed: public https only, size/time capped, no cross-host redirects."""
    if not is_safe_public_https_url(url):
        raise FeedError("unsafe_url")
    host = urlparse(url).hostname
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False, transport=transport,
                                 headers={"User-Agent": USER_AGENT}) as client:
        target = url
        for _ in range(3):
            try:
                async with client.stream("GET", target) as resp:
                    if resp.status_code in {301, 302, 307, 308}:
                        location = urljoin(target, resp.headers.get("location", ""))
                        if _site(urlparse(location).hostname) != _site(host) \
                                or not is_safe_public_https_url(location):
                            raise FeedError("redirect_refused")
                        target = location
                        continue
                    if resp.status_code != 200:
                        raise FeedError(f"http_{resp.status_code}")
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise FeedError("too_large")
                    return bytes(body)
            except httpx.HTTPError as exc:
                raise FeedError("network") from exc
        raise FeedError("too_many_redirects")


async def fetch_many(
    urls: list[str], fetch: Fetcher = http_fetch, *, concurrency: int = 4
) -> dict[str, list[RawItem] | FeedError]:
    """Fetch and parse every URL; one bad feed never stops the others."""
    sem = asyncio.Semaphore(concurrency)

    async def one(url: str) -> tuple[str, list[RawItem] | FeedError]:
        async with sem:
            try:
                return url, parse_feed(await fetch(url))
            except FeedError as exc:
                return url, exc

    return dict(await asyncio.gather(*(one(u) for u in urls)))
