"""F5: end-of-term one-page report (print-ready HTML; the teacher prints to PDF in the browser) and draft notes."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import generate, models as M
from .config import Config


def _student_ctx(conn: sqlite3.Connection, cfg: Config, student_id: int, today: date) -> tuple[dict, dict]:
    ctx = generate.build_context(conn, cfg, today, M.kst_now())
    conn.rollback()                       # build_context may freeze a goal; a report must not write
    me = next((s for s in ctx["students"] if s["id"] == student_id), None)
    if me is None:
        raise ValueError("this student is not active")
    return ctx, me


def term_report(conn: sqlite3.Connection, cfg: Config, student_id: int, today: date) -> str:
    ctx, me = _student_ctx(conn, cfg, student_id, today)
    st = conn.execute("SELECT * FROM students_public WHERE id = ?", (student_id,)).fetchone()
    cls = conn.execute("SELECT * FROM classes WHERE id = ?", (st["class_id"],)).fetchone()
    t_start, t_end = M.parse_term(cls["term"])
    start = t_start or today - timedelta(days=120)
    cd = {d for d in M.class_days(conn, cls, today) if d >= start}
    subs = {date.fromisoformat(r["day"]) for r in conn.execute(
        "SELECT day FROM submissions WHERE student_id = ? AND status != 'deleted'", (student_id,))}
    done_c = len(cd & subs)
    best, run, d = 0, 0, start          # longest streak in the term (same rule as models.streak)
    while d <= today:
        if d in subs:
            run += 1
        elif d in cd and d != today:
            run = 0
        best = max(best, run)
        d += timedelta(days=1)
    moments = conn.execute("""SELECT COUNT(*) FROM moments m JOIN sessions se ON se.id = m.session_id
                              WHERE m.class_id = ? AND se.date BETWEEN ? AND ?""",
                           (cls["id"], start.isoformat(), today.isoformat())).fetchone()[0]
    trend = next((w["trend"] for w in reversed(me["room"]) if w["trend"]), None)
    # F13.1: 연락 기록 (dates, kinds and the one-line summaries; the report is the teacher's print, never published)
    contacts = [{"date": M.utc_iso_to_kst(r["ts"]).date().isoformat(), "kind": r["kind"], "direction": r["direction"],
                 "summary": r["summary"] or "", "follow_up": r["follow_up"]}
                for r in conn.execute("SELECT * FROM contacts WHERE student_id = ? ORDER BY ts", (student_id,))
                if M.utc_iso_to_kst(r["ts"]).date() >= start]
    tpl = generate.env().get_template("report.html")
    return tpl.render(S=ctx["S"], me=me, today=today.isoformat(), best=best, done_c=done_c, class_c=len(cd),
                      rate=round(100 * done_c / len(cd)) if cd else 0, moments=moments, trend=trend,
                      term=f"{start.isoformat()} ~ {(t_end or today).isoformat()}", contacts=contacts)


def suggest_notes(conn: sqlite3.Connection, cfg: Config, cls: sqlite3.Row, day: date,
                  include_existing: bool = False) -> list[tuple[str, str, str]]:
    """[(slug, week, draft)] for active students of the class without a note for that week yet
    (with include_existing, for every active student: the Teacher tab prefills its fields with them)."""
    from . import comments as C, practice
    mode = practice.mode(conn, cfg)
    lang = "en" if mode == "manitoba" and cfg.extra.get("comment_lang") == "en" else "ko"
    week = M.iso_week(day)
    ctx = generate.build_context(conn, cfg, day, M.kst_now())
    conn.rollback()
    by_id = {s["id"]: s for s in ctx["students"]}
    out = []
    for r in conn.execute("SELECT id, slug FROM students_public WHERE class_id = ? AND active = 1 ORDER BY id", (cls["id"],)):
        if not include_existing and conn.execute("SELECT 1 FROM notes WHERE student_id = ? AND week = ?",
                                                 (r["id"], week)).fetchone():
            continue                      # the teacher already wrote one: skip
        s = by_id.get(r["id"])
        if not s:
            continue
        w = s["room"][-1]
        sessions = conn.execute("SELECT COUNT(*) FROM sessions WHERE class_id = ? AND cancelled IS NULL AND date BETWEEN ? AND ?",
                                (cls["id"], M.week_bounds(day)[0].isoformat(), day.isoformat())).fetchone()[0]
        # F16.3: korea: a warm Korean line; manitoba practice: strength / challenge / next step (English option)
        out.append((r["slug"], week, C.draft(w, s["streak"], sessions, mode, lang)))
    return out
