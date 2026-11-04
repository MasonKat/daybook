"""G4: printable 안내 cards. STANDING RULE: no printed paper ever carries a token, as text or as a QR. Links travel only
by the teacher's 1:1 KakaoTalk (the F11 안내 문구), so a lost card is never a lost link.

`cards students <class>`: one cut-out card per student (display name only). `cards parents <class>`: one A4 letter per
student, warm-formal, with the academy's own wording left as a placeholder for the teacher to fill; F17: what is recorded,
where it is kept, push, how to stop, and a tear-off consent slip. No name, token or QR is printed on it (every copy is
the same; the parent writes the student's name on the slip). The academy name comes from daybook.json "academy".
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import generate, ops
from .config import strings


def _students(conn: sqlite3.Connection, cls: sqlite3.Row) -> list[dict]:
    # display names only (students_public): no full name, phone, ID or token reaches a card
    return [{"display_name": r["display_name"]} for r in conn.execute(
        "SELECT display_name FROM students_public WHERE class_id = ? AND active = 1 ORDER BY id", (cls["id"],))]


def render(conn: sqlite3.Connection, kind: str, class_name: str, academy: str = "") -> str:
    if kind not in ("students", "parents"):
        raise ValueError("kind: students | parents")
    cls = ops.get_class(conn, class_name)
    S = strings()
    tpl = generate.env().get_template(f"cards_{kind}.html")
    return tpl.render(S=S, P=S["parent_msg"], cls=cls, students=_students(conn, cls), academy=academy)


def write(conn: sqlite3.Connection, kind: str, class_name: str, out_dir: str | Path = "cards", academy: str = "") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{class_name}-{kind}.html"
    path.write_text(render(conn, kind, class_name, academy), encoding="utf-8", newline="")
    return path
