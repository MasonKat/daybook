"""F15.3: Web Push from the console (strictly opt-in; the Worker holds the subscriptions and sends).

Texts are generic (no names, no scores); tapping opens the subscriber's own page. The Worker skips quiet hours
(22:00-08:00 KST) and caps every subscription at one push a day; every send or skip is logged in events here.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from . import lint, ops
from .config import Config, strings
from .db import log_event

KINDS = ("video", "message", "report", "test")


class PushError(ValueError):
    pass


def enabled(cfg: Config) -> bool:
    return bool(cfg.worker and cfg.teacher_token and cfg.extra.get("vapid_public"))


def default_text(kind: str, n: int = 0) -> str:
    S = strings()
    return S[f"push_{kind}"].format(n=n)


def _class_keys(conn: sqlite3.Connection, names) -> list[str]:
    return [ops.class_key(ops.get_class(conn, n)["id"]) for n in names or []]


def send(conn: sqlite3.Connection, cfg: Config, kind: str, classes=None, text: str | None = None, n: int = 0,
         post=None) -> dict:
    """POST /push/send in batches until the Worker has nothing more for today. Returns the totals."""
    from .pull import PullError, http_post_json
    if kind not in KINDS:
        raise PushError("kind: video | message | report | test")
    if not enabled(cfg):
        raise PushError('push is not set up: run `daybook push keys`, then set the Worker secrets (README)')
    body = (text or default_text(kind, n)).strip()
    if text and lint.text_scrubber(conn)(body) != body:
        raise PushError("the text must not carry a name, a phone number or an id")
    keys = _class_keys(conn, classes)
    post = post or (lambda payload: http_post_json(cfg, "/push/send", payload))
    total = {"sent": 0, "removed": 0, "failed": 0, "capped": 0, "quiet": False}
    for _ in range(50):                          # batches of ~10 (the Worker's free-plan limits); stops on "more": false
        try:
            r = post({"classes": keys, "kind": kind, "title": strings()["push_title"], "body": body})
        except PullError as e:
            raise PushError(str(e)) from e
        if not r.get("ok"):
            raise PushError(f"the Worker refused: {r.get('error')}")
        if r.get("quiet"):
            total["quiet"] = True
            break
        for k in ("sent", "removed", "failed"):
            total[k] += int(r.get(k) or 0)
        total["capped"] = int(r.get("capped") or 0)
        if not r.get("more"):
            break
    log_event(conn, "push_quiet" if total["quiet"] else "push_send",
              {"kind": kind, "classes": classes or "all", **{k: total[k] for k in ("sent", "removed", "failed", "capped")}})
    conn.commit()
    return {**total, "kind": kind, "text": body}


def count(conn: sqlite3.Connection, cfg: Config, fetch=None) -> dict:
    """Subscribers per class (students / parents): numbers only, never who."""
    from .pull import http_fetch
    fetch = fetch or http_fetch(cfg)
    r = json.loads(fetch("/push/count"))
    names = {ops.class_key(c["id"]): c["name"] for c in conn.execute("SELECT id, name FROM classes")}
    return {"students": r.get("students", 0), "parents": r.get("parents", 0), "vapid": r.get("vapid", False),
            "by_class": {names.get(k, k): v for k, v in (r.get("by_class") or {}).items()}}


def after_publish(conn: sqlite3.Connection, cfg: Config, post=None) -> list[str]:
    """After 발행: one push for students (new videos > a teacher message > the weekly update) and the weekly update for
    parents. The Worker's daily cap means nobody gets two. Never raises; returns lines for the publish log."""
    from . import generate
    if not enabled(cfg) or cfg.extra.get("push_after_publish", True) is False:
        return []
    pubs = conn.execute("SELECT ts FROM events WHERE type = 'publish' ORDER BY id DESC LIMIT 2").fetchall()
    prev = pubs[1]["ts"] if len(pubs) > 1 else ""
    prev_day = prev[:10]
    new_videos = [v for v in generate.load_videos(cfg) if prev_day and v["added"] and v["added"] > prev_day]
    new_note = bool(prev) and conn.execute("SELECT 1 FROM notes WHERE ts > ? LIMIT 1", (prev,)).fetchone()
    kind, n = ("video", len(new_videos)) if new_videos else ("message", 0) if new_note else ("report", 0)
    out = []
    for k, nn in ((kind, n), ("report", 0)) if kind != "report" else (("report", 0),):
        try:
            r = send(conn, cfg, k, None, None, nn, post)
            out.append(f"push ({k}): quiet hours, nothing sent" if r["quiet"] else
                       f"push ({k}): {r['sent']} sent, {r['capped']} already had one today")
        except PushError as e:
            out.append(f"warning: push not sent ({e})")
    return out


def write_keys(cfg: Config, subject: str, force: bool = False) -> tuple[str, str]:
    """Generate VAPID keys into daybook.json (never the repo). Refuses to replace existing keys without force:
    new keys silently end every existing subscription."""
    from pathlib import Path
    from . import vapid
    path = Path(cfg.path or "daybook.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if data.get("vapid_private") and not force:
        raise PushError("daybook.json already has VAPID keys (--force replaces them and ends every subscription)")
    if not subject.startswith(("mailto:", "https://")):
        raise PushError("--subject must be mailto:you@example.com or an https:// URL")
    pub, priv = vapid.generate()
    data.update({"vapid_public": pub, "vapid_private": priv, "vapid_subject": subject})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
    return pub, str(path)
