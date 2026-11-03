import re
import sqlite3
from pathlib import Path

import pytest

from console import db as dbmod

TABLES = {"students", "classes", "sessions", "submissions", "attitude", "tests", "notes", "effort", "events"}


def test_schema_creates_tables_and_view(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(students)")}
    assert cols == {"id", "slug", "full_name", "display_name", "class_id", "track", "parent_phone", "student_token",
                    "parent_token", "active", "created_at", "ext_id"}
    view_cols = {r["name"] for r in conn.execute("PRAGMA table_info(students_public)")}
    assert view_cols and not (view_cols & {"full_name", "parent_phone", "ext_id"})
    sub_cols = [r["name"] for r in conn.execute("PRAGMA table_info(submissions)")]
    assert sub_cols == ["id", "student_id", "session_id", "day", "ts", "photo_key", "source", "status", "reflection", "chip", "solved"]
    cls_cols = {r["name"]: r["dflt_value"] for r in conn.execute("PRAGMA table_info(classes)")}
    assert cls_cols["goal_coef"] == "2.4" and cls_cols["milestone_fracs"] == "'0.33,0.67'"


def test_init_is_idempotent(conn):
    dbmod.init(conn)
    dbmod.init(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION


def test_events_are_append_only(conn):
    dbmod.log_event(conn, "x", {"a": 1})
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE events SET type = 'y'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM events")


def test_check_constraints(conn, klass):
    _, studs = klass
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO submissions (student_id, day, ts, source) VALUES (?, '2026-11-06', 'x', 'email')",
                     (studs[0]["id"],))


def test_generator_reads_only_the_public_view():
    """generate.py (and the weekly message) never name the students table or a private column."""
    for name in ("generate.py", "weekly.py"):
        src = (Path(__file__).resolve().parent.parent / "console" / name).read_text(encoding="utf-8")
        assert not re.search(r"\b(FROM|JOIN)\s+students\b(?!_public)", src), name
        for col in ("full_name", "parent_phone", "ext_id"):
            assert col not in src, (name, col)


def test_migrates_a_v1_database_without_losing_data(tmp_path):
    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.executescript("""
        CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, term TEXT, weekday_mask INTEGER NOT NULL DEFAULT 0,
            reward_label TEXT, reward_goal_points INTEGER, goal_coef REAL DEFAULT 2.4, milestone_fracs TEXT DEFAULT '0.33,0.67');
        CREATE TABLE students (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL, display_name TEXT NOT NULL,
            class_id INTEGER, track TEXT, parent_phone TEXT, student_token TEXT NOT NULL UNIQUE, parent_token TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, ext_id TEXT UNIQUE);
        CREATE TABLE sessions (id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL, date TEXT NOT NULL, set_id TEXT, topic TEXT, note TEXT,
            UNIQUE (class_id, date));
        INSERT INTO classes (name) VALUES ('B');
        INSERT INTO sessions (class_id, date) VALUES (1, '2026-11-02');
        PRAGMA user_version = 1;""")
    c.commit()
    c.close()
    conn = dbmod.connect(p)
    dbmod.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert {"start_time", "code"} <= cols
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'checkins'").fetchone()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
