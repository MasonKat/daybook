"""F15.1 / F15.2: what the class watched (our own watch signals, pulled into `watches`).

Pages only ever get a student's own count and, per class, the one most-watched video ("인기"), never a name or a
per-student number of anyone else. The per-student detail is for the Teacher tab (laptop only).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

WATCHED_SEC = 10            # a record counts as "watched" from 10 seconds on
POPULAR_MIN_STUDENTS = 3    # the 인기 badge needs at least this many different students of the class


def watched_ids(conn: sqlite3.Connection, student_id: int, start: date, end: date) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT video_id FROM watches WHERE student_id = ? AND day BETWEEN ? AND ? AND sec >= ?",
        (student_id, start.isoformat(), end.isoformat(), WATCHED_SEC))}


def class_stats(conn: sqlite3.Connection, class_id: int, start: date, end: date) -> dict[str, dict]:
    """video_id -> {students, minutes, avg_pct} for the active students of one class over [start, end]."""
    out = {}
    for r in conn.execute("""SELECT w.video_id, COUNT(DISTINCT w.student_id) AS n, SUM(w.sec) AS sec, AVG(w.pct) AS pct
                             FROM watches w JOIN students_public s ON s.id = w.student_id
                             WHERE s.class_id = ? AND s.active = 1 AND w.day BETWEEN ? AND ? AND w.sec >= ?
                             GROUP BY w.video_id""", (class_id, start.isoformat(), end.isoformat(), WATCHED_SEC)):
        out[r["video_id"]] = {"students": r["n"], "minutes": round((r["sec"] or 0) / 60, 1), "avg_pct": round(r["pct"] or 0)}
    return out


def popular(conn: sqlite3.Connection, class_id: int, today: date, days: int = 7) -> str | None:
    """F15.2 "이번 주 인기 영상": the class's top video by students who watched, then watch minutes, over the last
    `days` days; None unless at least POPULAR_MIN_STUDENTS different students watched it."""
    stats = class_stats(conn, class_id, today - timedelta(days=days - 1), today)
    ranked = sorted(stats.items(), key=lambda kv: (-kv[1]["students"], -kv[1]["minutes"], kv[0]))
    if ranked and ranked[0][1]["students"] >= POPULAR_MIN_STUDENTS:
        return ranked[0][0]
    return None


def teacher_view(conn: sqlite3.Connection, videos: list[dict], today: date, days: int = 7) -> dict:
    """Teacher tab 영상: per class and video (students opened, avg %, minutes; views / views_7d from videos.json when
    the local agent fills them), and per student this week's videos. Laptop only."""
    start = today - timedelta(days=days - 1)
    by_id = {v["id"]: v for v in videos}
    classes = []
    for c in conn.execute("SELECT id, name FROM classes ORDER BY id"):
        n_active = conn.execute("SELECT COUNT(*) FROM students_public WHERE class_id = ? AND active = 1", (c["id"],)).fetchone()[0]
        stats = class_stats(conn, c["id"], start, today)
        rows = []
        for vid, st in sorted(stats.items(), key=lambda kv: (-kv[1]["students"], -kv[1]["minutes"])):
            v = by_id.get(vid, {})
            rows.append({"id": vid, "title": v.get("title") or vid, "kind": v.get("kind", ""), **st,
                         "views": v.get("views"), "views_7d": v.get("views_7d")})
        students = []
        for s in conn.execute("SELECT id, display_name FROM students_public WHERE class_id = ? AND active = 1 ORDER BY id", (c["id"],)):
            ids = watched_ids(conn, s["id"], start, today)
            students.append({"display_name": s["display_name"], "n": len(ids),
                             "titles": [by_id.get(i, {}).get("title") or i for i in sorted(ids)]})
        classes.append({"name": c["name"], "active": n_active, "videos": rows, "students": students,
                        "popular": popular(conn, c["id"], today, days)})
    return {"days": days, "from": start.isoformat(), "to": today.isoformat(), "classes": classes}


def report_text(view: dict) -> str:
    """The 2주 리포트 as plain text for the clipboard (video titles and class numbers only, no student)."""
    lines = [f"영상 리포트 {view['from']} ~ {view['to']}"]
    for c in view["classes"]:
        lines.append(f"\n[{c['name']}] (재원 {c['active']}명)")
        if not c["videos"]:
            lines.append("  시청 기록 없음")
        for v in c["videos"]:
            extra = f" · 전체 조회 {v['views_7d']:,} (7일)" if isinstance(v.get("views_7d"), int) else ""
            lines.append(f"  {v['title']}: {v['students']}명 · 평균 {v['avg_pct']}% · {v['minutes']}분{extra}")
    return "\n".join(lines)
