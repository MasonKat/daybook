"""F14: weekly-test rounds (주간 테스트). Students report each round on their own page; the laptop gets it with `pull`.

The teacher's round id (e.g. Adaptive_Screeners_1S_Nov06_G10) never leaves the laptop: the Worker and the pages only see
`rid`, a hash of it, and the student-facing label. `export reports` writes exactly the columns of the teacher's Notion
"Screeners Tracker", so the local agent can sync it from the laptop (nothing here talks to Notion).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import models as M
from .config import Config
from .db import KST, log_event

ROUND_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{3,80}$")
WHEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
GRADES = ("1학년", "2학년", "3학년")
REASONS = ("개념탈락", "계산실수", "정보이해부족", "시간부족")
EXPORT_COLUMNS = ["Student ID", "Name", "Grade", "Round ID", "Screening Date", "Incorrect Quiz #", "Missed Quiz #",
                  "Most Challenging Quiz", "Reasoning", "Wishlist", "metaphor"]
KEEP_DAYS = 14           # closed rounds stay on the Worker this long (the pull window), like every other record


class RoundError(ValueError):
    pass


def rid(round_id: str) -> str:
    return hashlib.sha256(f"round:{round_id}".encode("utf-8")).hexdigest()[:16]


def _when(s: str) -> datetime:
    s = (s or "").strip().replace("T", " ")
    if not WHEN_RE.match(s):
        raise RoundError("times look like 2026-11-06 20:00")
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=KST)


def _class_ids(conn: sqlite3.Connection, names) -> list[int]:
    if isinstance(names, str):
        names = [n.strip() for n in names.split(",")]
    ids = []
    for n in names or []:
        if not str(n).strip():
            continue
        r = conn.execute("SELECT id FROM classes WHERE name = ? OR id = ?", (str(n).strip(), str(n).strip())).fetchone()
        if not r:
            raise RoundError(f"no class named {n!r}")
        if r["id"] not in ids:
            ids.append(r["id"])
    if not ids:
        raise RoundError("a round needs at least one class")
    return ids


def default_label(open_at: datetime) -> str:
    return f"{open_at.month}/{open_at.day} 주간 테스트"


def get(conn: sqlite3.Connection, round_id: str) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM rounds WHERE id = ?", (round_id,)).fetchone()
    if not r:
        raise RoundError(f"no round {round_id!r} (see `daybook round list`)")
    return r


def _check(round_id, grade, n, t0, t1, label):
    if not ROUND_ID_RE.match(round_id or ""):
        raise RoundError("round id: letters, digits, _ and - (e.g. Adaptive_Screeners_1S_Nov06_G10)")
    if grade and grade not in GRADES:
        raise RoundError("grade: 1학년 | 2학년 | 3학년")
    if not isinstance(n, int) or not 1 <= n <= 60:
        raise RoundError("quizzes: 1-60")
    if t1 <= t0:
        raise RoundError("the round must close after it opens")
    if label is not None and (not label.strip() or len(label) > 40 or ROUND_ID_RE.match(label.strip()) and "_" in label):
        raise RoundError("the label is what students see, e.g. 11/6 주간 테스트 (not the round id)")


def add(conn: sqlite3.Connection, round_id: str, grade: str | None, classes, n: int, open_at: str, close_at: str,
        label: str | None = None) -> sqlite3.Row:
    if conn.execute("SELECT 1 FROM rounds WHERE id = ?", (round_id,)).fetchone():
        raise RoundError(f"round {round_id!r} exists (use edit)")
    t0, t1 = _when(open_at), _when(close_at)
    _check(round_id, grade, n, t0, t1, label)
    ids = _class_ids(conn, classes)
    conn.execute("INSERT INTO rounds (id, rid, grade, classes, n, open_at, close_at, label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (round_id, rid(round_id), grade or None, ",".join(map(str, ids)), n, t0.strftime("%Y-%m-%d %H:%M"),
                  t1.strftime("%Y-%m-%d %H:%M"), (label or default_label(t0)).strip()))
    log_event(conn, "round_add", {"round": round_id, "classes": ids, "n": n})
    return get(conn, round_id)


def edit(conn: sqlite3.Connection, round_id: str, **kw) -> sqlite3.Row:
    cur = get(conn, round_id)
    grade = kw.get("grade") if kw.get("grade") is not None else cur["grade"]
    n = int(kw["n"]) if kw.get("n") not in (None, "") else cur["n"]
    t0 = _when(kw["open_at"]) if kw.get("open_at") else _when(cur["open_at"])
    t1 = _when(kw["close_at"]) if kw.get("close_at") else _when(cur["close_at"])
    label = kw["label"] if kw.get("label") else cur["label"]
    _check(round_id, grade, n, t0, t1, label)
    ids = _class_ids(conn, kw["classes"]) if kw.get("classes") else [int(x) for x in cur["classes"].split(",")]
    conn.execute("UPDATE rounds SET grade = ?, classes = ?, n = ?, open_at = ?, close_at = ?, label = ? WHERE id = ?",
                 (grade or None, ",".join(map(str, ids)), n, t0.strftime("%Y-%m-%d %H:%M"), t1.strftime("%Y-%m-%d %H:%M"),
                  label.strip(), round_id))
    log_event(conn, "round_edit", {"round": round_id, "fields": sorted(k for k, v in kw.items() if v not in (None, ""))})
    return get(conn, round_id)


def close_now(conn: sqlite3.Connection, round_id: str, now: datetime | None = None) -> sqlite3.Row:
    cur = get(conn, round_id)
    now = (now or M.kst_now()).astimezone(KST)
    t0 = _when(cur["open_at"])
    end = max(now, t0 + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    conn.execute("UPDATE rounds SET close_at = ? WHERE id = ?", (end, round_id))
    log_event(conn, "round_close", {"round": round_id, "at": end})
    return get(conn, round_id)


def is_open(r: sqlite3.Row, now: datetime | None = None) -> bool:
    now = now or M.kst_now()
    return _when(r["open_at"]) <= now <= _when(r["close_at"])


def class_ids(r: sqlite3.Row) -> list[int]:
    return [int(x) for x in r["classes"].split(",") if x]


def worker_payload(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    """Rounds still useful to the Worker (open, upcoming or closed within KEEP_DAYS): hashes, class keys, N, window."""
    from .ops import class_key
    now = now or M.kst_now()
    out = []
    for r in conn.execute("SELECT * FROM rounds ORDER BY open_at"):
        if _when(r["close_at"]) < now - timedelta(days=KEEP_DAYS):
            continue
        out.append({"rid": r["rid"], "classes": [class_key(c) for c in class_ids(r)], "n": r["n"], "label": r["label"],
                    "open_from": _when(r["open_at"]).astimezone(timezone.utc).isoformat(),
                    "open_until": _when(r["close_at"]).astimezone(timezone.utc).isoformat()})
    return out


def push(conn: sqlite3.Connection, cfg: Config) -> str:
    """Send the rounds (never raises: a round change must not depend on the network; the next publish resends)."""
    from .pull import PullError, http_put_json
    if not cfg.worker or not cfg.teacher_token:
        return ""
    try:
        r = http_put_json(cfg, "/rounds", {"rounds": worker_payload(conn)})
        return f"Worker: {r.get('n', '?')} weekly-test round(s) accepted"
    except PullError as e:
        return f"warning: rounds not sent to the Worker ({e}); students see the round after the next publish"


def apply_reports(conn: sqlite3.Connection, items: list[dict]) -> tuple[int, int]:
    """Pulled reports -> test_reports (keyed student + round; the latest edit wins). Returns (new, updated)."""
    from .pull import _token_map
    tokens = _token_map(conn)
    by_rid = {r["rid"]: r["id"] for r in conn.execute("SELECT id, rid FROM rounds")}
    new = upd = 0

    def nums(v):
        return [int(x) for x in str(v or "").split(",") if x.strip().isdigit()]

    for it in items:
        sid, round_id = tokens.get(it.get("token", "")), by_rid.get(it.get("rid", ""))
        if sid is None or round_id is None:
            continue
        reasons = [x for x in str(it.get("reasons") or "").split(",") if x in REASONS]
        row = (json.dumps(nums(it.get("incorrect"))), json.dumps(nums(it.get("missed"))), json.dumps(nums(it.get("hardest"))),
               json.dumps(reasons, ensure_ascii=False), (it.get("wishlist") or "")[:100] or None,
               (it.get("metaphor") or "")[:30] or None)
        cur = conn.execute("SELECT updated FROM test_reports WHERE student_id = ? AND round_id = ?", (sid, round_id)).fetchone()
        if cur is None:
            conn.execute("""INSERT INTO test_reports (student_id, round_id, incorrect, missed, hardest, reasons, wishlist, metaphor,
                            ts, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (sid, round_id, *row, it["ts"], it["updated"]))
            log_event(conn, "test_report", {"student_id": sid, "round": round_id})
            new += 1
        elif cur["updated"] < it["updated"]:
            conn.execute("""UPDATE test_reports SET incorrect = ?, missed = ?, hardest = ?, reasons = ?, wishlist = ?, metaphor = ?,
                            updated = ? WHERE student_id = ? AND round_id = ?""", (*row, it["updated"], sid, round_id))
            log_event(conn, "test_report_edit", {"student_id": sid, "round": round_id})
            upd += 1
    return new, upd


def progress(conn: sqlite3.Connection, round_id: str) -> dict:
    """Teacher tab: who reported (a ✓ per student, never the answers on a projected screen)."""
    r = get(conn, round_id)
    ids = class_ids(r)
    q = f"""SELECT s.id, s.slug, s.display_name, c.name AS class_name, t.updated FROM students_public s
            JOIN classes c ON c.id = s.class_id
            LEFT JOIN test_reports t ON t.student_id = s.id AND t.round_id = ?
            WHERE s.active = 1 AND s.class_id IN ({",".join("?" * len(ids))}) ORDER BY s.class_id, s.id"""
    rows = [{"slug": x["slug"], "display_name": x["display_name"], "class_name": x["class_name"],
             "done": bool(x["updated"]), "at": M.utc_iso_to_kst(x["updated"]).strftime("%m/%d %H:%M") if x["updated"] else ""}
            for x in conn.execute(q, (round_id, *ids))]
    return {"round": round_id, "label": r["label"], "n": r["n"], "done": sum(x["done"] for x in rows), "total": len(rows),
            "students": rows}


def export_rows(conn: sqlite3.Connection, round_id: str) -> list[dict]:
    """One row per student who reported, with exactly the teacher's Notion columns (lists stay lists here)."""
    r = get(conn, round_id)
    out = []
    for x in conn.execute("""SELECT t.*, s.ext_id, s.full_name FROM test_reports t JOIN students s ON s.id = t.student_id
                             WHERE t.round_id = ? ORDER BY s.class_id, s.id""", (round_id,)):
        out.append({"Student ID": x["ext_id"] or "", "Name": x["full_name"], "Grade": r["grade"] or "", "Round ID": round_id,
                    "Screening Date": r["open_at"][:10], "Incorrect Quiz #": json.loads(x["incorrect"] or "[]"),
                    "Missed Quiz #": json.loads(x["missed"] or "[]"), "Most Challenging Quiz": json.loads(x["hardest"] or "[]"),
                    "Reasoning": json.loads(x["reasons"] or "[]"), "Wishlist": x["wishlist"] or "", "metaphor": x["metaphor"] or ""})
    return out


def export(conn: sqlite3.Connection, round_id: str, fmt: str = "csv", out_dir: str | Path = "reports") -> Path:
    """reports/<round>.csv (lists comma-joined) or reports/<round>.json (notion-json: lists stay lists). Gitignored."""
    rows = export_rows(conn, round_id)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: ",".join(map(str, v)) if isinstance(v, list) else v for k, v in row.items()})
        path = out / f"{round_id}.csv"
        # Excel on Windows reads Korean with the BOM; newline="" keeps "\n" so the bytes are the same on every OS
        path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    elif fmt == "notion-json":
        path = out / f"{round_id}.json"
        path.write_text(json.dumps({"round": round_id, "columns": EXPORT_COLUMNS, "rows": rows}, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8", newline="")
    else:
        raise RoundError("format: csv | notion-json")
    log_event(conn, "reports_export", {"round": round_id, "rows": len(rows), "format": fmt})
    return path
