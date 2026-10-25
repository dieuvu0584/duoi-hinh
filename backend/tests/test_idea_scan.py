"""Idea scan: feed parsing, schedule slots, shortlist rules, proposals, API."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.api.deps import get_llm, get_settings
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.urlsafety import is_safe_public_https_url
from app.db.models import (
    ContentItem,
    FeedItem,
    FeedSource,
    IdeaCandidate,
    Idiom,
    LlmCall,
    ProviderBudget,
    ScanRun,
)
from app.main import app
from app.providers.contracts import JsonResult
from app.services import content_pipeline, feed_service, idea_service
from app.services.seed_data import FEED_SOURCES, IDIOMS
from tests.conftest import OWNER_PASSWORD

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 23, 3, 30, tzinfo=UTC)  # 10:30 in Vietnam


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------------------ feeds
RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title>
<item><title>Chu meo ngu   quen</title>
<description><![CDATA[<a href="https://x.vn/a"><img src="https://x.vn/i.jpg"></a></br>Mot <b>ngay</b> thu Hai &amp; mua.]]></description>
<pubDate>Thu, 22 Oct 2026 08:00:00 +0700</pubDate>
<link>https://x.vn/a</link></item>
<item><title>Khong co link</title></item>
<item><title>Link khong an toan</title><link>http://x.vn/b</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom entry</title>
<link href="https://y.vn/e1"/><summary>Tom tat</summary>
<updated>2026-10-22T01:00:00Z</updated></entry></feed>"""


def test_rss_items_become_short_plain_text():
    items = feed_service.parse_feed(RSS)
    assert len(items) == 1  # the one without a link and the http one are skipped
    item = items[0]
    assert item.title == "Chu meo ngu quen"
    assert item.summary == "Mot ngay thu Hai & mua."  # no tags, entities decoded
    assert item.url == "https://x.vn/a" and item.published_at.tzinfo is not None


def test_atom_entries_are_read_too():
    items = feed_service.parse_feed(ATOM)
    assert [i.url for i in items] == ["https://y.vn/e1"] and items[0].summary == "Tom tat"


def test_entity_bombs_and_dtds_are_refused():
    bomb = b"""<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>
    <rss><channel><item><title>&b;</title><link>https://x.vn/a</link></item></channel></rss>"""
    with pytest.raises(feed_service.FeedError):
        feed_service.parse_feed(bomb)
    with pytest.raises(feed_service.FeedError):
        feed_service.parse_feed(b"not xml at all")


def test_text_is_cut_to_the_limit():
    assert len(feed_service.clean_text("x" * 5000, 300)) == 300
    assert feed_service.clean_text(None, 10) == ""


def test_only_public_https_feeds_are_fetched():
    for url in ("http://x.vn/rss", "https://localhost/rss", "https://10.0.0.5/rss",
                "https://user:pw@x.vn/rss"):
        with pytest.raises(feed_service.FeedError, match="unsafe_url"):
            run(feed_service.http_fetch(url))


def _mock(handler):
    import httpx

    return httpx.MockTransport(handler)


def test_a_same_site_redirect_is_followed_even_when_relative():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rss/tin.rss":
            return httpx.Response(301, headers={"location": "/home.rss"})
        return httpx.Response(200, content=RSS)

    data = run(feed_service.http_fetch("https://tuoitre.vn/rss/tin.rss", transport=_mock(handler)))
    assert data == RSS


def test_a_redirect_to_another_site_is_refused():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example.com/x"})

    with pytest.raises(feed_service.FeedError, match="redirect_refused"):
        run(feed_service.http_fetch("https://tuoitre.vn/r.rss", transport=_mock(handler)))


def test_errors_and_oversized_feeds_are_refused():
    import httpx

    with pytest.raises(feed_service.FeedError, match="http_500"):
        run(feed_service.http_fetch("https://x.vn/r", transport=_mock(
            lambda r: httpx.Response(500))))
    big = b"x" * (feed_service.MAX_BYTES + 10)
    with pytest.raises(feed_service.FeedError, match="too_large"):
        run(feed_service.http_fetch("https://x.vn/r", transport=_mock(
            lambda r: httpx.Response(200, content=big))))


def test_one_broken_feed_does_not_stop_the_others():
    async def fetch(url):
        if "bad" in url:
            raise feed_service.FeedError("http_500")
        return RSS

    out = run(feed_service.fetch_many(["https://ok.vn/r", "https://bad.vn/r"], fetch))
    assert len(out["https://ok.vn/r"]) == 1
    assert isinstance(out["https://bad.vn/r"], feed_service.FeedError)


def test_the_seed_data_is_sane():
    assert all(is_safe_public_https_url(url) for _, url, _ in FEED_SOURCES)
    assert len({u for _, u, _ in FEED_SOURCES}) == len(FEED_SOURCES)
    assert len({t for t, _ in IDIOMS}) == len(IDIOMS) >= 60
    assert all(t.strip() == t and 2 <= len(t) <= 120 for t, _ in IDIOMS)


# ------------------------------------------------------------------------------ schedule
TIMES = ["10:00", "16:00", "20:00", "00:00"]


def test_a_slot_is_due_shortly_after_its_time_in_vietnam_time():
    assert idea_service.due_slots(NOW, TIMES) == ["2026-10-23T10:00"]  # 10:30 VN
    far = datetime(2026, 10, 23, 7, 0, tzinfo=UTC)  # 14:00 VN: past the grace window
    assert idea_service.due_slots(far, TIMES) == []
    early = datetime(2026, 10, 23, 2, 59, tzinfo=UTC)  # 09:59 VN: not yet
    assert idea_service.due_slots(early, TIMES) == []


def test_the_midnight_slot_belongs_to_the_right_day():
    just_after = datetime(2026, 10, 22, 17, 20, tzinfo=UTC)  # 00:20 VN on the 23rd
    assert idea_service.due_slots(just_after, TIMES) == ["2026-10-23T00:00"]


def test_a_slot_is_queued_once_even_if_asked_twice(db):
    first = idea_service.queue_due_scans(db, TIMES, NOW)
    second = idea_service.queue_due_scans(db, TIMES, NOW)
    assert len(first) == 1 and second == []
    assert len(db.scalars(select(ScanRun)).all()) == 1


def test_scan_times_are_validated_and_sorted(client, owner, auth_headers):
    url = "/api/system/settings"

    def put(times):
        return client.put(url, json={"changes": {"scan_times": times}}, headers=auth_headers)

    ok = put(["20:00", "9:05", "10:00", "10:00"])
    assert ok.status_code == 200 and ok.json()["scan_times"] == ["09:05", "10:00", "20:00"]
    assert put(["25:00"]).status_code == 422
    assert put(["ten"]).status_code == 422
    assert put([f"0{h}:00" for h in range(9)]).status_code == 422
    on = client.put(url, json={"changes": {"scan_enabled": True}}, headers=auth_headers)
    assert on.json()["scan_enabled"] is True


# ------------------------------------------------------------------------------ the scan
def cand(ids, *, score=80, lane="daily_life", risk="low", cats=(), situation="Tinh huong chung"):
    return {"item_ids": ids, "situation": situation, "why": "de dong cam", "score": score,
            "suggested_lane": lane, "risk_level": risk, "sensitive_categories": list(cats)}


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def complete_json(self, *, system, content, schema, max_tokens=4000):
        self.calls.append(content)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return JsonResult(payload=answer, refused=False, input_tokens=2000, output_tokens=500,
                          model="m", request_id="r")


def feeds_xml(*titles, prefix="https://x.vn/"):
    items = "".join(
        f"<item><title>{t}</title><link>{prefix}{i}</link></item>" for i, t in enumerate(titles))
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'.encode()


@pytest.fixture()
def cfg():
    return Settings(_env_file=None, prompts_dir=str(REPO / "prompts"),
                    schemas_dir=str(REPO / "docs" / "schemas"), blacklist_words="cam tu")


@pytest.fixture()
def money(db):
    db.execute(text("UPDATE system_settings SET daily_budget_limit=5, monthly_budget_limit=5"))
    db.add(ProviderBudget(provider="anthropic", monthly_limit_usd=Decimal("5")))
    db.commit()


def add_source(db, name="Bao A", url="https://a.vn/rss"):
    src = FeedSource(name=name, url=url, kind="rss", enabled=True)
    db.add(src)
    db.commit()
    return src


def scan(db, llm, cfg, fetch, now=NOW):
    queued = ScanRun(trigger="manual", status="queued")
    db.add(queued)
    db.commit()
    return run(idea_service.run_scan(db, llm, cfg, queued.id, fetch=fetch, now=now))


def fixed(xml):
    async def fetch(_url):
        return xml

    return fetch


def test_a_scan_stores_headlines_and_shortlists_candidates(db, cfg, money):
    add_source(db)
    llm = FakeLLM({"candidates": [cand([1, 2], score=90), cand([3], score=70, lane="either")]})
    result = scan(db, llm, cfg, fixed(feeds_xml("Meo ngu", "Cho cung ngu", "Troi mua")))
    assert result.status == "done" and result.items_new == 3 and result.candidates == 2
    rows = db.scalars(select(IdeaCandidate).order_by(IdeaCandidate.score.desc())).all()
    assert [r.score for r in rows] == [90, 70] and rows[0].status == "new"
    assert [s["title"] for s in rows[0].source_items] == ["Meo ngu", "Cho cung ngu"]
    assert rows[0].expires_at > NOW + timedelta(hours=47)
    assert all(i.processed_at is not None for i in db.scalars(select(FeedItem)))
    sent = llm.calls[0][0]["text"]
    assert "Meo ngu" in sent and "Bao A" in sent  # headlines go in as data


def test_the_backend_drops_what_it_should_not_trust(db, cfg, money):
    add_source(db)
    answer = {"candidates": [
        cand([1], score=95, cats=["politics"]),  # sensitive category
        cand([2], score=95, risk="high"),  # risky
        cand([3], score=30),  # too weak
        cand([99], score=95),  # an id the model made up
        cand([4], score=95, situation="Chuyen cam tu hom nay"),  # the owner's blacklist
        cand([5], score=60),  # the only good one
    ]}
    result = scan(db, FakeLLM(answer), cfg, fixed(feeds_xml("a", "b", "c", "d", "e")))
    assert result.candidates == 1
    assert db.scalar(select(IdeaCandidate)).score == 60


def test_the_same_headlines_are_not_paid_for_twice(db, cfg, money):
    add_source(db)
    xml = feeds_xml("Meo ngu", "Cho ngu")
    llm = FakeLLM({"candidates": [cand([1], score=80)]})
    scan(db, llm, cfg, fixed(xml))
    again = scan(db, llm, cfg, fixed(xml))
    assert again.status == "done" and again.note == "no_new_items" and again.items_new == 0
    assert len(llm.calls) == 1  # nothing new, so no LLM call and no spend


def test_the_same_story_from_two_feeds_is_kept_once(db, cfg, money):
    add_source(db, "A", "https://a.vn/rss")
    add_source(db, "B", "https://b.vn/rss")

    async def fetch(url):
        return feeds_xml("Cung mot tin", prefix="https://" + url.split("/")[2] + "/")

    result = scan(db, FakeLLM({"candidates": []}), cfg, fetch)
    assert result.items_new == 1


def test_a_candidate_already_on_the_list_is_not_added_again(db, cfg, money):
    add_source(db)
    llm = FakeLLM({"candidates": [cand([1], score=80)]})
    scan(db, llm, cfg, fixed(feeds_xml("Meo ngu")))
    # the headline reappears under a new URL-less title tweak but the same link
    db.execute(text("DELETE FROM feed_items"))
    db.commit()
    scan(db, llm, cfg, fixed(feeds_xml("Meo ngu")))
    assert len(db.scalars(select(IdeaCandidate)).all()) == 1


def test_a_failing_feed_is_recorded_and_the_rest_still_work(db, cfg, money):
    add_source(db, "Tot", "https://ok.vn/rss")
    add_source(db, "Hong", "https://bad.vn/rss")

    async def fetch(url):
        if "bad" in url:
            raise feed_service.FeedError("http_500")
        return feeds_xml("Meo ngu")

    result = scan(db, FakeLLM({"candidates": [cand([1])]}), cfg, fetch)
    assert result.status == "done" and result.candidates == 1 and result.note == "feeds_failed:1"
    bad = db.scalar(select(FeedSource).where(FeedSource.name == "Hong"))
    assert bad.last_error == "http_500"


def test_no_budget_means_no_scan_and_the_headlines_wait(db, cfg):
    add_source(db)  # budgets stay at 0
    result = scan(db, FakeLLM({"candidates": []}), cfg, fixed(feeds_xml("Meo ngu")))
    assert result.status == "failed" and result.note.startswith("budget_")
    assert db.scalar(select(FeedItem)).processed_at is None  # retried by the next scan
    assert db.scalar(select(LlmCall)) is None


def test_old_candidates_expire_and_old_headlines_are_deleted(db, cfg, money):
    add_source(db)
    scan(db, FakeLLM({"candidates": [cand([1])]}), cfg, fixed(feeds_xml("Meo ngu")))
    idea_service.expire_and_purge(db, NOW + timedelta(hours=49))
    assert db.scalar(select(IdeaCandidate)).status == "expired"
    idea_service.expire_and_purge(db, NOW + timedelta(days=8))
    assert db.scalar(select(FeedItem)) is None


def test_only_one_manual_scan_can_wait_at_a_time(db, owner):
    idea_service.queue_manual_scan(db, owner)
    with pytest.raises(ApiError) as err:
        idea_service.queue_manual_scan(db, owner)
    assert err.value.code == "scan_in_progress"


# ------------------------------------------------------------------------------ proposals
def option(**over):
    base = {"title": "Meo ngu quen", "brief": "Mot chu meo nam ngu trong hop giay.",
            "answer": "", "idiom": "", "twist": "", "explanation": "hop xu huong",
            "syllables": [], "difficulty": "easy"}
    base.update(over)
    return base


def make_candidate(db, **over):
    fields = {"situation": "Ai cung buon ngu", "why": "w", "score": 80,
              "suggested_lane": "either", "risk_level": "low", "categories": [], "status": "new",
              "source_items": [{"title": "Meo ngu", "source": "A", "url": "https://x.vn/1",
                                "published_at": None}],
              "expires_at": NOW + timedelta(hours=48)}
    fields.update(over)
    c = IdeaCandidate(**fields)
    db.add(c)
    db.commit()
    return c


def test_daily_life_options_carry_no_answer(db, cfg, money):
    c = make_candidate(db)
    llm = FakeLLM({"options": [option(answer="khong dung", idiom="x", syllables=["a"])]})
    out = run(idea_service.propose(db, llm, cfg, c.id, "daily_life"))
    assert out.status == "proposed" and out.lane == "daily_life"
    p = out.proposals[0]
    assert p["answer"] == "" and p["idiom"] == "" and p["syllables"] == []


def test_rebus_options_use_the_idiom_bank_and_never_give_the_answer_away(db, cfg, money):
    db.add(Idiom(text="Đầu voi đuôi chuột", meaning="m", enabled=True))
    db.commit()
    c = make_candidate(db)
    good = option(title="Voi va chuot", brief="Mot cai dau voi to, ke ben cai duoi chuot be.",
                  answer="Đầu voi đuôi mèo", idiom="dau voi duoi chuot", twist="doi chuot thanh meo",
                  syllables=["x"], difficulty="medium")
    leaking = option(title="Lo dap an", brief="Hinh ve dau voi duoi meo ro rang",
                     answer="Đầu voi đuôi mèo", idiom="Đầu voi đuôi chuột")
    llm = FakeLLM({"options": [leaking, good]})
    out = run(idea_service.propose(db, llm, cfg, c.id, "rebus"))
    assert len(out.proposals) == 1  # the option that spells its own answer is dropped
    p = out.proposals[0]
    assert p["answer"] == "Đầu voi đuôi mèo" and p["idiom"] == "Đầu voi đuôi chuột"
    assert p["syllables"] == ["Đầu", "voi", "đuôi", "mèo"]  # recomputed, not trusted
    sent = llm.calls[0][0]["text"]
    assert "Đầu voi đuôi chuột" in sent and "idiom_bank" in sent


def test_rebus_needs_an_idiom_bank(db, cfg, money):
    c = make_candidate(db)
    with pytest.raises(ApiError) as err:
        run(idea_service.propose(db, FakeLLM({"options": [option()]}), cfg, c.id, "rebus"))
    assert err.value.code == "no_idioms"


def test_when_nothing_usable_comes_back_the_owner_is_told(db, cfg, money):
    c = make_candidate(db)
    llm = FakeLLM({"options": [option(brief="  ")]})
    with pytest.raises(ApiError) as err:
        run(idea_service.propose(db, llm, cfg, c.id, "daily_life"))
    assert err.value.code == "no_valid_options"


def test_creating_starts_the_pipeline_once(db, cfg, owner):
    c = make_candidate(db, status="proposed")
    item = idea_service.create_content(db, owner, c.id, lane="daily_life",
                                       brief="Meo ngu quen trong hop", answer=None)
    db.expire_all()
    assert db.get(ContentItem, item.id).status == "DRAFT"
    assert db.get(IdeaCandidate, c.id).status == "created"
    assert db.get(IdeaCandidate, c.id).content_item_id == item.id
    with pytest.raises(ApiError) as err:  # a second press must not create a second post
        idea_service.create_content(db, owner, c.id, lane="daily_life", brief="x", answer=None)
    assert err.value.code == "idea_closed"


def test_a_rebus_cannot_be_created_without_the_owners_answer(db, owner):
    c = make_candidate(db)
    with pytest.raises(ApiError) as err:
        idea_service.create_content(db, owner, c.id, lane="rebus", brief="clues", answer=" ")
    assert err.value.code == "answer_required"
    assert db.get(IdeaCandidate, c.id).status == "new"


# ------------------------------------------------------------------------------ API
def test_every_idea_route_needs_a_login(client, owner):
    fake = "00000000-0000-0000-0000-000000000000"
    for method, path in (("get", "/api/ideas/candidates"), ("get", f"/api/ideas/candidates/{fake}"),
                         ("post", "/api/ideas/scan"), ("get", "/api/ideas/sources"),
                         ("post", "/api/ideas/sources"), ("get", "/api/ideas/idioms"),
                         ("post", f"/api/ideas/candidates/{fake}/propose"),
                         ("post", f"/api/ideas/candidates/{fake}/create"),
                         ("post", f"/api/ideas/candidates/{fake}/dismiss")):
        assert getattr(client, method)(path).status_code == 401, path


def test_the_candidate_list_shows_sources_and_the_last_scan(client, owner, auth_headers, db):
    c = make_candidate(db)
    db.add(ScanRun(trigger="manual", status="done", candidates=1, items_new=5))
    db.commit()
    body = client.get("/api/ideas/candidates", headers=auth_headers).json()
    assert body["items"][0]["id"] == str(c.id)
    assert body["items"][0]["sources"][0]["title"] == "Meo ngu"
    assert body["last_scan"]["status"] == "done" and body["last_scan"]["items_new"] == 5
    assert client.get("/api/ideas/candidates?status=nope", headers=auth_headers).status_code == 422


def test_propose_edit_and_create_through_the_api(client, owner, auth_headers, db):
    c = make_candidate(db)
    llm = FakeLLM({"options": [option()]})
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, prompts_dir=str(REPO / "prompts"),
        schemas_dir=str(REPO / "docs" / "schemas"))
    try:
        db.execute(text("UPDATE system_settings SET daily_budget_limit=5, monthly_budget_limit=5"))
        db.add(ProviderBudget(provider="anthropic", monthly_limit_usd=Decimal("5")))
        db.commit()
        r = client.post(f"/api/ideas/candidates/{c.id}/propose", headers=auth_headers,
                        json={"lane": "daily_life"})
        assert r.status_code == 200 and r.json()["proposals"][0]["title"] == "Meo ngu quen"
        made = client.post(f"/api/ideas/candidates/{c.id}/create", headers=auth_headers,
                           json={"lane": "daily_life", "brief": "Ban da sua lai y tuong"})
        assert made.status_code == 201 and made.json()["status"] == "DRAFT"
        again = client.post(f"/api/ideas/candidates/{c.id}/create", headers=auth_headers,
                            json={"lane": "daily_life", "brief": "lan hai"})
        assert again.status_code == 409
    finally:
        app.dependency_overrides.pop(get_llm, None)
        app.dependency_overrides.pop(get_settings, None)


def test_dismissing_removes_it_from_the_list(client, owner, auth_headers, db):
    c = make_candidate(db)
    assert client.post(f"/api/ideas/candidates/{c.id}/dismiss", headers=auth_headers).status_code == 204
    assert client.get("/api/ideas/candidates", headers=auth_headers).json()["items"] == []


def test_scan_now_queues_one_scan(client, owner, auth_headers):
    first = client.post("/api/ideas/scan", headers=auth_headers)
    assert first.status_code == 202 and first.json()["status"] == "queued"
    assert client.post("/api/ideas/scan", headers=auth_headers).status_code == 409


def test_feed_sources_can_be_managed_but_only_if_public_https(client, owner, auth_headers):
    ok = client.post("/api/ideas/sources", headers=auth_headers,
                     json={"name": "Bao moi", "url": "https://news.google.com/rss?hl=vi"})
    assert ok.status_code == 201 and ok.json()["kind"] == "google_news"
    sid = ok.json()["id"]
    assert client.post("/api/ideas/sources", headers=auth_headers,
                       json={"name": "Trung", "url": "https://news.google.com/rss?hl=vi"}
                       ).status_code == 409
    for bad in ("http://x.vn/rss", "https://127.0.0.1/rss", "https://intranet/rss"):
        r = client.post("/api/ideas/sources", headers=auth_headers, json={"name": "x", "url": bad})
        assert r.status_code == 422, bad
    off = client.patch(f"/api/ideas/sources/{sid}", headers=auth_headers, json={"enabled": False})
    assert off.json()["enabled"] is False
    assert client.delete(f"/api/ideas/sources/{sid}", headers=auth_headers).status_code == 204
    assert client.get("/api/ideas/sources", headers=auth_headers).json()["items"] == []


def test_the_idiom_bank_can_be_edited(client, owner, auth_headers):
    ok = client.post("/api/ideas/idioms", headers=auth_headers,
                     json={"text": "  Nước   đổ đầu vịt ", "meaning": "nói mãi không nghe"})
    assert ok.status_code == 201 and ok.json()["text"] == "Nước đổ đầu vịt"
    assert client.post("/api/ideas/idioms", headers=auth_headers,
                       json={"text": "Nước đổ đầu vịt"}).status_code == 409
    assert len(client.get("/api/ideas/idioms", headers=auth_headers).json()["items"]) == 1
    assert client.delete(f"/api/ideas/idioms/{ok.json()['id']}",
                         headers=auth_headers).status_code == 204


def test_the_password_is_not_needed_for_scan_settings(client, owner, auth_headers):
    r = client.put("/api/system/settings", headers=auth_headers,
                   json={"changes": {"scan_enabled": True, "scan_times": ["10:00"]}})
    assert r.status_code == 200 and OWNER_PASSWORD  # no re-auth: it only schedules small spend


# ------------------------------------------------------------------ international sources
def test_the_international_sources_are_part_of_the_seed():
    names = " ".join(n for n, _u, _k in FEED_SOURCES)
    for expected in ("BBC", "Guardian", "Good News Network", "UPI", "NPR", "Google News (Anh)"):
        assert expected in names
    assert len(FEED_SOURCES) >= 20


def test_a_busy_source_cannot_crowd_the_others_out_of_the_batch(db):
    big = add_source(db, "Lon", "https://big.vn/rss")
    small = add_source(db, "Nho", "https://small.vn/rss")
    items = [FeedItem(source_id=big.id, url_hash=f"b{i}", title=f"b{i}", url=f"https://big.vn/{i}",
                      fetched_at=NOW) for i in range(30)]
    items += [FeedItem(source_id=small.id, url_hash=f"s{i}", title=f"s{i}",
                       url=f"https://small.vn/{i}", fetched_at=NOW) for i in range(3)]
    batch = idea_service.pick_batch(items, limit=10)
    assert len(batch) == 10
    assert {i.title for i in batch} >= {"s0", "s1", "s2"}  # the small feed is fully represented
    assert [i.title for i in batch][:2] == ["b0", "s0"]  # sources take turns


def test_the_batch_never_exceeds_the_limit_and_keeps_newest_first_within_a_source(db):
    src = add_source(db)
    items = [FeedItem(source_id=src.id, url_hash=f"x{i}", title=f"n{i}", url=f"https://a.vn/{i}",
                      fetched_at=NOW) for i in range(100)]
    batch = idea_service.pick_batch(items)
    assert len(batch) == idea_service.MAX_PENDING_ITEMS
    assert [i.title for i in batch][:3] == ["n0", "n1", "n2"]


# ------------------------------------------------------------------------------ auto creation
def set_auto(db, n):
    db.execute(text("UPDATE system_settings SET auto_create_per_day=:n"), {"n": n})
    db.commit()


def test_auto_create_is_off_by_default(db, cfg, money):
    make_candidate(db)
    llm = FakeLLM({"options": [option()]})
    assert run(idea_service.auto_create(db, llm, cfg, now=NOW)) is None
    assert llm.calls == []  # nothing is asked, nothing is spent


def test_the_best_low_risk_idea_becomes_a_video_up_to_n_a_day(db, cfg, money):
    set_auto(db, 2)
    low = make_candidate(db, score=70)
    best = make_candidate(db, score=90)
    make_candidate(db, score=99, risk_level="medium")  # never picked by itself
    llm = FakeLLM({"options": [option(brief="Meo ngu quen ben cua so")]})
    first = run(idea_service.auto_create(db, llm, cfg, now=NOW))
    assert first is not None and first.pipeline_input["auto_created"] is True
    assert first.pipeline_input["brief"] == "Meo ngu quen ben cua so"
    assert db.get(IdeaCandidate, best.id).status == "created"  # highest score first
    assert db.get(IdeaCandidate, low.id).status == "new"
    second = run(idea_service.auto_create(db, llm, cfg, now=NOW))
    assert second is not None and db.get(IdeaCandidate, low.id).status == "created"
    third = run(idea_service.auto_create(db, llm, cfg, now=NOW))
    assert third is None  # N = 2 reached for the day


def test_auto_create_waits_while_too_many_videos_wait_for_the_owner(db, cfg, money, owner):
    set_auto(db, 5)
    make_candidate(db)
    for _ in range(idea_service.MAX_WAITING_FOR_OWNER):
        item = content_pipeline.start(db, owner, lane="daily_life", brief="x")
        item.status = "PENDING_APPROVAL"
    db.commit()
    llm = FakeLLM({"options": [option()]})
    assert run(idea_service.auto_create(db, llm, cfg, now=NOW)) is None
    assert llm.calls == []


def test_a_paused_system_makes_nothing(db, cfg, money):
    set_auto(db, 2)
    db.execute(text("UPDATE system_settings SET generation_paused=true"))
    db.commit()
    make_candidate(db)
    llm = FakeLLM({"options": [option()]})
    assert run(idea_service.auto_create(db, llm, cfg, now=NOW)) is None


def test_an_idea_that_fails_is_set_aside_not_retried_forever(db, cfg, money):
    set_auto(db, 2)
    c = make_candidate(db)
    llm = FakeLLM({"options": []})  # no usable option comes back
    assert run(idea_service.auto_create(db, llm, cfg, now=NOW)) is None
    assert db.get(IdeaCandidate, c.id).status == "dismissed"


def test_no_more_than_the_per_scan_limit_from_one_scan_become_videos(db, cfg, money):
    set_auto(db, 10)
    db.execute(text("UPDATE system_settings SET auto_create_per_scan=2"))
    scan_a = ScanRun(trigger="schedule", status="done", candidates=3, items_new=3)
    scan_b = ScanRun(trigger="schedule", status="done", candidates=1, items_new=1)
    db.add_all([scan_a, scan_b])
    db.commit()
    for score in (90, 80, 70):
        make_candidate(db, score=score, scan_run_id=scan_a.id)
    other = make_candidate(db, score=60, scan_run_id=scan_b.id)
    db.execute(text("UPDATE system_settings SET auto_create_min_score=50"))
    db.commit()
    llm = FakeLLM({"options": [option()]})
    made = [run(idea_service.auto_create(db, llm, cfg, now=NOW)) for _ in range(4)]
    assert [m is not None for m in made] == [True, True, True, False]
    # two from scan A (limit 2), then the next best idea from another scan
    assert db.get(IdeaCandidate, other.id).status == "created"
    left = db.scalars(select(IdeaCandidate).where(IdeaCandidate.status == "new")).all()
    assert [c.score for c in left] == [70]


def test_ideas_below_the_minimum_score_are_never_made_automatically(db, cfg, money):
    set_auto(db, 3)
    weak = make_candidate(db, score=69)  # default minimum is 70
    llm = FakeLLM({"options": [option()]})
    assert run(idea_service.auto_create(db, llm, cfg, now=NOW)) is None
    assert llm.calls == [] and db.get(IdeaCandidate, weak.id).status == "new"
    db.execute(text("UPDATE system_settings SET auto_create_min_score=60"))
    db.commit()
    assert run(idea_service.auto_create(db, llm, cfg, now=NOW)) is not None


def test_the_owner_can_star_an_idea_and_the_stars_feed_the_summary(
        client, owner, auth_headers, db):
    c = make_candidate(db, score=80)
    url = f"/api/ideas/candidates/{c.id}/rating"
    assert client.post(url, json={"rating": 4}).status_code == 401
    assert client.post(url, headers=auth_headers, json={"rating": 6}).status_code == 422
    r = client.post(url, headers=auth_headers, json={"rating": 4})
    assert r.status_code == 200 and r.json()["owner_rating"] == 4
    listed = client.get("/api/ideas/candidates", headers=auth_headers).json()["items"]
    assert listed[0]["owner_rating"] == 4
    ideas = client.get("/api/feedback/summary", headers=auth_headers).json()["ideas"]
    assert ideas == {"count": 1, "average_rating": 4.0,
                     "average_claude_score_by_rating": {"4": 80.0}}
    assert client.post(f"/api/ideas/candidates/{'0' * 8}-0000-0000-0000-{'0' * 12}/rating",
                       headers=auth_headers, json={"rating": 3}).status_code == 404


def test_approved_lessons_reach_the_idea_prompts(db, cfg, money):
    from app.db.models import Lesson
    db.add(Lesson(text="Uu tien tinh huong cong so", lane="all", status="approved"))
    db.commit()
    c = make_candidate(db)
    llm = FakeLLM({"options": [option()]})
    run(idea_service.propose(db, llm, cfg, c.id, "daily_life"))
    sent = json.loads(llm.calls[0][0]["text"])
    assert sent["channel_lessons"] == ["Uu tien tinh huong cong so"]


def test_the_scan_learns_from_the_owners_stars_once_there_are_enough(db, cfg, money):
    add_source(db)
    for i, (stars, score) in enumerate([(5, 60), (5, 55), (1, 90), (2, 85), (3, 70)]):
        make_candidate(db, situation=f"Tinh huong {i}", score=score, owner_rating=stars,
                       status="dismissed",
                       source_items=[{"title": f"t{i}", "source": "A",
                                      "url": f"https://x.vn/old{i}", "published_at": None}])
    llm = FakeLLM({"candidates": [cand([1], score=80)]})
    scan(db, llm, cfg, fixed(feeds_xml("a", "b")))
    sent = json.loads(llm.calls[0][0]["text"])
    ex = sent["owner_rated_examples"]
    assert ex["rated_count"] == 5
    assert ex["average_your_score_by_owner_stars"]["5"] == 57.5  # Claude under-scored the loved ones
    assert ex["average_your_score_by_owner_stars"]["1"] == 90.0  # and over-scored the disliked
    assert {e["owner_stars"] for e in ex["liked"]} == {5}
    assert {e["owner_stars"] for e in ex["disliked"]} == {1, 2}
    assert "headlines" in sent


def test_with_too_few_ratings_the_scan_sends_the_headlines_unchanged(db, cfg, money):
    add_source(db)
    make_candidate(db, owner_rating=5, status="dismissed")
    llm = FakeLLM({"candidates": [cand([1], score=80)]})
    scan(db, llm, cfg, fixed(feeds_xml("a", "b")))
    sent = json.loads(llm.calls[0][0]["text"])
    assert isinstance(sent, list)  # no calibration object yet


def test_an_idea_under_three_stars_is_hidden_by_default_not_deleted(
        client, owner, auth_headers, db):
    low = make_candidate(db, score=80)
    fine = make_candidate(db, score=70)
    fresh = make_candidate(db, score=60)
    for cand, stars in ((low, 2), (fine, 4)):
        r = client.post(f"/api/ideas/candidates/{cand.id}/rating", headers=auth_headers,
                        json={"rating": stars})
        assert r.status_code == 200

    def ids(rating=None):
        url = "/api/ideas/candidates" + (f"?rating={rating}" if rating else "")
        return [i["id"] for i in client.get(url, headers=auth_headers).json()["items"]]

    assert ids() == [str(fine.id), str(fresh.id)]  # the 2-star idea is hidden by default
    # an idea without stars shows in every filter; the filters only narrow the rated ones
    assert ids("hidden") == [str(low.id), str(fresh.id)]  # ...the hidden one comes back
    assert set(ids("all")) == {str(low.id), str(fine.id), str(fresh.id)}
    assert ids("4plus") == [str(fine.id), str(fresh.id)]
    assert ids("5") == [str(fresh.id)]
    assert client.get("/api/ideas/candidates?rating=nope",
                      headers=auth_headers).status_code == 422
    db.expire_all()
    assert db.get(IdeaCandidate, low.id).status == "new"  # nothing was closed or deleted


def test_a_hidden_idea_is_never_made_into_a_video_automatically(db, cfg, money):
    set_auto(db, 3)
    make_candidate(db, score=95, owner_rating=2)
    llm = FakeLLM({"options": [option()]})
    assert run(idea_service.auto_create(db, llm, cfg, now=NOW)) is None
    assert llm.calls == []


def test_the_same_story_is_not_scanned_again_after_a_low_rating(db, cfg, money):
    add_source(db)
    make_candidate(db, owner_rating=1, status="dismissed",
                   source_items=[{"title": "Mèo ngủ gật", "source": "A",
                                  "url": "https://x.vn/dis", "published_at": None}])
    fresh_xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<item><title>Mèo ngủ gật</title><link>https://other.vn/same</link></item>"
        "<item><title>Chuyen khac hoan toan</title><link>https://x.vn/new</link></item>"
        "<item><title>Mot chuyen moi nua</title><link>https://x.vn/dis</link></item>"
        "</channel></rss>").encode()
    llm = FakeLLM({"candidates": []})
    scan(db, llm, cfg, fixed(fresh_xml))
    titles = [h["title"] for h in json.loads(llm.calls[0][0]["text"])]
    # the disliked URL and the same story from another outlet never reach the model again
    assert titles == ["Chuyen khac hoan toan"]
