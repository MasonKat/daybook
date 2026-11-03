"""F16: practice_mode. DAYBOOK runs in a Korean hagwon: `korea` is the default and keeps the original Korean design.
`manitoba` is the teacher's personal practice layer (Manitoba, Canada norms) and is opt-in only.

Rule for every item: when a Canadian norm is the opposite of normal Korean hagwon practice, it lives behind
practice_mode == "manitoba", never in the default. Examples: the projected board's "오늘 3점" names (korea: public praise
by name, as designed; manitoba: hidden), the comment lint (korea: privacy only; manitoba: absolutes, comparisons, hedges,
jargon) and the weekly-line drafts (korea: warm Korean; manitoba: strength / challenge / next step, English option).

The Teacher tab switch ("캐나다 연습 모드") stores the mode in the local database and logs every change in events;
daybook.json's "practice_mode" is only the starting value.
"""
from __future__ import annotations

import sqlite3

from .config import Config
from .db import log_event, now_utc_iso

MODES = ("korea", "manitoba")


def mode(conn: sqlite3.Connection, cfg: Config) -> str:
    r = conn.execute("SELECT value FROM settings WHERE key = 'practice_mode'").fetchone()
    m = r["value"] if r else str(cfg.extra.get("practice_mode") or "korea")
    return m if m in MODES else "korea"


def set_mode(conn: sqlite3.Connection, value: str) -> str:
    if value not in MODES:
        raise ValueError("practice_mode: korea | manitoba")
    conn.execute("""INSERT INTO settings (key, value, ts) VALUES ('practice_mode', ?, ?)
                    ON CONFLICT (key) DO UPDATE SET value = excluded.value, ts = excluded.ts""", (value, now_utc_iso()))
    log_event(conn, "practice_mode", {"mode": value})
    conn.commit()
    return value


def manitoba(conn: sqlite3.Connection, cfg: Config) -> bool:
    return mode(conn, cfg) == "manitoba"
