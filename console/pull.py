"""`daybook pull`: GET /submissions since the last pull -> submissions (+ events). Photos stay in the Worker."""
from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .config import Config
from .db import last_event, log_event

Fetch = Callable[[str], bytes]   # url path+query -> response body
MARGIN = timedelta(minutes=5)
CHIPS = ("ok", "confused", "rewatch")    # KV listing is eventually consistent; re-read a small overlap (upserts are idempotent)


class PullError(RuntimeError):
    pass


def http_post_json(cfg: Config, path: str, payload: dict) -> dict:
    return http_put_json(cfg, path, payload, method="POST")


def http_put_json(cfg: Config, path: str, payload: dict, method: str = "PUT") -> dict:
    if not cfg.worker or not cfg.teacher_token:
        raise PullError("worker_url and teacher_token must be set")
    req = urllib.request.Request(cfg.worker + path, data=json.dumps(payload).encode("utf-8"), method=method,
                                 headers={"X-Teacher-Token": cfg.teacher_token, "Content-Type": "application/json",
                                          "User-Agent": "daybook-console"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise PullError(f"Worker answered {e.code} for {path}") from e
    except urllib.error.URLError as e:
        raise PullError(f"cannot reach the Worker: {e.reason}") from e


def push_tokmap(conn: sqlite3.Connection, cfg: Config) -> str:
    """H3: send the token -> class-key map so the Worker accepts exactly the current students' links.
    Returns a one-line status for the CLI (never raises: publishing must not depend on the network)."""
    from .ops import no_consent_tokens, parent_tokmap, tokmap
    if not cfg.worker or not cfg.teacher_token:
        return ""
    try:
        r = http_put_json(cfg, "/tokmap", {"tokmap": tokmap(conn), "parents": parent_tokmap(conn),
                                           "no_consent": no_consent_tokens(conn)})
        msg = f"Worker: {r.get('n', '?')} student links accepted"
    except PullError as e:
        return f"warning: student links not sent to the Worker ({e}); uploads from new links wait until it is"
    from . import rounds as R                     # F14: the weekly-test rounds travel with the token map
    if R.worker_payload(conn):
        msg += "\n" + R.push(conn, cfg)
    return msg


def http_fetch(cfg: Config) -> Fetch:
    if not cfg.worker or not cfg.teacher_token:
        raise PullError("worker_url and teacher_token must be set (daybook.json or DAYBOOK_WORKER_URL / "
                        "DAYBOOK_TEACHER_TOKEN)")

    def fetch(path: str) -> bytes:
        req = urllib.request.Request(cfg.worker + path, headers={"X-Teacher-Token": cfg.teacher_token,
                                                                 "User-Agent": "daybook-console"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise PullError(f"Worker answered {e.code} for {path.split('?')[0]}") from e
        except urllib.error.URLError as e:
            raise PullError(f"cannot reach the Worker: {e.reason}") from e

    return fetch


@dataclass
class PullReport:
    fetched: int = 0
    new: int = 0
    updated: int = 0
    unknown_tokens: int = 0
    photos: list[str] = field(default_factory=list)
    checkins: int = 0
    live: int = 0
    reports: int = 0
    watches: int = 0
    reviews: int = 0
    votes: int = 0


def _token_map(conn: sqlite3.Connection) -> dict[str, int]:
    m = {}
    # tokens retired by `roster rotate-tokens` still map to their student
    for r in conn.execute("SELECT payload_json FROM events WHERE type = 'rotate_tokens' ORDER BY id"):
        p = json.loads(r["payload_json"])
        if p.get("old_student_token"):
            m[p["old_student_token"]] = p["student_id"]
    for r in conn.execute("SELECT id, student_token FROM students"):
        m[r["student_token"]] = r["id"]
    return m


def _teacher_fixed(conn: sqlite3.Connection, student_id: int, day: str) -> bool:
    return bool(conn.execute("""SELECT 1 FROM events WHERE type = 'solved_fix' AND json_extract(payload_json, '$.student_id') = ?
                                AND json_extract(payload_json, '$.day') = ? LIMIT 1""", (student_id, day)).fetchone())


def apply_items(conn: sqlite3.Connection, items: list[dict]) -> PullReport:
    rep = PullReport(fetched=len(items))
    tokens = _token_map(conn)
    for it in items:
        sid = tokens.get(it.get("token", ""))
        if sid is None:
            rep.unknown_tokens += 1
            continue
        day, ts, key = it["day"], it["ts"], it.get("key")
        cls = conn.execute("SELECT class_id FROM students WHERE id = ?", (sid,)).fetchone()
        sess = conn.execute("SELECT id FROM sessions WHERE class_id = ? AND date = ?",
                            (cls["class_id"], day)).fetchone() if cls else None
        cur = conn.execute("SELECT * FROM submissions WHERE student_id = ? AND day = ?", (sid, day)).fetchone()
        payload = {"student_id": sid, "day": day, "ts": ts, "set": it.get("set", ""), "n": it.get("n", 1)}
        line = str(it.get("line") or "")[:40] or None
        chip = it.get("chip") if it.get("chip") in CHIPS else None
        solved = it.get("solved") if isinstance(it.get("solved"), int) and 0 <= it["solved"] <= 60 else None
        if cur is not None and (solved is None or _teacher_fixed(conn, sid, day)):
            solved = cur["solved"]          # F13.5: no new count, or the teacher corrected it: keep what is there
        if cur is None:
            conn.execute(
                """INSERT INTO submissions (student_id, session_id, day, ts, photo_key, source, status, reflection, chip, solved)
                   VALUES (?, ?, ?, ?, ?, 'phone', 'received', ?, ?, ?)""",
                (sid, sess["id"] if sess else None, day, ts, key, line, chip, solved),
            )
            log_event(conn, "submission", payload)
            rep.new += 1
        elif (cur["photo_key"] != key or cur["source"] != "phone" or cur["status"] == "deleted"
              or (cur["reflection"] or None) != line or (cur["chip"] or None) != chip or cur["solved"] != solved):
            conn.execute(
                "UPDATE submissions SET photo_key = ?, source = 'phone', status = 'received', ts = MIN(ts, ?), "
                "session_id = COALESCE(session_id, ?), reflection = ?, chip = ?, solved = ? WHERE id = ?",
                (key, ts, sess["id"] if sess else None, line, chip, solved, cur["id"]),
            )
            log_event(conn, "submission_update", payload)
            rep.updated += 1
    return rep


def apply_watches(conn: sqlite3.Connection, items: list[dict]) -> int:
    """F15.1: watch signals -> watches (one row per student, video and day; the larger value wins)."""
    tokens = _token_map(conn)
    consented = {r[0] for r in conn.execute("SELECT id FROM students WHERE consent = 'yes'")}   # F17
    n = 0
    for it in items:
        sid = tokens.get(it.get("token", ""))
        if sid not in consented:
            continue
        try:
            v, day, sec, pct = str(it["v"]), str(it["day"]), int(it["sec"]), int(it["pct"])
        except (KeyError, TypeError, ValueError):
            continue
        if sid is None or not (0 <= sec <= 36000 and 0 <= pct <= 100):
            continue
        conn.execute("""INSERT INTO watches (student_id, video_id, day, sec, pct, ts) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT (student_id, video_id, day) DO UPDATE SET sec = MAX(watches.sec, excluded.sec),
                          pct = MAX(watches.pct, excluded.pct), ts = excluded.ts""", (sid, v, day, sec, pct, it.get("ts", "")))
        n += 1
    return n


def apply_checkins(conn: sqlite3.Connection, items: list[dict], rep: PullReport) -> None:
    from datetime import date as _date
    from .ops import ensure_session, record_checkin
    tokens = _token_map(conn)
    for it in items:
        sid = tokens.get(it.get("token", ""))
        if sid is None:
            rep.unknown_tokens += 1
            continue
        st = conn.execute("SELECT class_id FROM students WHERE id = ?", (sid,)).fetchone()
        if not st or st["class_id"] is None:
            continue
        sess = ensure_session(conn, st["class_id"], _date.fromisoformat(it["day"]))
        if record_checkin(conn, sid, sess, it["ts"], "phone"):
            rep.checkins += 1


def apply_live(conn: sqlite3.Connection, items: list[dict], rep: PullReport) -> None:
    """F7: AMP Live keeps only per-student counts; they become `effort` rows (source 'live')."""
    tokens = _token_map(conn)
    for it in items:
        sid = tokens.get(it.get("token", ""))
        if sid is None or not all(k in it for k in ("date", "room", "answered", "correct", "ts")):
            continue
        set_id = f"live:{it['date']}:{it['room']}"
        cur = conn.execute("SELECT id FROM effort WHERE student_id = ? AND set_id = ? AND source = 'live'", (sid, set_id)).fetchone()
        if cur:
            conn.execute("UPDATE effort SET attempted = ?, correct = ?, ts = ? WHERE id = ?",
                         (int(it["answered"]), int(it["correct"]), it["ts"], cur["id"]))
        else:
            conn.execute("INSERT INTO effort (student_id, set_id, item_id, attempted, correct, source, ts) VALUES (?, ?, NULL, ?, ?, 'live', ?)",
                         (sid, set_id, int(it["answered"]), int(it["correct"]), it["ts"]))
            log_event(conn, "live_result", {"student_id": sid, "set_id": set_id})
            rep.live += 1


def pull(conn: sqlite3.Connection, fetch: Fetch, photos_dir: str | Path | None = None) -> PullReport:
    last = last_event(conn, "pull") or {}
    since = last.get("next_since", "")
    body = json.loads(fetch("/submissions" + (f"?since={urllib.parse.quote(since)}" if since else "")))
    rep = apply_items(conn, body.get("items", []))
    try:
        ck = json.loads(fetch("/checkins" + (f"?since={urllib.parse.quote(since)}" if since else "")))
        apply_checkins(conn, ck.get("items", []), rep)
    except PullError:
        pass  # an older Worker without /checkins
    try:
        lv = json.loads(fetch("/live/results" + (f"?since={urllib.parse.quote(since)}" if since else "")))
        apply_live(conn, lv.get("items", []), rep)
    except PullError:
        pass  # an older Worker without AMP Live
    try:
        from . import rounds as R
        rp = json.loads(fetch("/reports" + (f"?since={urllib.parse.quote(since)}" if since else "")))
        n_new, n_upd = R.apply_reports(conn, rp.get("items", []))
        rep.reports = n_new + n_upd
    except PullError:
        pass  # an older Worker without weekly-test reports
    try:
        wt = json.loads(fetch("/watches" + (f"?since={urllib.parse.quote(since)}" if since else "")))
        rep.watches = apply_watches(conn, wt.get("items", []))
    except PullError:
        pass  # an older Worker without watch signals
    from . import reviews as RV                     # F18.3: new 학생 리뷰 and votes; finished votes get their winners
    rep.reviews, rep.votes = RV.pull_and_close(conn, fetch)
    now = datetime.fromisoformat(body.get("now", datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
    next_since = (now - MARGIN).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if photos_dir:
        out = Path(photos_dir)
        out.mkdir(parents=True, exist_ok=True)
        for it in body.get("items", []):
            key = it.get("key")
            if not key:
                continue
            target = out / (key.replace("photo:", "").replace(":", "_").replace("/", "_") + ("" if "." in key else ".jpg"))
            try:
                target.write_bytes(fetch("/photo?key=" + urllib.parse.quote(key, safe="")))
                rep.photos.append(str(target))
            except PullError:
                pass  # expired (7-day retention) or missing
    log_event(conn, "pull", {"since": since, "next_since": next_since, "fetched": rep.fetched, "new": rep.new,
                             "updated": rep.updated, "unknown_tokens": rep.unknown_tokens,
                             "checkins": rep.checkins, "live": rep.live, "reports": rep.reports, "watches": rep.watches,
                             "reviews": rep.reviews})
    conn.commit()
    return rep
