"""F21.2 수업 문제 and the sub-concept skill map (내 실력 지도).

In class the teacher opens a set (board, remote or `classwork open`): a label and N quizzes. Right after the answers are
revealed, each student marks ○ / ✕ / – per quiz on the phone (the weekly test's 5-per-row grid); students solve in their own
books, so no quiz text, answer or key exists anywhere in DAYBOOK. A set closes at the end of the class day.

Sub-concept tags live only on this laptop, in `classwork_tags.json` (path: daybook.json "classwork_tags"):

    {"sets": {"쎈 이차방정식 A": {"1": "이차방정식/판별식", "2": "이차방정식/근과 계수의 관계"},
              "Adaptive_Screeners_1S_Nov06_G10": {"3": "부등식/산술·기하평균"}}}

A set's label (or a weekly-test round id) picks its tags; "단원/태그" groups tags by 단원. The Worker never sees a tag.
The map counts ○, ✕ and – separately (– = 시간 부족 / 못 품, never merged into ✕); correct rate = ○ / (○ + ✕); fewer than
3 attempts = 데이터 부족. Classwork and tagged weekly-test rounds add up. Untagged quizzes still count in the totals.
The student's own page shows the map; the parent page only with students.share_classwork = yes (both agreed).
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import models as M
from .config import ROOT, Config
from .db import log_event

MARKS = ("o", "x", "m", "p")         # ○ ✕ – and △ (부분, 서술형); – is never ✕
SELF_MARKS = ("o", "x", "m")
SYMBOL = {"○": "o", "o": "o", "O": "o", "✕": "x", "x": "x", "X": "x", "×": "x", "–": "m", "-": "m", "m": "m", "△": "p", "p": "p"}
MIN_ATTEMPTS = 3
STRONG = 0.7


class ClassworkError(ValueError):
    pass


def tags_path(cfg: Config) -> Path:
    p = Path(str(cfg.extra.get("classwork_tags") or "classwork_tags.json"))
    if p.is_absolute():
        return p
    return p if p.exists() else ROOT / p


def load_tags(cfg: Config) -> dict[str, dict[int, str]]:
    p = tags_path(cfg)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise ClassworkError(f"{p.name}: not valid JSON ({e})") from e
    out = {}
    for label, quizzes in (data.get("sets") or {}).items():
        if isinstance(quizzes, dict):
            out[str(label)] = {int(k): str(v).strip()[:30] for k, v in quizzes.items() if str(k).isdigit() and str(v).strip()}
    return out


def split_tag(t: str) -> tuple[str, str]:
    unit, _, tag = t.partition("/")
    return (unit.strip(), tag.strip()) if tag else ("기타", unit.strip())


# ------------------------------------------------------------ open, pull

def open_set(conn: sqlite3.Connection, cfg: Config, class_id: int, label: str, n: int | None = None) -> dict:
    """Opens a set on the Worker (label and N only) and records it here. N defaults to the tag file's last quiz number."""
    from .ops import class_key
    from .pull import PullError, http_post_json
    label = " ".join((label or "").split())[:40]
    if not label:
        raise ClassworkError("a label, e.g. 쎈 이차방정식 A")
    tags = load_tags(cfg).get(label, {})
    n = n or (max(tags) if tags else 0)
    if not 1 <= int(n) <= 40:
        raise ClassworkError("N: 1-40 quizzes")
    try:
        r = http_post_json(cfg, "/classwork-open", {"class": class_key(class_id), "label": label, "n": int(n)})
    except PullError as e:
        raise ClassworkError(f"not opened ({e})") from e
    if not r.get("ok"):
        raise ClassworkError(f"not opened ({r.get('error')})")
    conn.execute("INSERT OR IGNORE INTO classwork_sets (sid, class_id, day, label, n, ts) VALUES (?, ?, ?, ?, ?, ?)",
                 (r["sid"], class_id, M.kst_today().isoformat(), label, int(n), r.get("until", "")))
    log_event(conn, "classwork_open", {"sid": r["sid"], "class_id": class_id, "n": int(n), "tagged": len(tags)})
    conn.commit()
    return {"sid": r["sid"], "label": label, "n": int(n), "tagged": len(tags)}


def apply_pull(conn: sqlite3.Connection, body: dict) -> tuple[int, int]:
    from .ops import class_key
    from .pull import _token_map
    keys = {class_key(r[0]): r[0] for r in conn.execute("SELECT id FROM classes")}
    sets = marks = 0
    for st in body.get("sets", []):
        cid = keys.get(str(st.get("class", "")))
        if cid is None or not str(st.get("sid", "")):
            continue
        day = M.utc_iso_to_kst(st["open_from"]).date().isoformat()
        cur = conn.execute("INSERT OR IGNORE INTO classwork_sets (sid, class_id, day, label, n, ts) VALUES (?, ?, ?, ?, ?, ?)",
                           (st["sid"], cid, day, str(st.get("label", ""))[:40], int(st["n"]), st.get("created", "")))
        sets += cur.rowcount
    tokens = _token_map(conn)
    for mk in body.get("marks", []):
        sid, student = str(mk.get("sid", "")), tokens.get(str(mk.get("token", "")))
        row = conn.execute("SELECT n FROM classwork_sets WHERE sid = ?", (sid,)).fetchone()
        m = str(mk.get("marks", ""))
        if student is None or not row or len(m) != row[0] or set(m) - set(SELF_MARKS):
            continue
        conn.execute("""INSERT INTO classwork_marks (sid, student_id, marks, updated) VALUES (?, ?, ?, ?)
                        ON CONFLICT (sid, student_id) DO UPDATE SET marks = excluded.marks, updated = excluded.updated
                        WHERE excluded.updated >= classwork_marks.updated""", (sid, student, m, mk.get("updated", "")))
        marks += 1
    if sets or marks:
        log_event(conn, "classwork_pull", {"sets": sets, "marks": marks})
    return sets, marks


# ------------------------------------------------------------ the map

def _records(conn: sqlite3.Connection, tags: dict, student_id: int) -> tuple[list[tuple[str | None, str]], dict]:
    """(tag or None, mark) per quiz, and the totals. Per quiz the teacher's mark (incl. 서술형) wins, then the student's own
    self-mark, then an accepted photo mark; tagged weekly-test rounds add their quizzes to the tags (not to the totals)."""
    q: dict[tuple, dict] = {}
    for r in conn.execute("""SELECT s.day, s.label, m.marks FROM classwork_marks m JOIN classwork_sets s ON s.sid = m.sid
                             WHERE m.student_id = ?""", (student_id,)):
        for i, ch in enumerate(r["marks"], 1):
            q.setdefault((r["day"], r["label"], i), {})["self"] = ch
    for r in conn.execute("SELECT * FROM quiz_marks WHERE student_id = ? AND status = 'ok'", (student_id,)):
        slot = q.setdefault((r["day"], r["label"], r["quiz"]), {})
        slot["teacher" if r["source"] in ("teacher", "essay") else "photo"] = r["mark"]
        if r["tags"]:
            slot["tags"] = json.loads(r["tags"])
    recs: list[tuple[str | None, str]] = []
    totals = {k: 0 for k in MARKS}
    for (day, label, quiz), slot in q.items():
        mark = slot.get("teacher") or slot.get("self") or slot.get("photo")
        if not mark:
            continue
        totals[mark] += 1
        ts = slot.get("tags") or ([tags[label][quiz]] if quiz in tags.get(label, {}) else [None])
        recs += [(t, mark) for t in ts]
    for r in conn.execute("""SELECT t.round_id, t.incorrect, t.missed, r.n FROM test_reports t JOIN rounds r ON r.id = t.round_id
                             WHERE t.student_id = ?""", (student_id,)):
        t = tags.get(r["round_id"])
        if not t:
            continue
        inc, mis = set(json.loads(r["incorrect"] or "[]")), set(json.loads(r["missed"] or "[]"))
        for i in range(1, r["n"] + 1):
            if i in t:
                recs.append((t[i], "m" if i in mis else "x" if i in inc else "o"))
    return recs, totals


def _status(o: int, x: int, m: int, p: int = 0) -> tuple[float | None, str]:
    """rate = (○ + ½△) / (○ + △ + ✕); – stays out of the rate (shown on its own); fewer than 3 attempts = 데이터 부족."""
    rate = (o + 0.5 * p) / (o + x + p) if o + x + p else None
    if o + x + m + p < MIN_ATTEMPTS:
        return rate, "few"
    return rate, "strong" if rate is not None and rate >= STRONG else "work"


def skill_map(conn: sqlite3.Connection, cfg: Config, student_id: int, tags: dict | None = None) -> dict:
    tags = load_tags(cfg) if tags is None else tags
    recs, totals = _records(conn, tags, student_id)
    by: dict[str, dict] = {}
    for tag, ch in recs:
        if not tag:
            continue
        c = by.setdefault(tag, {k: 0 for k in MARKS})
        c[ch] += 1
    units: dict[str, list] = {}
    for tag, c in sorted(by.items()):
        unit, name = split_tag(tag)
        rate, st = _status(c["o"], c["x"], c["m"], c["p"])
        units.setdefault(unit, []).append({"tag": name, "full": tag, **c, "rate": None if rate is None else round(100 * rate),
                                           "status": st})
    return {"totals": totals, "units": [{"unit": u, "tags": ts} for u, ts in sorted(units.items(), key=lambda kv: (kv[0] == "기타", kv[0]))],
            "any": bool(by) or any(totals.values())}


def class_grid(conn: sqlite3.Connection, cfg: Config, class_id: int) -> dict:
    """Teacher tab (teacher only): tag × student counts for one class."""
    tags = load_tags(cfg)
    studs = conn.execute("SELECT id, display_name FROM students_public WHERE class_id = ? AND active = 1 ORDER BY id", (class_id,)).fetchall()
    rows, all_tags = [], set()
    for s in studs:
        sm = skill_map(conn, cfg, s["id"], tags)
        cells = {t["full"]: {k: t[k] for k in ("o", "x", "m", "p", "rate", "status")} for u in sm["units"] for t in u["tags"]}
        all_tags |= set(cells)
        rows.append({"display_name": s["display_name"], "totals": sm["totals"], "cells": cells})
    return {"tags": sorted(all_tags), "students": rows}


def export_csv(conn: sqlite3.Connection, cfg: Config, class_id: int, class_name: str, out_dir: str | Path = "reports") -> Path:
    g = class_grid(conn, cfg, class_id)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["student", "단원", "tag", "o", "x", "m", "p", "rate"])
    for s in g["students"]:
        for t in g["tags"]:
            c = s["cells"].get(t)
            if c:
                unit, name = split_tag(t)
                w.writerow([s["display_name"], unit, name, c["o"], c["x"], c["m"], c["p"], "" if c["rate"] is None else c["rate"]])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"skills_{class_name}_{date.today().isoformat()}.csv"
    path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    log_event(conn, "skills_export", {"class_id": class_id, "rows": sum(len(s["cells"]) for s in g["students"])})
    return path


def set_names(cfg: Config) -> list[str]:
    """For the board and the remote: the set labels in the tag file (round ids are left out)."""
    from .rounds import ROUND_ID_RE
    return [k for k in load_tags(cfg) if not (ROUND_ID_RE.match(k) and "_" in k)]


def all_tags(cfg: Config) -> list[str]:
    return sorted({t for q in load_tags(cfg).values() for t in q.values()})


# ------------------------------------------------------------ F21.4 서술형 (the teacher grades by hand)

def essay_view(conn: sqlite3.Connection, class_id: int, day: str, label: str) -> dict:
    """The quick-entry screen: the class list with what is already recorded for this 서술형 quiz."""
    rows = {r["student_id"]: dict(r) for r in conn.execute(
        "SELECT * FROM quiz_marks WHERE class_id = ? AND day = ? AND label = ? AND quiz = 1 AND source = 'essay'", (class_id, day, label))}
    tags = next((json.loads(r["tags"]) for r in rows.values() if r["tags"]), [])
    mx = next((r["max_score"] for r in rows.values() if r["max_score"]), None)
    return {"tags": tags, "max": mx, "students": [
        {"id": s["id"], "display_name": s["display_name"], "mark": rows.get(s["id"], {}).get("mark"),
         "score": rows.get(s["id"], {}).get("score"), "note": rows.get(s["id"], {}).get("note") or ""}
        for s in conn.execute("SELECT id, display_name FROM students_public WHERE class_id = ? AND active = 1 ORDER BY id", (class_id,))]}


def essay_save(conn: sqlite3.Connection, class_id: int, day: str, label: str, tags: list[str], max_score: float | None,
               rows: list[dict]) -> int:
    """One row per student x quiz, editable; a score out of max_score becomes ○ (full) / △ (part) / ✕ (0). Every change is an event."""
    from .db import now_utc_iso
    label = " ".join((label or "").split())[:40]
    if not label or not re.match(r"^\d{4}-\d{2}-\d{2}$", day or ""):
        raise ClassworkError("서술형: a day and a label")
    tags = [str(t)[:30] for t in tags if str(t).strip()][:3]
    members = {r[0] for r in conn.execute("SELECT id FROM students WHERE class_id = ? AND active = 1", (class_id,))}
    ts, n = now_utc_iso(), 0
    for r in rows:
        sid = r.get("student_id")
        if sid not in members:
            continue
        score = r.get("score")
        mark = SYMBOL.get(str(r.get("mark") or ""), "")
        if score not in (None, "") and max_score:
            score = max(0.0, min(float(score), float(max_score)))
            mark = "o" if score >= float(max_score) else "x" if score <= 0 else "p"
        if mark not in ("o", "x", "p", "m"):
            if conn.execute("DELETE FROM quiz_marks WHERE student_id = ? AND day = ? AND label = ? AND quiz = 1 AND source = 'essay'",
                            (sid, day, label)).rowcount:
                log_event(conn, "essay_clear", {"student_id": sid, "day": day, "label": label})
            continue
        cur = conn.execute("""SELECT mark, score, note, tags FROM quiz_marks WHERE student_id = ? AND day = ? AND label = ? AND quiz = 1
                              AND source = 'essay'""", (sid, day, label)).fetchone()
        new = (mark, None if score in (None, "") else float(score), (r.get("note") or "")[:80] or None, json.dumps(tags, ensure_ascii=False))
        if cur and tuple(cur) == new:
            continue
        conn.execute("""INSERT INTO quiz_marks (student_id, class_id, day, label, quiz, mark, source, score, max_score, tags, note, ts, updated)
                        VALUES (?, ?, ?, ?, 1, ?, 'essay', ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (student_id, day, label, quiz, source) DO UPDATE SET mark = excluded.mark, score = excluded.score,
                        max_score = excluded.max_score, tags = excluded.tags, note = excluded.note, updated = excluded.updated""",
                     (sid, class_id, day, label, new[0], new[1], max_score, new[3], new[2], ts, ts))
        log_event(conn, "essay_mark", {"student_id": sid, "day": day, "label": label, "mark": mark})
        n += 1
    conn.commit()
    return n


# ------------------------------------------------------------ F21.5 photo grading (a local vision model writes the CSV)

PHOTO_COLUMNS = ("date", "class", "student", "set", "quiz", "read_answer", "mark", "confidence", "source")


def threshold(cfg: Config) -> float:
    try:
        return min(1.0, max(0.0, float(cfg.extra.get("classwork_auto_min", 0.9))))
    except (TypeError, ValueError):
        return 0.9


def _self_mark(conn, student_id: int, day: str, label: str, quiz: int) -> str | None:
    r = conn.execute("""SELECT m.marks FROM classwork_marks m JOIN classwork_sets s ON s.sid = m.sid
                        WHERE m.student_id = ? AND s.day = ? AND s.label = ?""", (student_id, day, label)).fetchone()
    return r[0][quiz - 1] if r and 0 < quiz <= len(r[0]) else None


def import_photo(conn: sqlite3.Connection, cfg: Config, csv_path: str | Path) -> dict:
    """results.csv -> quiz_marks (source photo). Confidence >= classwork_auto_min: stored; below it, or when it disagrees with
    the student's own mark: 사진 확인 필요 (nothing from a queued row reaches a page until the teacher decides)."""
    from .db import now_utc_iso
    th, ts = threshold(cfg), now_utc_iso()
    n = {"stored": 0, "queued": 0, "flagged": 0, "skipped": 0}
    text = Path(csv_path).read_text(encoding="utf-8-sig")
    for i, row in enumerate(csv.DictReader(io.StringIO(text)), 2):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        st = conn.execute("""SELECT s.id, s.class_id FROM students s LEFT JOIN classes c ON c.id = s.class_id
                             WHERE s.slug = ? AND (c.name = ? OR ? = '')""", (row.get("student", ""), row.get("class", ""), row.get("class", ""))).fetchone()
        mark = SYMBOL.get(row.get("mark", ""))
        try:
            quiz, conf = int(row.get("quiz", "")), float(row.get("confidence", "0") or 0)
            day = date.fromisoformat(row.get("date", "")).isoformat()
        except ValueError:
            st = None
        if not st or mark not in SELF_MARKS or not row.get("set") or (row.get("source") or "photo") != "photo":
            n["skipped"] += 1
            continue
        label = row["set"][:40]
        own = _self_mark(conn, st["id"], day, label, quiz)
        disagree = own is not None and own != mark
        status = "ok" if conf >= th and not disagree else "queued"
        conn.execute("""INSERT INTO quiz_marks (student_id, class_id, day, label, quiz, mark, source, read_answer, confidence, status, ts, updated)
                        VALUES (?, ?, ?, ?, ?, ?, 'photo', ?, ?, ?, ?, ?)
                        ON CONFLICT (student_id, day, label, quiz, source) DO UPDATE SET mark = excluded.mark, read_answer = excluded.read_answer,
                        confidence = excluded.confidence, status = CASE WHEN quiz_marks.status = 'done' THEN 'done' ELSE excluded.status END,
                        updated = excluded.updated""",
                     (st["id"], st["class_id"], day, label, quiz, mark, row.get("read_answer", "")[:20], conf, status, ts, ts))
        n["stored" if status == "ok" else "queued"] += 1
        n["flagged"] += disagree
    log_event(conn, "classwork_photo_import", n)
    conn.commit()
    return n


def queue(conn: sqlite3.Connection, cfg: Config) -> list[dict]:
    """사진 확인 필요: low-confidence photo marks, and photo marks that disagree with the student's own (no teacher mark yet)."""
    out = []
    for r in conn.execute("""SELECT q.*, s.display_name, c.name AS class_name FROM quiz_marks q JOIN students s ON s.id = q.student_id
                             LEFT JOIN classes c ON c.id = q.class_id WHERE q.source = 'photo' AND q.status != 'done'
                             ORDER BY q.day, q.label, q.quiz, q.student_id"""):
        if conn.execute("""SELECT 1 FROM quiz_marks WHERE student_id = ? AND day = ? AND label = ? AND quiz = ? AND source = 'teacher'""",
                        (r["student_id"], r["day"], r["label"], r["quiz"])).fetchone():
            continue
        own = _self_mark(conn, r["student_id"], r["day"], r["label"], r["quiz"])
        flag = own is not None and own != r["mark"]
        if r["status"] == "queued" or flag:
            out.append({"id": r["id"], "student_id": r["student_id"], "display_name": r["display_name"], "class_name": r["class_name"] or "",
                        "day": r["day"], "label": r["label"], "quiz": r["quiz"], "read_answer": r["read_answer"] or "",
                        "photo_mark": r["mark"], "self_mark": own, "confidence": r["confidence"], "flag": flag})
    return out


def decide(conn: sqlite3.Connection, photo_id: int, mark: str) -> None:
    """The teacher's choice (○ / ✕ / –) for a queued photo mark: a teacher mark that wins over everything."""
    from .db import now_utc_iso
    mark = SYMBOL.get(mark, mark)
    r = conn.execute("SELECT * FROM quiz_marks WHERE id = ? AND source = 'photo'", (photo_id,)).fetchone()
    if not r or mark not in SELF_MARKS:
        raise ClassworkError("a queued photo mark and ○ / ✕ / –")
    ts = now_utc_iso()
    conn.execute("""INSERT INTO quiz_marks (student_id, class_id, day, label, quiz, mark, source, ts, updated)
                    VALUES (?, ?, ?, ?, ?, ?, 'teacher', ?, ?)
                    ON CONFLICT (student_id, day, label, quiz, source) DO UPDATE SET mark = excluded.mark, updated = excluded.updated""",
                 (r["student_id"], r["class_id"], r["day"], r["label"], r["quiz"], mark, ts, ts))
    conn.execute("UPDATE quiz_marks SET status = 'done', updated = ? WHERE id = ?", (ts, photo_id))
    log_event(conn, "classwork_decide", {"id": photo_id, "mark": mark, "photo": r["mark"]})
    conn.commit()


def photo_file(conn: sqlite3.Connection, cfg: Config, student_id: int, day: str) -> Path | None:
    """The student's own submission photo that day in the local photo store (`pull --photos`), for the queue's thumbnail."""
    r = conn.execute("SELECT photo_key FROM submissions WHERE student_id = ? AND day = ? AND photo_key IS NOT NULL", (student_id, day)).fetchone()
    if not r:
        return None
    folder = Path(str(cfg.extra.get("photos_dir") or "photos"))
    stem = r[0].replace("photo:", "").replace(":", "_").replace("/", "_")
    for cand in (folder / stem, folder / (stem + ".jpg")):
        if cand.is_file():
            return cand
    return None
