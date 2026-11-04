"""F18.3 학생 리뷰: students write a funny or great class moment; the teacher approves (pre-moderation); classmates
vote at a random moment once per 2-week period; the winner becomes a "베스트 리뷰" record.

Safety: the same filter runs on the phone and in the Worker (worker/src/review_filter.js, wordlist in
filters/review_words.txt + filters/review_words.local.txt); classmates' names are compared by hash only (the page gets
salted SHA-256 of display names; the Worker gets HMAC hashes of display and full names keyed with the teacher token).
Nothing reaches classmates until approved; the teacher can hide an approved review at any time; every action is an event.
korea mode shows the writer's display name unless they ticked 이름 숨기기; manitoba mode is always anonymous. Parents never
see other children's text: a parent page shows only its own child's win.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import models as M
from .config import ROOT, Config
from .db import KST, log_event, now_utc_iso

EPOCH = date(2026, 1, 5)              # a Monday: periods are the 14 days from EPOCH + 14k
VOTE_HOURS = 24
WINDOW = (time(15, 0), time(21, 30))  # the random moment: a class day between 15:00 and 21:30 KST (never quiet hours)
WORDS = (ROOT / "filters" / "review_words.txt", ROOT / "filters" / "review_words.local.txt")


class ReviewError(ValueError):
    pass


def period(d: date) -> tuple[date, date]:
    k = (d - EPOCH).days // 14
    start = EPOCH + timedelta(days=14 * k)
    return start, start + timedelta(days=13)


def period_label(start: date, end: date) -> str:
    return f"{start.month}/{start.day}–{end.month}/{end.day}"


def words() -> list[str]:
    out = []
    for p in WORDS:
        if p.exists():
            out += [w.strip() for w in p.read_text(encoding="utf-8").splitlines() if w.strip() and not w.strip().startswith("#")]
    return out


def words_text() -> str:
    return "\n".join(words())


def page_name_hashes(conn: sqlite3.Connection, class_id: int) -> list[str]:
    """For the phone's filter: salted SHA-256 of the class's display names (salt = the class key); never names."""
    from .ops import class_key
    salt = class_key(class_id)
    return sorted({hashlib.sha256(f"{salt}:{r[0]}".encode()).hexdigest()[:16]
                   for r in conn.execute("SELECT display_name FROM students_public WHERE class_id = ? AND active = 1", (class_id,))
                   if r[0] and len(r[0]) >= 2})


def worker_name_hashes(conn: sqlite3.Connection, cfg: Config) -> dict[str, list[str]]:
    """For the Worker's filter: HMAC (the teacher token) of display and full names per class key."""
    from .ops import class_key
    out: dict[str, set] = {}
    key = (cfg.teacher_token or "").encode()
    for r in conn.execute("SELECT class_id, display_name, full_name FROM students WHERE active = 1 AND class_id IS NOT NULL"):
        for n in {r["display_name"], r["full_name"]}:
            if n and len(n) >= 2:
                out.setdefault(class_key(r["class_id"]), set()).add(hmac.new(key, f"name:{n}".encode(), hashlib.sha256).hexdigest()[:16])
    return {k: sorted(v) for k, v in out.items()}


def label(conn: sqlite3.Connection, cfg: Config, r: sqlite3.Row) -> str:
    """What classmates see as the writer: the display name in korea mode unless hidden; always anonymous in manitoba."""
    from . import practice
    if r["hide_name"] or practice.manitoba(conn, cfg):
        return ""
    row = conn.execute("SELECT display_name FROM students_public WHERE id = ?", (r["student_id"],)).fetchone()
    return row[0] if row else ""


def apply_pull(conn: sqlite3.Connection, body: dict) -> tuple[int, int]:
    from .pull import _token_map
    tokens = _token_map(conn)
    new = votes = 0
    for it in body.get("reviews", []):
        sid = tokens.get(it.get("token", ""))
        if sid is None or conn.execute("SELECT 1 FROM reviews WHERE id = ?", (it["id"],)).fetchone():
            continue
        cls = conn.execute("SELECT class_id FROM students WHERE id = ?", (sid,)).fetchone()
        conn.execute("""INSERT INTO reviews (id, student_id, class_id, day, text, hide_name, status, ts)
                        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                     (it["id"], sid, cls["class_id"] if cls else None, it["day"], str(it["text"])[:60], int(it.get("hide") or 0), it["ts"]))
        log_event(conn, "review_new", {"review": it["id"], "student_id": sid})
        new += 1
    for it in body.get("votes", []):
        sid = tokens.get(it.get("token", ""))
        if sid is None or not conn.execute("SELECT 1 FROM review_rounds WHERE id = ?", (it["round"],)).fetchone() \
                or not conn.execute("SELECT 1 FROM reviews WHERE id = ?", (it["review"],)).fetchone():
            continue
        cur = conn.execute("INSERT OR IGNORE INTO review_votes (round_id, student_id, review_id, ts) VALUES (?, ?, ?, ?)",
                           (it["round"], sid, it["review"], it["ts"]))
        votes += cur.rowcount
    return new, votes


def decide(conn: sqlite3.Connection, review_id: str, status: str) -> None:
    if status not in ("approved", "hidden", "pending"):
        raise ReviewError("status: approved | hidden")
    r = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    if not r:
        raise ReviewError("no such review")
    conn.execute("UPDATE reviews SET status = ?, decided_ts = ? WHERE id = ?", (status, now_utc_iso(), review_id))
    log_event(conn, "review_" + ("approve" if status == "approved" else "hide" if status == "hidden" else "reset"),
              {"review": review_id, "student_id": r["student_id"]})


def _round_id(class_id: int, start: date) -> str:
    return f"c{class_id}-{start.isoformat()}"


def plan(conn: sqlite3.Connection, today: date, rng: random.Random | None = None) -> list[str]:
    """One random moment per class for the current 2-week period (a class day, 15:00-21:30 KST, today or later)."""
    rng = rng or random.Random()
    start, end = period(today)
    made = []
    for c in conn.execute("SELECT * FROM classes ORDER BY id").fetchall():
        rid = _round_id(c["id"], start)
        if conn.execute("SELECT 1 FROM review_rounds WHERE id = ?", (rid,)).fetchone():
            continue
        days = sorted(d for d in M.scheduled_days(c["weekday_mask"], max(start, today), end)) if c["weekday_mask"] else []
        if not days:
            continue
        d = rng.choice(days)
        lo = WINDOW[0].hour * 60 + WINDOW[0].minute
        hi = WINDOW[1].hour * 60 + WINDOW[1].minute
        m = rng.randint(lo, hi)
        at = datetime.combine(d, time(m // 60, m % 60), KST)
        if at < M.kst_now() and d == today:
            at = M.kst_now() + timedelta(minutes=5)
        _store_round(conn, c["id"], start, end, at)
        made.append(rid)
    return made


def _store_round(conn, class_id, start, end, at: datetime) -> str:
    rid = _round_id(class_id, start)
    open_at = at.astimezone(timezone.utc)
    conn.execute("""INSERT INTO review_rounds (id, class_id, period_from, period_to, open_at, close_at, status) VALUES (?, ?, ?, ?, ?, ?, 'planned')
                    ON CONFLICT (id) DO UPDATE SET open_at = excluded.open_at, close_at = excluded.close_at, status = 'planned'""",
                 (rid, class_id, start.isoformat(), end.isoformat(), open_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                  (open_at + timedelta(hours=VOTE_HOURS)).strftime("%Y-%m-%dT%H:%M:%S.000Z")))
    log_event(conn, "review_round", {"round": rid, "open_at": open_at.isoformat()})
    return rid


def open_now(conn: sqlite3.Connection, class_id: int, now: datetime | None = None) -> str:
    """지금 투표 열기: this period's vote opens now for 24 h."""
    now = now or M.kst_now()
    start, end = period(now.date())
    return _store_round(conn, class_id, start, end, now)


def candidates(conn: sqlite3.Connection, rnd: sqlite3.Row) -> list[sqlite3.Row]:
    return conn.execute("""SELECT * FROM reviews WHERE class_id = ? AND status = 'approved' AND day BETWEEN ? AND ? ORDER BY ts""",
                        (rnd["class_id"], rnd["period_from"], rnd["period_to"])).fetchall()


def close_due(conn: sqlite3.Connection, now: datetime | None = None) -> list[str]:
    """Rounds whose 24 h are over: winners = the most votes (ties keep all); fewer than 3 candidates = no vote."""
    now_s = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    done = []
    for rnd in conn.execute("SELECT * FROM review_rounds WHERE status = 'planned' AND close_at <= ?", (now_s,)).fetchall():
        cands = candidates(conn, rnd)
        counts = {r["id"]: 0 for r in cands}
        for v in conn.execute("SELECT review_id FROM review_votes WHERE round_id = ?", (rnd["id"],)):
            if v[0] in counts:
                counts[v[0]] += 1
        if len(cands) < 3 or not any(counts.values()):
            conn.execute("UPDATE review_rounds SET status = 'novote' WHERE id = ?", (rnd["id"],))
            log_event(conn, "review_novote", {"round": rnd["id"], "candidates": len(cands)})
        else:
            top = max(counts.values())
            for rid, n in counts.items():
                if n == top:
                    conn.execute("INSERT OR IGNORE INTO review_wins (round_id, review_id, votes) VALUES (?, ?, ?)", (rnd["id"], rid, n))
            conn.execute("UPDATE review_rounds SET status = 'closed' WHERE id = ?", (rnd["id"],))
            log_event(conn, "review_winners", {"round": rnd["id"], "votes": top})
        done.append(rnd["id"])
    return done


def wins(conn: sqlite3.Connection, class_id: int | None = None) -> list[dict]:
    q = """SELECT w.round_id, w.votes, r.id, r.text, r.student_id, r.hide_name, r.class_id, rr.period_from, rr.period_to
           FROM review_wins w JOIN reviews r ON r.id = w.review_id JOIN review_rounds rr ON rr.id = w.round_id
           WHERE r.status = 'approved'""" + (" AND r.class_id = ?" if class_id else "") + " ORDER BY rr.period_from DESC, r.ts"
    return [dict(x) for x in conn.execute(q, (class_id,) if class_id else ())]


def sync_payload(conn: sqlite3.Connection, cfg: Config) -> dict:
    from .ops import class_key
    revs = conn.execute("SELECT * FROM reviews WHERE status != 'pending'").fetchall()
    now_s = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    rounds = [{"id": r["id"], "class": class_key(r["class_id"]), "open_from": r["open_at"], "open_until": r["close_at"],
               "period_from": r["period_from"], "period_to": r["period_to"]}
              for r in conn.execute("SELECT * FROM review_rounds WHERE status = 'planned' AND close_at > ?", (now_s,))]
    best = []
    for w in wins(conn):
        rr = conn.execute("SELECT * FROM reviews WHERE id = ?", (w["id"],)).fetchone()
        best.append({"review": w["id"], "class": class_key(w["class_id"]), "text": w["text"], "label": label(conn, cfg, rr),
                     "period": period_label(date.fromisoformat(w["period_from"]), date.fromisoformat(w["period_to"]))})
    return {"reviews": [{"id": r["id"], "status": r["status"], "label": label(conn, cfg, r)} for r in revs],
            "rounds": rounds, "best": best, "words": words(), "names": worker_name_hashes(conn, cfg)}


def sync(conn: sqlite3.Connection, cfg: Config) -> str:
    from .pull import PullError, http_put_json
    if not cfg.worker or not cfg.teacher_token:
        return ""
    try:
        http_put_json(cfg, "/reviews-sync", sync_payload(conn, cfg))
        return "Worker: 학생 리뷰 synced"
    except PullError as e:
        return f"warning: 학생 리뷰 not synced ({e})"


def after_publish(conn: sqlite3.Connection, cfg: Config, today: date | None = None) -> str:
    """발행: plan this period's vote moments (once per class), then send decisions / rounds / winners / wordlist."""
    plan(conn, today or M.kst_today())
    conn.commit()
    return sync(conn, cfg)


def pull_and_close(conn: sqlite3.Connection, fetch) -> tuple[int, int]:
    """`daybook pull`: new reviews and votes; rounds whose 24 h are over get their winners."""
    from .pull import PullError
    since = conn.execute("SELECT payload_json FROM events WHERE type = 'review_pull' ORDER BY id DESC LIMIT 1").fetchone()
    since = json.loads(since[0]).get("next", "") if since else ""
    try:
        body = json.loads(fetch("/reviews-pull" + (f"?since={since}" if since else "")))
    except PullError:
        return 0, 0
    new, votes = apply_pull(conn, body)
    close_due(conn)
    nxt = ""
    if body.get("now"):                             # a small overlap: rows are deduplicated by id
        nxt = (datetime.fromisoformat(body["now"].replace("Z", "+00:00")) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    log_event(conn, "review_pull", {"next": nxt, "new": new, "votes": votes})
    return new, votes


def parent_win(conn: sqlite3.Connection, student_id: int, today: date) -> dict | None:
    """The parent page's only review line: this child's own win in the latest finished period (last 14 days)."""
    for w in wins(conn):
        if w["student_id"] == student_id and date.fromisoformat(w["period_to"]) >= today - timedelta(days=28):
            return {"text": w["text"], "period": period_label(date.fromisoformat(w["period_from"]), date.fromisoformat(w["period_to"]))}
    return None
