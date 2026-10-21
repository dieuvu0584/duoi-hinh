"""Package 0 experiments that PUBLISH publicly to the TEST Page, then delete what they create.

Answers the remaining VERIFY items in docs/publishing-meta.md:
  E1  FINISH(PUBLISHED): does it go public, how long until ready/published?
  E2  Is FINISH idempotent for the same video_id (second FINISH)?
  E3  Can a published Reel be deleted through the API?
  E4  Can a DRAFT be promoted to public (POST published=true, or a second FINISH)?

Guards: needs --live and --confirm-page-id == META_PAGE_ID. Every video created is deleted in a
`finally` block. Use only with a TEST Page. Never prints the token.

    python spikes/meta_publish_experiments.py --video spikes/output/reel.mp4 --live \
        --confirm-page-id <PAGE_ID>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import meta_reels_spike as base


class Lab:
    def __init__(self, graph: str, page_id: str, token: str, video: Path) -> None:
        self.graph, self.page_id, self.token, self.video = graph, page_id, token, video
        self.secrets = [token]
        self.client = httpx.Client(timeout=120)
        self.created: list[str] = []
        self.log: list[dict[str, Any]] = []
        self.t0 = time.monotonic()

    def note(self, event: str, **data: Any) -> None:
        entry = {"t": round(time.monotonic() - self.t0, 1), "event": event, **data}
        self.log.append(entry)
        print(f"  +{entry['t']:>6}s {event} {json.dumps(data, ensure_ascii=False)[:230]}",
              file=sys.stderr, flush=True)

    def _redact(self, text: str) -> str:
        return base.redact(text, self.secrets)

    def post(self, path: str, data: dict[str, str]) -> tuple[int, Any]:
        r = self.client.post(f"{self.graph}/{path}", data={**data, "access_token": self.token})
        return r.status_code, self._json(r)

    def get(self, path: str, fields: str) -> tuple[int, Any]:
        r = self.client.get(f"{self.graph}/{path}",
                            params={"fields": fields, "access_token": self.token})
        return r.status_code, self._json(r)

    def delete(self, vid: str) -> tuple[int, Any]:
        r = self.client.delete(f"{self.graph}/{vid}", params={"access_token": self.token})
        return r.status_code, self._json(r)

    def _json(self, r: httpx.Response) -> Any:
        try:
            return json.loads(self._redact(r.text))
        except ValueError:
            return {"raw": self._redact(r.text[:300])}

    # -- building blocks -------------------------------------------------------------------
    def prepare(self, label: str) -> str:
        code, body = self.post(f"{self.page_id}/video_reels", {"upload_phase": "start"})
        if code != 200:
            raise RuntimeError(f"START failed {code} {body}")
        vid, url = str(body["video_id"]), str(body["upload_url"])
        self.created.append(vid)
        payload = self.video.read_bytes()
        r = self.client.post(url, headers={"Authorization": f"OAuth {self.token}", "offset": "0",
                                           "file_size": str(len(payload)),
                                           "Content-Type": "application/octet-stream"},
                             content=payload, timeout=600)
        self.note(f"{label}: uploaded", video_id=vid, http=r.status_code)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:  # copyright check completes before FINISH
            _, cc = self.get(vid, "copyright_check_information")
            st = (cc.get("copyright_check_information", {}) or {}).get("status", {})
            state = st.get("status") if isinstance(st, dict) else st
            if state == "complete":
                break
            time.sleep(5)
        self.note(f"{label}: copyright", state=state)
        return vid

    def finish(self, vid: str, state: str, label: str) -> tuple[int, Any]:
        code, body = self.post(
            f"{self.page_id}/video_reels",
            {"upload_phase": "finish", "video_id": vid, "video_state": state,
             "description": f"Package 0 test {label} #test", "is_ai_generated": "true"},
        )
        self.note(f"{label}: FINISH {state}", http=code, body=body)
        return code, body

    def status(self, vid: str) -> dict[str, Any]:
        _, body = self.get(vid, "status")
        return body.get("status", body) if isinstance(body, dict) else {}

    def wait_status(self, vid: str, label: str, timeout: float = 150) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        st: dict[str, Any] = {}
        while time.monotonic() < deadline:
            st = self.status(vid)
            ph = (st.get("publishing_phase") or {}) if isinstance(st, dict) else {}
            self.note(f"{label}: status", video_status=st.get("video_status"),
                      publish_status=ph.get("publish_status"))
            if st.get("video_status") in {"ready", "error", "expired"}:
                return st
            time.sleep(4)
        return st

    def published_flag(self, vid: str) -> Any:
        code, body = self.get(vid, "id,published,permalink_url,length")
        return code, body

    def page_videos(self) -> Any:
        return self.get(f"{self.page_id}/videos", "id,published,created_time,description")

    def cleanup(self) -> None:
        for vid in self.created:
            code, body = self.delete(vid)
            self.note("cleanup: DELETE", video_id=vid, http=code, body=body)


def experiments(lab: Lab) -> dict[str, Any]:
    results: dict[str, Any] = {}

    # E1 + E2 + E3: publish A, FINISH twice, list page videos, delete published A.
    a = lab.prepare("A")
    t_finish = time.monotonic()
    code, body = lab.finish(a, "PUBLISHED", "A")
    st = lab.wait_status(a, "A")
    results["E1_publish"] = {
        "finish_http": code, "finish_body": body,
        "seconds_finish_to_ready": round(time.monotonic() - t_finish, 1),
        "status": st, "video_object": lab.published_flag(a),
    }
    code2, body2 = lab.finish(a, "PUBLISHED", "A-again")
    time.sleep(8)
    listing = lab.page_videos()
    mine = [v for v in (listing[1].get("data", []) if isinstance(listing[1], dict) else [])
            if "Package 0 test" in (v.get("description") or "")]
    results["E2_second_finish"] = {"http": code2, "body": body2,
                                   "package0_videos_on_page": mine, "count": len(mine),
                                   "status_after": lab.status(a)}
    code3, body3 = lab.delete(a)
    lab.created.remove(a)
    gone = lab.published_flag(a)
    lab.note("E3: DELETE published", http=code3, body=body3, after=gone)
    results["E3_delete_published"] = {"http": code3, "body": body3, "get_after": gone}

    # E4: promote a draft.
    b = lab.prepare("B")
    lab.finish(b, "DRAFT", "B")
    st_b = lab.wait_status(b, "B")
    pc, pb = lab.post(b, {"published": "true"})
    lab.note("E4: POST published=true", http=pc, body=pb)
    time.sleep(6)
    after_flag = lab.published_flag(b)
    promoted_by_field = isinstance(after_flag[1], dict) and after_flag[1].get("published") is True
    second = None
    if not promoted_by_field:
        second = lab.finish(b, "PUBLISHED", "B-promote")
        time.sleep(10)
        after_flag = lab.published_flag(b)
    results["E4_promote_draft"] = {
        "draft_status": st_b, "post_published_true": {"http": pc, "body": pb},
        "promoted_by_field": promoted_by_field,
        "second_finish": {"http": second[0], "body": second[1]} if second else None,
        "video_after": after_flag,
        "is_public_now": isinstance(after_flag[1], dict) and after_flag[1].get("published") is True,
    }
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True)
    p.add_argument("--env", default=".env")
    p.add_argument("--live", action="store_true")
    p.add_argument("--confirm-page-id", default="")
    p.add_argument("--out", default="spikes/output/meta_experiments.json")
    args = p.parse_args(argv)
    env = base.load_env(args.env)
    page_id, token = env.get("META_PAGE_ID", ""), env.get("META_ACCESS_TOKEN", "")
    if not args.live:
        print("dry run: would run E1-E4 against the test Page; pass --live and --confirm-page-id")
        return 0
    if not page_id or not token or args.confirm_page_id != page_id:
        print("ERROR: need META_PAGE_ID/META_ACCESS_TOKEN and --confirm-page-id == META_PAGE_ID",
              file=sys.stderr)
        return 1
    version = env.get("META_API_VERSION") or base.DEFAULT_API_VERSION
    lab = Lab(f"{base.DEFAULT_BASE}/{version}", page_id, token, Path(args.video))
    results: dict[str, Any] = {}
    error = None
    try:
        results = experiments(lab)
    except Exception as exc:  # report, then always clean up
        error = base.redact(f"{type(exc).__name__}: {exc}", lab.secrets)
        print(f"ERROR: {error}", file=sys.stderr)
    finally:
        lab.cleanup()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"at": datetime.now(UTC).isoformat(), "error": error,
                               "results": results, "log": lab.log},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False)[:6000])
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
