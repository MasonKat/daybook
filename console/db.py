"""SQLite schema, connection helpers and the append-only event log.

Table and column names follow BUILD_BRIEF §4 exactly. `full_name`, `parent_phone`
and `ext_id` are private: the generator only reads the `students_public` view.
"""
from __future__ import annotations

import base64
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 8
KST = timezone(timedelta(hours=9))  # Asia/Seoul has no DST

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL UNIQUE,
    term               TEXT,              -- 'YYYY-MM-DD..YYYY-MM-DD' (start..end), see DECISIONS.md
    weekday_mask       INTEGER NOT NULL DEFAULT 0,  -- Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64
    reward_label       TEXT,
    reward_goal_points INTEGER,           -- NULL = computed (and frozen) at the next publish
    goal_coef          REAL DEFAULT 2.4,
    milestone_fracs    TEXT DEFAULT '0.33,0.67',
    grade              TEXT,              -- F10: e.g. 고1 / 고2 / 기초 (videos.json `grades` match it)
    start_time         TEXT,              -- F13: class hours HH:MM (sessions may override the start)
    end_time           TEXT,
    self_study_until   TEXT,              -- F13.5: e.g. 21:00 after a 17:30-20:00 class
    late_min           INTEGER DEFAULT 10,   -- F13.2 policy: late = check-in later than start + late_min
    late_count         INTEGER DEFAULT 3,    -- ... on >= late_count of the last 6 class days
    late_avg_min       INTEGER DEFAULT 15    -- ... or an average lateness >= late_avg_min
);

CREATE TABLE IF NOT EXISTS students (
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL,          -- console only
    display_name  TEXT NOT NULL,
    class_id      INTEGER REFERENCES classes(id),
    track         TEXT,
    parent_phone  TEXT,                   -- console only
    student_token TEXT NOT NULL UNIQUE,
    parent_token  TEXT NOT NULL UNIQUE,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    ext_id        TEXT UNIQUE             -- console only (the teacher's roster ID)
);

CREATE TABLE IF NOT EXISTS sessions (
    id       INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id),
    date     TEXT NOT NULL,
    set_id   TEXT,
    topic    TEXT,
    note     TEXT,
    start_time TEXT,                      -- HH:MM (F2)
    code     TEXT,                        -- 4-digit check-in code (F2)
    cancelled TEXT,                       -- F5: a reason (e.g. 휴강) = not a class day for streaks and goals
    UNIQUE (class_id, date)
);

CREATE TABLE IF NOT EXISTS moments (      -- 오늘의 장면 (F4): class-wide, up to 3 per session
    id         INTEGER PRIMARY KEY,
    class_id   INTEGER NOT NULL REFERENCES classes(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts         TEXT NOT NULL,
    text       TEXT,
    image_key  TEXT NOT NULL                -- file name inside moments_dir (EXIF-free JPEG, <= 1080 px, <= 200 KB)
);

CREATE TABLE IF NOT EXISTS parent_profiles (   -- F13.1: how this parent wants to be reached (laptop only)
    student_id INTEGER PRIMARY KEY REFERENCES students(id),
    channel    TEXT NOT NULL DEFAULT 'call' CHECK (channel IN ('call', 'kakao', 'both')),
    style      TEXT NOT NULL DEFAULT 'weekly' CHECK (style IN ('weekly', 'needed')),
    greeting   TEXT NOT NULL DEFAULT '보호자님' CHECK (greeting IN ('어머님', '아버님', '보호자님'))
);

CREATE TABLE IF NOT EXISTS contacts (       -- F13.1: the teacher's parent-contact log (never published)
    id         INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('call', 'message', 'meeting')),
    direction  TEXT NOT NULL DEFAULT 'out' CHECK (direction IN ('out', 'in')),
    summary    TEXT,                       -- <= 120 characters
    follow_up  TEXT                        -- YYYY-MM-DD
);

CREATE TABLE IF NOT EXISTS snoozes (        -- F13.2: "이번 주는 보류" (7 days)
    id         INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    rule       TEXT NOT NULL,
    until      TEXT NOT NULL,
    ts         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rounds (         -- F14: weekly-test rounds (the teacher's own round ids; never online)
    id         TEXT PRIMARY KEY,               -- e.g. Adaptive_Screeners_1S_Nov06_G10
    rid        TEXT NOT NULL UNIQUE,           -- the hash the Worker and the pages use
    grade      TEXT,                           -- 1학년 | 2학년 (the teacher's Notion "Grade")
    classes    TEXT NOT NULL,                  -- class ids, comma-separated
    n          INTEGER NOT NULL,               -- number of quizzes
    open_at    TEXT NOT NULL,                  -- KST 'YYYY-MM-DD HH:MM'
    close_at   TEXT NOT NULL,
    label      TEXT NOT NULL                   -- what students see, e.g. 11/6 주간 테스트
);

CREATE TABLE IF NOT EXISTS test_reports (   -- F14: what each student reported for a round (pulled from the Worker)
    student_id INTEGER NOT NULL REFERENCES students(id),
    round_id   TEXT NOT NULL REFERENCES rounds(id),
    incorrect  TEXT,                           -- JSON lists of quiz numbers / reasons
    missed     TEXT,
    hardest    TEXT,
    reasons    TEXT,
    wishlist   TEXT,
    metaphor   TEXT,
    ts         TEXT NOT NULL,                  -- first submission (UTC)
    updated    TEXT NOT NULL,                  -- last edit (UTC)
    PRIMARY KEY (student_id, round_id)
);

CREATE TABLE IF NOT EXISTS class_bonus (  -- F7: class-wide points (AMP Live: >= 80 % of the class correct)
    id         INTEGER PRIMARY KEY,
    class_id   INTEGER NOT NULL REFERENCES classes(id),
    session_id INTEGER REFERENCES sessions(id),
    points     INTEGER NOT NULL CHECK (points > 0),
    source     TEXT NOT NULL,
    ref        TEXT NOT NULL,              -- e.g. live:<date>:<room>:<q>; one bonus per ref
    ts         TEXT NOT NULL,
    UNIQUE (source, ref)
);

CREATE TABLE IF NOT EXISTS checkins (     -- 출석 (F2)
    id         INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    ts         TEXT NOT NULL,
    source     TEXT NOT NULL CHECK (source IN ('phone', 'teacher')),
    UNIQUE (student_id, session_id)
);

CREATE TABLE IF NOT EXISTS submissions (
    id         INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    session_id INTEGER REFERENCES sessions(id),
    day        TEXT NOT NULL,             -- local date, Asia/Seoul
    ts         TEXT NOT NULL,             -- first upload of the day (ISO, UTC)
    photo_key  TEXT,
    source     TEXT NOT NULL CHECK (source IN ('phone', 'teacher', 'paper')),
    status     TEXT NOT NULL DEFAULT 'received' CHECK (status IN ('received', 'parsed', 'deleted')),
    reflection TEXT,                      -- F3: 오늘 한 줄 (0-40 chars, the student's own words)
    chip       TEXT,                      -- F3: ok | confused | rewatch
    solved     INTEGER,                   -- F13.5: 오늘 푼 문제 수 (self-reported 0-60, the teacher may correct)
    UNIQUE (student_id, day)
);

CREATE TABLE IF NOT EXISTS attitude (
    id         INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    points     INTEGER NOT NULL CHECK (points BETWEEN 0 AND 3),
    said_aloud INTEGER NOT NULL DEFAULT 0,
    ts         TEXT NOT NULL,
    UNIQUE (student_id, session_id)
);

CREATE TABLE IF NOT EXISTS tests (
    id         INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    date       TEXT NOT NULL,
    test_name  TEXT NOT NULL,
    n_quizzes  INTEGER,
    correct    INTEGER,
    wrong      INTEGER,
    blank      INTEGER,
    reach      REAL,
    efficiency REAL,
    persona    TEXT,
    top_reason TEXT,
    source     TEXT,
    UNIQUE (student_id, test_name, date)
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    week       TEXT NOT NULL,             -- ISO week 'YYYY-Www'
    text       TEXT NOT NULL,
    ts         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS effort (           -- P2: created empty
    id         INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id),
    set_id     TEXT,
    item_id    TEXT,
    attempted  INTEGER,
    correct    INTEGER,
    source     TEXT,
    ts         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY,
    type         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    ts           TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

-- The generator reads only this view: no full_name, parent_phone or ext_id.
CREATE VIEW IF NOT EXISTS students_public AS
    SELECT id, slug, display_name, class_id, track, student_token, parent_token, active, created_at
    FROM students;

CREATE INDEX IF NOT EXISTS ix_sub_day ON submissions(day);
CREATE INDEX IF NOT EXISTS ix_att_student ON attitude(student_id);
CREATE INDEX IF NOT EXISTS ix_tests_student ON tests(student_id, date);
CREATE INDEX IF NOT EXISTS ix_events_type ON events(type, id);
"""

PRIVATE_COLUMNS = ("full_name", "parent_phone", "ext_id")


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created by an older version (never drops data)."""
    if not is_initialized(conn):
        return
    added = {   # table -> {column: declaration} for columns that arrived after v1 (additive only)
        "sessions": {"start_time": "TEXT", "code": "TEXT", "cancelled": "TEXT"},
        "submissions": {"reflection": "TEXT", "chip": "TEXT", "solved": "INTEGER"},
        "classes": {"grade": "TEXT", "start_time": "TEXT", "end_time": "TEXT", "self_study_until": "TEXT", "late_min": "INTEGER DEFAULT 10",
                    "late_count": "INTEGER DEFAULT 3", "late_avg_min": "INTEGER DEFAULT 15"},
    }
    for table, new_cols in added.items():
        cols = _columns(conn, table)
        if not cols:          # the table does not exist yet: SCHEMA below creates it with every column
            continue
        for col, decl in new_cols.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.executescript(SCHEMA)   # creates any new tables (IF NOT EXISTS)
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init(conn: sqlite3.Connection) -> None:
    migrate(conn)
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def is_initialized(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='students'").fetchone()
    return row is not None


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_token() -> str:
    """128 random bits as lowercase base32 without padding: exactly 26 characters [a-z2-7]."""
    return base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=").lower()


def log_event(conn: sqlite3.Connection, type_: str, payload: dict, ts: str | None = None) -> None:
    conn.execute(
        "INSERT INTO events (type, payload_json, ts) VALUES (?, ?, ?)",
        (type_, json.dumps(payload, ensure_ascii=False, sort_keys=True), ts or now_utc_iso()),
    )


def last_event(conn: sqlite3.Connection, type_: str):
    row = conn.execute("SELECT * FROM events WHERE type = ? ORDER BY id DESC LIMIT 1", (type_,)).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"])
