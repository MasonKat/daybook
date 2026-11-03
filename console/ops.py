"""Write operations shared by the CLI and the board. Every write also appends to `events`."""
from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from .db import KST, log_event, new_token, now_utc_iso

WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")


class OpError(ValueError):
    pass


def get_class(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM classes WHERE name = ?", (name,)).fetchone()
    if row is None:
        names = ", ".join(r["name"] for r in conn.execute("SELECT name FROM classes ORDER BY id")) or "none"
        raise OpError(f"no class named {name!r} (classes: {names})")
    return row


def get_student(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM students WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise OpError(f"no student with slug {slug!r} (see `daybook roster list`)")
    return row


def ensure_session(conn: sqlite3.Connection, class_id: int, d: date, set_id: str | None = None,
                   topic: str | None = None) -> int:
    row = conn.execute("SELECT * FROM sessions WHERE class_id = ? AND date = ?", (class_id, d.isoformat())).fetchone()
    if row is None:
        cur = conn.execute("INSERT INTO sessions (class_id, date, set_id, topic) VALUES (?, ?, ?, ?)",
                           (class_id, d.isoformat(), set_id, topic))
        log_event(conn, "session_start", {"class_id": class_id, "date": d.isoformat(), "set_id": set_id})
        sid = cur.lastrowid
        # link submissions of that day that arrived before the session existed
        conn.execute(
            """UPDATE submissions SET session_id = ? WHERE session_id IS NULL AND day = ?
               AND student_id IN (SELECT id FROM students WHERE class_id = ?)""",
            (sid, d.isoformat(), class_id))
        return sid
    if (set_id and set_id != row["set_id"]) or (topic and topic != row["topic"]):
        conn.execute("UPDATE sessions SET set_id = COALESCE(?, set_id), topic = COALESCE(?, topic) WHERE id = ?",
                     (set_id, topic, row["id"]))
        log_event(conn, "session_update", {"session_id": row["id"], "set_id": set_id})
    return row["id"]


def set_attitude(conn: sqlite3.Connection, student_id: int, session_id: int, points: int, said_aloud: bool = False) -> None:
    """One score per student per session; a second tap corrects it (see DECISIONS.md)."""
    if not 0 <= int(points) <= 3:
        raise OpError("points must be 0..3")
    ts = now_utc_iso()
    conn.execute(
        """INSERT INTO attitude (student_id, session_id, points, said_aloud, ts) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (student_id, session_id) DO UPDATE SET points = excluded.points,
             said_aloud = excluded.said_aloud, ts = excluded.ts""",
        (student_id, session_id, int(points), 1 if said_aloud else 0, ts))
    log_event(conn, "attitude", {"student_id": student_id, "session_id": session_id, "points": int(points)}, ts)


def teacher_submit(conn: sqlite3.Connection, student_id: int, day: date, source: str = "teacher") -> bool:
    """Mark a submission on the student's behalf (board "대신 제출" or a paper copy). Returns False if one exists."""
    if source not in ("teacher", "paper"):
        raise OpError("source must be teacher or paper")
    if conn.execute("SELECT 1 FROM submissions WHERE student_id = ? AND day = ? AND status != 'deleted'",
                    (student_id, day.isoformat())).fetchone():
        return False
    st = conn.execute("SELECT class_id FROM students WHERE id = ?", (student_id,)).fetchone()
    sess = conn.execute("SELECT id FROM sessions WHERE class_id = ? AND date = ?",
                        (st["class_id"], day.isoformat())).fetchone()
    ts = now_utc_iso()
    conn.execute(
        """INSERT INTO submissions (student_id, session_id, day, ts, photo_key, source, status)
           VALUES (?, ?, ?, ?, NULL, ?, 'received')
           ON CONFLICT (student_id, day) DO UPDATE SET status = 'received', source = excluded.source""",
        (student_id, sess["id"] if sess else None, day.isoformat(), ts, source))
    log_event(conn, "submission", {"student_id": student_id, "day": day.isoformat(), "source": source}, ts)
    return True


def add_note(conn: sqlite3.Connection, student_id: int, week: str, text: str) -> None:
    if not WEEK_RE.match(week):
        raise OpError("week must look like 2026-W45")
    text = " ".join(text.split())
    if not text:
        raise OpError("empty note")
    ts = now_utc_iso()
    conn.execute("INSERT INTO notes (student_id, week, text, ts) VALUES (?, ?, ?, ?)", (student_id, week, text, ts))
    log_event(conn, "note", {"student_id": student_id, "week": week}, ts)


def rotate_tokens(conn: sqlite3.Connection, student: sqlite3.Row, which: str = "both") -> None:
    new_s = new_token() if which in ("both", "student") else student["student_token"]
    new_p = new_token() if which in ("both", "parent") else student["parent_token"]
    conn.execute("UPDATE students SET student_token = ?, parent_token = ? WHERE id = ?", (new_s, new_p, student["id"]))
    log_event(conn, "rotate_tokens", {
        "student_id": student["id"], "which": which,
        "old_student_token": student["student_token"] if new_s != student["student_token"] else None,
    })


# ---------------------------------------------------------------- 출석 (F2)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
OPEN_BEFORE = timedelta(minutes=30)
OPEN_AFTER = timedelta(minutes=60)


def class_key(class_id: int) -> str:
    """The Worker only ever sees this key, never a class name."""
    from .generate import set_hash
    return set_hash(f"class:{class_id}")


def start_session(conn: sqlite3.Connection, cls: sqlite3.Row, day: date, at: str | None = None,
                  set_id: str | None = None, topic: str | None = None, new_code: bool = False) -> sqlite3.Row:
    """Create/update today's session with a start time and a random 4-digit code (kept on re-runs)."""
    if at is not None and not TIME_RE.match(at):
        raise OpError("--at must be HH:MM (24 h)")
    sid = ensure_session(conn, cls["id"], day, set_id, topic)
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    start = at or row["start_time"] or datetime.now(KST).strftime("%H:%M")
    code = row["code"] if row["code"] and not new_code else f"{secrets.randbelow(10000):04d}"
    if (start, code) != (row["start_time"], row["code"]):
        conn.execute("UPDATE sessions SET start_time = ?, code = ? WHERE id = ?", (start, code, sid))
        log_event(conn, "session_code", {"session_id": sid, "start_time": start})
    return conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()


def session_window(day: date, start: str) -> tuple[datetime, datetime]:
    h, m = map(int, start.split(":"))
    t0 = datetime(day.year, day.month, day.day, h, m, tzinfo=KST)
    return t0 - OPEN_BEFORE, t0 + OPEN_AFTER


def session_payload(conn: sqlite3.Connection, sess: sqlite3.Row) -> dict:
    """PUT /session body: the session (class key, code, window) + the token -> class-key map of all active students."""
    day = date.fromisoformat(sess["date"])
    t_from, t_until = session_window(day, sess["start_time"])
    tokmap = {r["student_token"]: class_key(r["class_id"]) for r in conn.execute(
        "SELECT student_token, class_id FROM students_public WHERE active = 1 AND class_id IS NOT NULL")}
    return {"class": class_key(sess["class_id"]), "date": sess["date"], "code": sess["code"], "start": sess["start_time"],
            "open_from": t_from.isoformat(), "open_until": t_until.isoformat(), "tokmap": tokmap}


def record_checkin(conn: sqlite3.Connection, student_id: int, session_id: int, ts: str, source: str) -> bool:
    if source not in ("phone", "teacher"):
        raise OpError("source must be phone or teacher")
    cur = conn.execute("INSERT OR IGNORE INTO checkins (student_id, session_id, ts, source) VALUES (?, ?, ?, ?)",
                       (student_id, session_id, ts, source))
    if cur.rowcount:
        log_event(conn, "checkin", {"student_id": student_id, "session_id": session_id, "source": source}, ts)
    return bool(cur.rowcount)


def cancel_class(conn: sqlite3.Connection, cls: sqlite3.Row, day: date, reason: str | None = "휴강") -> None:
    """F5: mark a class day as cancelled (reason) or undo it (reason None)."""
    sid = ensure_session(conn, cls["id"], day)
    conn.execute("UPDATE sessions SET cancelled = ? WHERE id = ?", ((reason or "").strip() or None if reason is not None else None, sid))
    log_event(conn, "class_cancel" if reason is not None else "class_uncancel",
              {"class_id": cls["id"], "date": day.isoformat(), "reason": reason})
