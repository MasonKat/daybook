"""F16.4: weekly metrics, class level only (never one row per student). Both practice modes.

`daybook metrics weekly [--class c] [--weeks 8]` -> reports/metrics_<YYYY-Www>.csv, one row per week:
- late_rate: late 출석 (after the start + the class's late_min) per student-session; record_rate: student-sessions with a
  출석 or a submission;
- contact_coverage_4w: share of parents with >= 1 logged contact in the 4 weeks to the week's end;
  trigger_call_7d: of the 연락 필요 flags whose newest evidence fell in the week, the share followed by a call within
  7 days; positive_calls_per_student: calls tagged positive since the term started, per active student;
- retention: active students at the week's end vs the roster at the baseline date ("metrics_baseline", default the
  earliest term start);
- test_improved_share: share of students whose mean of their last 3 rounds > the mean of their first 3 (students with
  fewer than 6 rounds are counted in test_insufficient, never dropped silently);
- solved_mean (오늘 푼 문제 수 per reported session), weekly_lines (teacher notes), slc (student-led conferences).
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from . import contact as C
from . import models as M
from .config import Config

COLUMNS = ["week", "class", "students", "sessions", "student_sessions", "late_rate", "record_rate", "contact_coverage_4w",
           "trigger_call_7d", "positive_calls_per_student", "retention", "test_improved_share", "test_insufficient",
           "solved_mean", "weekly_lines", "slc"]


def _rate(a: float, b: float) -> str:
    return f"{a / b:.3f}" if b else ""


def _active_on(st: sqlite3.Row, d: date) -> bool:
    return st["created_at"][:10] <= d.isoformat() and (not st["left_on"] or st["left_on"] > d.isoformat()) and \
        (st["active"] or st["left_on"])


def _students(conn: sqlite3.Connection, class_ids: list[int]) -> list[sqlite3.Row]:
    q = f"SELECT * FROM students WHERE class_id IN ({','.join('?' * len(class_ids))})"
    return conn.execute(q, class_ids).fetchall()


def baseline(conn: sqlite3.Connection, cfg: Config, classes: list[sqlite3.Row]) -> date:
    if cfg.extra.get("metrics_baseline"):
        return date.fromisoformat(str(cfg.extra["metrics_baseline"]))
    starts = [M.parse_term(c["term"])[0] for c in classes if M.parse_term(c["term"])[0]]
    return min(starts) if starts else date(2000, 1, 1)


def week_row(conn: sqlite3.Connection, cfg: Config, classes: list[sqlite3.Row], mon: date, today: date) -> dict:
    sun = mon + timedelta(days=6)
    cut = min(sun, today)
    ids = [c["id"] for c in classes]
    studs = _students(conn, ids)
    active = [s for s in studs if _active_on(s, cut)]
    by_id = {s["id"]: s for s in studs}
    # sessions, lateness, records
    sessions = student_sessions = late = recorded = 0
    for cls in classes:
        pol = C.policy(cls)
        days = sorted(d for d in M.class_days(conn, cls, cut) if mon <= d <= cut)
        for d in days:
            sessions += 1
            sess = conn.execute("SELECT * FROM sessions WHERE class_id = ? AND date = ?", (cls["id"], d.isoformat())).fetchone()
            start = (sess["start_time"] if sess and sess["start_time"] else None) or pol["start"]
            for s in (x for x in studs if x["class_id"] == cls["id"] and _active_on(x, d)):
                student_sessions += 1
                ck = conn.execute("""SELECT c.ts FROM checkins c JOIN sessions se ON se.id = c.session_id
                                     WHERE c.student_id = ? AND se.class_id = ? AND se.date = ?""",
                                  (s["id"], cls["id"], d.isoformat())).fetchone()
                sub = conn.execute("SELECT 1 FROM submissions WHERE student_id = ? AND day = ? AND status != 'deleted'",
                                   (s["id"], d.isoformat())).fetchone()
                if ck or sub:
                    recorded += 1
                if ck and start:
                    t = M.utc_iso_to_kst(ck["ts"])
                    h, m = map(int, start.split(":"))
                    if (t.hour * 60 + t.minute) > h * 60 + m + pol["late_min"]:
                        late += 1
    # contacts
    since4 = cut - timedelta(days=27)
    contacts = conn.execute(f"SELECT student_id, ts, kind, tag FROM contacts WHERE student_id IN ({','.join('?' * len(studs)) or 'NULL'})",
                            [s["id"] for s in studs]).fetchall()
    cdays = [(r["student_id"], C._kst_day(r["ts"]), r["kind"], r["tag"]) for r in contacts]
    covered = {sid for sid, d, _, _ in cdays if since4 <= d <= cut}
    coverage = _rate(len([s for s in active if s["id"] in covered]), len(active))
    flagged = called = 0
    for cls in classes:
        for s in C.students(conn, cls["id"]):
            if s["id"] not in {a["id"] for a in active}:
                continue
            for f in C.rules_for(conn, cfg, s, cls, cut):
                if f["level"] != "need" or not (mon <= f["evidence"] <= cut):
                    continue
                flagged += 1
                if any(sid == s["id"] and k == "call" and f["evidence"] <= d <= f["evidence"] + timedelta(days=7)
                       for sid, d, k, _ in cdays):
                    called += 1
    term_start = baseline(conn, cfg, classes)
    positive = sum(1 for sid, d, k, tag in cdays if k == "call" and tag == "positive" and term_start <= d <= cut)
    # retention
    base_day = baseline(conn, cfg, classes)
    base = [s for s in studs if s["created_at"][:10] <= base_day.isoformat() and (not s["left_on"] or s["left_on"] > base_day.isoformat())]
    retained = [s for s in base if not s["left_on"] or s["left_on"] > cut.isoformat()]
    # tests: own first 3 vs own last 3 rounds
    improved = sufficient = insufficient = 0
    for s in active:
        eff = [r[0] for r in conn.execute("SELECT efficiency FROM tests WHERE student_id = ? AND efficiency IS NOT NULL AND date <= ? ORDER BY date",
                                          (s["id"], cut.isoformat()))]
        if len(eff) < 6:
            insufficient += 1
            continue
        sufficient += 1
        if sum(eff[-3:]) / 3 > sum(eff[:3]) / 3:
            improved += 1
    solved = [r[0] for r in conn.execute(f"""SELECT solved FROM submissions WHERE solved IS NOT NULL AND status != 'deleted'
                                             AND day BETWEEN ? AND ? AND student_id IN ({','.join('?' * len(studs)) or 'NULL'})""",
                                         [mon.isoformat(), cut.isoformat(), *[s["id"] for s in studs]])]
    week = M.iso_week(mon)
    lines = conn.execute(f"SELECT COUNT(*) FROM notes WHERE week = ? AND student_id IN ({','.join('?' * len(studs)) or 'NULL'})",
                         [week, *[s["id"] for s in studs]]).fetchone()[0]
    slc = conn.execute(f"""SELECT COUNT(*) FROM meetings WHERE kind = 'slc' AND day BETWEEN ? AND ?
                           AND student_id IN ({','.join('?' * len(studs)) or 'NULL'})""",
                       [mon.isoformat(), cut.isoformat(), *[s["id"] for s in studs]]).fetchone()[0]
    return {"week": week, "class": ",".join(c["name"] for c in classes), "students": len(active), "sessions": sessions,
            "student_sessions": student_sessions, "late_rate": _rate(late, student_sessions),
            "record_rate": _rate(recorded, student_sessions), "contact_coverage_4w": coverage,
            "trigger_call_7d": _rate(called, flagged), "positive_calls_per_student": _rate(positive, len(active)),
            "retention": _rate(len(retained), len(base)), "test_improved_share": _rate(improved, sufficient),
            "test_insufficient": insufficient, "solved_mean": f"{sum(solved) / len(solved):.1f}" if solved else "",
            "weekly_lines": lines, "slc": slc}


def weekly(conn: sqlite3.Connection, cfg: Config, class_name: str | None = None, weeks: int = 8,
           today: date | None = None) -> list[dict]:
    from .ops import get_class
    today = today or M.kst_today()
    classes = [get_class(conn, class_name)] if class_name else conn.execute("SELECT * FROM classes ORDER BY id").fetchall()
    if not classes:
        return []
    mon = M.week_bounds(today)[0]
    return [week_row(conn, cfg, classes, mon - timedelta(weeks=k), today) for k in range(weeks - 1, -1, -1)]


def write_csv(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    return path


def log_meeting(conn: sqlite3.Connection, student_id: int, kind: str, day: date) -> None:
    from .db import log_event, now_utc_iso
    if kind not in ("slc", "other"):
        raise ValueError("kind: slc | other")
    conn.execute("INSERT INTO meetings (student_id, day, kind, ts) VALUES (?, ?, ?, ?)", (student_id, day.isoformat(), kind, now_utc_iso()))
    log_event(conn, "meeting", {"student_id": student_id, "kind": kind, "day": day.isoformat()})
