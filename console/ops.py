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
    start = at or row["start_time"] or cls["start_time"] or datetime.now(KST).strftime("%H:%M")   # F11: the class's start
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
    return {"tokmap": tokmap(conn), "class": class_key(sess["class_id"]), "date": sess["date"], "code": sess["code"], "start": sess["start_time"],
            "open_from": t_from.isoformat(), "open_until": t_until.isoformat()}


def tokmap(conn: sqlite3.Connection) -> dict[str, str]:
    """token -> opaque class key for every active student (H3: the Worker accepts only these tokens)."""
    return {r["student_token"]: class_key(r["class_id"]) for r in conn.execute(
        "SELECT student_token, class_id FROM students_public WHERE active = 1 AND class_id IS NOT NULL")}


def board_window(day: date, start: str | None, until: str | None = None) -> tuple[datetime, datetime] | None:
    """H1: the board polls the Worker only from 30 min before the start until 2 h after it
    (F13.5: or until the end of the class's self-study block, when that is later, so 과제 끝 is pulled during it)."""
    if not start or not TIME_RE.match(start):
        return None
    t0, _ = session_window(day, start)
    h, m = map(int, start.split(":"))
    end = datetime(day.year, day.month, day.day, h, m, tzinfo=KST) + timedelta(hours=2)
    if until and TIME_RE.match(until):
        uh, um = map(int, until.split(":"))
        end = max(end, datetime(day.year, day.month, day.day, uh, um, tzinfo=KST))
    return t0, end


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


def edit_student(conn: sqlite3.Connection, slug: str, display: str | None = None, new_slug: str | None = None,
                 track: str | None = None, class_name: str | None = None, active: bool | None = None) -> set[str]:
    """Shared by `daybook roster edit` and the Teacher tab. Returns the changed field names."""
    from . import models as M
    s = get_student(conn, slug)
    fields: dict = {}
    if display is not None:
        if not display.strip():
            raise OpError("display name cannot be empty")
        fields["display_name"] = display.strip()
    if new_slug is not None:
        fields["slug"] = M.unique_slug(conn, new_slug)
    if track is not None:
        fields["track"] = track
    if class_name is not None:
        fields["class_id"] = get_class(conn, class_name)["id"]
    if active is not None:
        fields["active"] = 1 if active else 0
    if not fields:
        raise OpError("nothing to change")
    conn.execute(f"UPDATE students SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), s["id"]))
    log_event(conn, "student_update", {"student_id": s["id"], "fields": sorted(fields)})
    return set(fields)


# ---------------------------------------------------------------- F13: class policy + 푼 문제 수

POLICY_FIELDS = ("start_time", "end_time", "self_study_until", "late_min", "late_count", "late_avg_min")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def class_policy(conn: sqlite3.Connection, cls: sqlite3.Row, values: dict) -> dict:
    """F13.2/F13.5: per-class 지각 thresholds and class hours. Shared by `daybook class edit` and the Teacher tab.
    An empty time clears it (e.g. no self-study block)."""
    fields: dict = {}
    for k, v in values.items():
        if k not in POLICY_FIELDS or v is None:
            continue
        if k in ("start_time", "end_time", "self_study_until"):
            v = str(v).strip()
            if v and not _HHMM.match(v):
                raise OpError(f"{k} must be HH:MM")
            fields[k] = v or None
        else:
            try:
                n = int(v)
            except (TypeError, ValueError) as e:
                raise OpError(f"{k} must be a whole number") from e
            if not (1 if k == "late_count" else 0) <= n <= (6 if k == "late_count" else 120):
                raise OpError(f"{k} is out of range")
            fields[k] = n
    if fields:
        conn.execute(f"UPDATE classes SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), cls["id"]))
        log_event(conn, "class_policy", {"class_id": cls["id"], **fields})
    return fields


def set_solved(conn: sqlite3.Connection, student_id: int, day: str, solved) -> None:
    """F13.5: the teacher corrects a self-reported 오늘 푼 문제 수 (0-60; empty clears it)."""
    try:
        date.fromisoformat(day)
    except ValueError as e:
        raise OpError("day must be YYYY-MM-DD") from e
    n = None if solved in (None, "") else int(solved)
    if n is not None and not 0 <= n <= 60:
        raise OpError("푼 문제 수 is 0-60")
    cur = conn.execute("UPDATE submissions SET solved = ? WHERE student_id = ? AND day = ? AND status != 'deleted'",
                       (n, student_id, day))
    if not cur.rowcount:
        raise OpError("no submission on that day")
    log_event(conn, "solved_fix", {"student_id": student_id, "day": day, "solved": n})


# ---------------------------------------------------------------- F11: the teacher manages classes and members

PHONE_OK = re.compile(r"^[0-9+\- ]{7,20}$")


def save_class(conn: sqlite3.Connection, name: str, *, create: bool, new_name: str | None = None, term: str | None = None,
               days: str | None = None, reward_label: str | None = None, goal: int | None = None, grade: str | None = None,
               start_time: str | None = None) -> sqlite3.Row:
    """반 만들기 / 반 수정 (the Teacher tab; the same rules as `daybook class add/edit`). None = leave as it is."""
    from . import models as M
    name = (name or "").strip()
    if not name or len(name) > 20:
        raise OpError("class name: 1-20 characters")
    fields: dict = {}
    if term is not None:
        if M.parse_term(term) == (None, None):
            raise OpError("term must be START..END, e.g. 2026-11-02..2027-02-26")
        fields["term"] = term
    if days is not None:
        try:
            fields["weekday_mask"] = M.mask_from_days(days)
        except ValueError as e:
            raise OpError(str(e)) from e
    if reward_label is not None:
        fields["reward_label"] = reward_label.strip()
    if goal is not None:
        if goal < 0:
            raise OpError("goal must be 0 (computed) or more")
        fields["reward_goal_points"] = goal or None
    if grade is not None:
        fields["grade"] = grade.strip() or None
    if start_time is not None:
        if start_time and not _HHMM.match(start_time):
            raise OpError("start time must be HH:MM")
        fields["start_time"] = start_time or None
    if create:
        if conn.execute("SELECT 1 FROM classes WHERE name = ?", (name,)).fetchone():
            raise OpError(f"class {name!r} exists")
        if "term" not in fields or "weekday_mask" not in fields:
            raise OpError("a new class needs a term and weekdays")
        cols = ["name", *fields]
        cur = conn.execute(f"INSERT INTO classes ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", (name, *fields.values()))
        log_event(conn, "class_add", {"class_id": cur.lastrowid, "name": name})
        return conn.execute("SELECT * FROM classes WHERE id = ?", (cur.lastrowid,)).fetchone()
    cls = get_class(conn, name)
    if new_name and new_name.strip() != name:
        if conn.execute("SELECT 1 FROM classes WHERE name = ?", (new_name.strip(),)).fetchone():
            raise OpError(f"class {new_name.strip()!r} exists")
        fields["name"] = new_name.strip()
    if not fields:
        raise OpError("nothing to change")
    conn.execute(f"UPDATE classes SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), cls["id"]))
    log_event(conn, "class_edit", {"class_id": cls["id"], "fields": sorted(fields)})
    return conn.execute("SELECT * FROM classes WHERE id = ?", (cls["id"],)).fetchone()


def set_left(conn: sqlite3.Connection, slug: str, left_on: str | None) -> sqlite3.Row:
    """퇴원 처리 (a date) or its undo (None). No new page renders while inactive; the history stays."""
    s = get_student(conn, slug)
    if left_on is not None:
        try:
            date.fromisoformat(left_on)
        except ValueError as e:
            raise OpError("date must be YYYY-MM-DD") from e
    conn.execute("UPDATE students SET active = ?, left_on = ? WHERE id = ?", (0 if left_on else 1, left_on, s["id"]))
    log_event(conn, "student_leave" if left_on else "student_return", {"student_id": s["id"], "date": left_on})
    return get_student(conn, slug)
