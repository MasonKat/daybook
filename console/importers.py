"""Importers (BUILD_BRIEF §5.1). Unmatched rows are reported, never invented."""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import models as M
from .db import log_event, new_token, now_utc_iso


# ---------------------------------------------------------------- roster

@dataclass
class RosterReport:
    added: list[str] = field(default_factory=list)      # slugs
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)    # human-readable reasons (console only)
    warnings: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)       # F11 preview: one per CSV line (local Teacher tab only)


def load_mapping(path: str | Path) -> dict:
    m = json.loads(Path(path).read_text(encoding="utf-8"))
    cols = m.get("columns") or {}
    if "full_name" not in cols.values():
        raise ValueError("mapping: one column must map to full_name")
    return m


def add_student(conn: sqlite3.Connection, class_id: int | None, full_name: str, display_name: str | None = None,
                ext_id: str | None = None, parent_phone: str | None = None, track: str | None = None,
                active: bool = True) -> sqlite3.Row:
    full_name = full_name.strip()
    display = (display_name or M.default_display_name(full_name)).strip()
    slug = M.unique_slug(conn, display)
    cur = conn.execute(
        """INSERT INTO students (slug, full_name, display_name, class_id, track, parent_phone, student_token,
                                 parent_token, active, created_at, ext_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (slug, full_name, display, class_id, track, parent_phone or None, new_token(), new_token(),
         1 if active else 0, now_utc_iso(), ext_id or None),
    )
    log_event(conn, "student_add", {"student_id": cur.lastrowid, "class_id": class_id})
    return conn.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()


def import_roster(conn: sqlite3.Connection, csv_path: str | Path, mapping: dict, class_id: int | None,
                  dry_run: bool = False) -> RosterReport:
    """dry_run (F11 가져오기 미리보기): the same matching, then everything is rolled back and nothing is logged."""
    rep = RosterReport()
    if dry_run:
        conn.execute("SAVEPOINT roster_preview")
    try:
        _import_roster(conn, csv_path, mapping, class_id, rep)
    finally:
        if dry_run:
            conn.execute("ROLLBACK TO roster_preview")
            conn.execute("RELEASE roster_preview")
    if not dry_run:
        log_event(conn, "import_roster", {"file": Path(csv_path).name, "added": len(rep.added),
                                          "updated": len(rep.updated), "skipped": len(rep.skipped)})
        conn.commit()
    return rep


# F23 (setup rehearsal): Korean / variant headers are accepted as the mapped English ones (any spacing or case)
HEADER_ALIASES = {
    "full_name": ("name", "이름", "성명", "학생명", "학생 이름", "학생이름"),
    "ext_id": ("student id", "id", "학번", "학생 id", "학생번호", "번호(id)", "아이디"),
    "active": ("status", "상태", "재원상태", "재원 상태", "재원여부"),
    "parent_phone": ("phone number", "phone", "연락처", "전화번호", "보호자 연락처", "학부모 연락처", "보호자연락처", "학부모연락처"),
    "callsign": ("callsign", "call sign", "호출명"),
}
ROSTER_ENCODINGS = ("utf-8-sig", "cp949")          # Excel's "CSV UTF-8" and the plain Korean-Windows "CSV" (CP949)


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").replace("\ufeff", "").strip().lower())


def read_roster_rows(csv_path: str | Path, mapping: dict) -> tuple[list[tuple[int, dict]], list[str]]:
    """(line, {field: value}) for every non-blank row, and notes. Accepts a BOM, CP949, Korean headers, blank rows."""
    raw = Path(csv_path).read_bytes()
    text, notes = None, []
    for enc in dict.fromkeys((mapping.get("encoding", "utf-8-sig"),) + ROSTER_ENCODINGS):
        try:
            text = raw.decode(enc)
            if enc == "cp949":
                notes.append("read as CP949 (Excel's plain CSV); saving as 'CSV UTF-8' avoids this")
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("cannot read the CSV (not UTF-8 or CP949): in Excel, Save As → 'CSV UTF-8'")
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    heads = reader.fieldnames or []
    want = {}                                    # field -> the CSV's header for it
    for src, dst in mapping["columns"].items():
        for h in heads:
            if _norm(h) == _norm(src) or _norm(h) in HEADER_ALIASES.get(dst, ()):
                want.setdefault(dst, h)
    for dst, names in HEADER_ALIASES.items():
        for h in heads:
            if _norm(h) in names:
                want.setdefault(dst, h)
    if "full_name" not in want:
        raise ValueError(f"CSV has no name column (Name / 이름 / 성명); found {heads}")
    out = []
    for i, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values() if isinstance(v, str)):
            continue                                 # a blank row (Excel leaves some at the end): ignored
        out.append((i, {dst: (row.get(h) or "").strip() for dst, h in want.items()}))
    return out, notes


def _import_roster(conn: sqlite3.Connection, csv_path: str | Path, mapping: dict, class_id: int | None,
                   rep: RosterReport) -> None:
    active_values = {v.strip() for v in mapping.get("active_values", [])}
    rows, notes = read_roster_rows(csv_path, mapping)
    rep.warnings.extend(notes)
    for i, rec in rows:
        name = rec.get("full_name", "")
        if not name:
            rep.skipped.append(f"line {i}: empty name")
            rep.rows.append({"line": i, "name": "", "ext_id": rec.get("ext_id") or "", "status": "skipped", "why": "empty name"})
            continue
        active = (rec["active"] in active_values) if "active" in rec and active_values else True
        ext_id = rec.get("ext_id") or None
        phone = rec.get("parent_phone") or None
        existing = None
        if ext_id:
            existing = conn.execute("SELECT * FROM students WHERE ext_id = ?", (ext_id,)).fetchone()
        if existing is None:
            same = conn.execute("SELECT * FROM students WHERE full_name = ?", (name,)).fetchall()
            if len(same) == 1 and (not ext_id or not same[0]["ext_id"]):
                existing = same[0]
            elif len(same) > 1:
                rep.skipped.append(f"line {i}: several students share this name and no ID matched; fix by hand")
                rep.rows.append({"line": i, "name": name, "ext_id": ext_id or "", "status": "skipped", "why": "same name"})
                continue
        if existing is None:
            if class_id is None:
                rep.skipped.append(f"line {i}: new student but no --class given")
                rep.rows.append({"line": i, "name": name, "ext_id": ext_id or "", "status": "skipped", "why": "no class"})
                continue
            s = add_student(conn, class_id, name, ext_id=ext_id, parent_phone=phone, active=active)
            _set_callsign(conn, s, rec.get("callsign"), rep)
            rep.added.append(s["slug"])
            rep.rows.append({"line": i, "name": name, "ext_id": ext_id or "", "status": "new", "why": ""})
            if s["display_name"] == s["full_name"]:
                rep.warnings.append(
                    f"{s['slug']}: display name equals the full name; publish will refuse it until you run "
                    f"`daybook roster edit {s['slug']} --display <given name>`")
            continue
        changes = {}
        if existing["full_name"] != name:
            changes["full_name"] = name
        if ext_id and existing["ext_id"] != ext_id:
            changes["ext_id"] = ext_id
        if "parent_phone" in rec and (existing["parent_phone"] or None) != phone:
            changes["parent_phone"] = phone
        if bool(existing["active"]) != active:
            changes["active"] = 1 if active else 0
        if rec.get("callsign") and rec["callsign"] != (existing["callsign"] or ""):
            _set_callsign(conn, existing, rec["callsign"], rep)
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE students SET {sets} WHERE id = ?", (*changes.values(), existing["id"]))
            log_event(conn, "student_update", {"student_id": existing["id"], "fields": sorted(changes)})
            rep.updated.append(existing["slug"])
            rep.rows.append({"line": i, "name": name, "ext_id": ext_id or "", "status": "updated",
                             "why": ", ".join(sorted(changes))})
        else:
            rep.unchanged.append(existing["slug"])
            rep.rows.append({"line": i, "name": name, "ext_id": ext_id or "", "status": "unchanged", "why": ""})


def _set_callsign(conn, s, cs: str | None, rep: RosterReport) -> None:
    """F19.1 호출명 from the CSV (optional column 호출명 / callsign); a clash is a warning, never a failed import."""
    if not cs:
        return
    from . import ops
    s = conn.execute("SELECT * FROM students WHERE id = ?", (s["id"],)).fetchone()
    try:
        conn.execute("UPDATE students SET callsign = ? WHERE id = ?", (ops.check_callsign(conn, s, cs), s["id"]))
    except ops.OpError as e:
        rep.warnings.append(f"{s['slug']}: call sign {cs!r} not set ({e})")


# ---------------------------------------------------------------- screeners

ROUND_RE = re.compile(r"^round_(?P<set>.+)_(?P<date>\d{4}-\d{2}-\d{2})\.json$")


@dataclass
class ScreenerReport:
    files: int = 0
    rows: int = 0
    imported: int = 0
    unmatched: list[str] = field(default_factory=list)   # "file: student=… name=…" (console only)
    errors: list[str] = field(default_factory=list)


def match_student(conn: sqlite3.Connection, student: str, name: str) -> tuple[sqlite3.Row | None, str]:
    """`student` -> ext_id, else `name` -> full_name (also `student` -> full_name, since it may hold a name)."""
    student, name = (student or "").strip(), (name or "").strip()
    if student:
        r = conn.execute("SELECT * FROM students WHERE ext_id = ?", (student,)).fetchall()
        if len(r) == 1:
            return r[0], "ext_id"
    for cand in (name, student):
        if not cand:
            continue
        r = conn.execute("SELECT * FROM students WHERE full_name = ?", (cand,)).fetchall()
        if len(r) == 1:
            return r[0], "full_name"
        if len(r) > 1:
            return None, "ambiguous name"
    return None, "no match"


def upsert_test(conn: sqlite3.Connection, student_id: int, d: str, test_name: str, n_quizzes, correct, wrong, blank,
                reach, eff, persona, top_reason, source) -> None:
    conn.execute(
        """INSERT INTO tests (student_id, date, test_name, n_quizzes, correct, wrong, blank, reach, efficiency,
                              persona, top_reason, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (student_id, test_name, date) DO UPDATE SET
             n_quizzes = excluded.n_quizzes, correct = excluded.correct, wrong = excluded.wrong,
             blank = excluded.blank, reach = excluded.reach, efficiency = excluded.efficiency,
             persona = excluded.persona, top_reason = excluded.top_reason, source = excluded.source""",
        (student_id, d, test_name, n_quizzes, correct, wrong, blank, reach, eff, persona, top_reason, source),
    )


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def import_screeners(conn: sqlite3.Connection, directory: str | Path) -> ScreenerReport:
    rep = ScreenerReport()
    files = sorted(Path(directory).glob("round_*.json"))
    for f in files:
        if not ROUND_RE.match(f.name):
            rep.errors.append(f"{f.name}: file name is not round_<set_id>_<YYYY-MM-DD>.json")
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            set_id, d, nq = str(data["set_id"]), str(data["date"]), _int(data.get("n_quizzes"))
            date.fromisoformat(d)
            students = data["students"]
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            rep.errors.append(f"{f.name}: {e}")
            continue
        rep.files += 1
        for row in students:
            rep.rows += 1
            s, how = match_student(conn, str(row.get("student", "")), str(row.get("name", "")))
            if s is None:
                rep.unmatched.append(f"{f.name}: student={row.get('student', '')!s} name={row.get('name', '')!s} ({how})")
                continue
            c, w, b = _int(row.get("correct")), _int(row.get("wrong")), _int(row.get("blank"))
            upsert_test(conn, s["id"], d, set_id, nq, c, w, b, _float(row.get("reach_ceiling")), M.efficiency(c, w),
                        row.get("archetype"), row.get("top_reason"), f"screener:{f.name}")
            rep.imported += 1
    log_event(conn, "import_screeners", {"files": rep.files, "rows": rep.rows, "imported": rep.imported,
                                         "unmatched": len(rep.unmatched)})
    conn.commit()
    return rep


# ---------------------------------------------------------------- generic tests CSV

TEST_COLS = ("date", "student_slug", "test_name", "n_quizzes", "correct", "wrong", "blank")


def import_tests_csv(conn: sqlite3.Connection, csv_path: str | Path) -> ScreenerReport:
    rep = ScreenerReport(files=1)
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in TEST_COLS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV is missing columns: {missing}")
        for i, row in enumerate(reader, start=2):
            rep.rows += 1
            s = conn.execute("SELECT id FROM students WHERE slug = ?", (row["student_slug"].strip(),)).fetchone()
            if s is None:
                rep.unmatched.append(f"line {i}: student_slug={row['student_slug']}")
                continue
            try:
                date.fromisoformat(row["date"].strip())
            except ValueError:
                rep.errors.append(f"line {i}: bad date {row['date']!r}")
                continue
            c, w, b = _int(row["correct"]), _int(row["wrong"]), _int(row["blank"])
            upsert_test(conn, s["id"], row["date"].strip(), row["test_name"].strip(), _int(row["n_quizzes"]), c, w, b,
                        None, M.efficiency(c, w), None, None, f"csv:{Path(csv_path).name}")
            rep.imported += 1
    log_event(conn, "import_tests", {"rows": rep.rows, "imported": rep.imported, "unmatched": len(rep.unmatched)})
    conn.commit()
    return rep


# ---------------------------------------------------------------- F12: the teacher's own analysis (insights)

@dataclass
class InsightReport:
    rows: int = 0
    imported: int = 0
    unknown: list[str] = field(default_factory=list)     # ext_ids not on the roster (console only)
    errors: list[str] = field(default_factory=list)


def _clip(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def import_insights(conn: sqlite3.Connection, path: str | Path) -> InsightReport:
    """{"round", "date", "students": [{ext_id, focus[], videos[], student_line, parent_line}]} -> insights, matched by
    ext_id (an unknown ext_id is reported and skipped). At most 3 focus items and 3 http(s) videos are kept; the
    lines are scrubbed again at publish (names, phones, raw ids never reach a page)."""
    rep = InsightReport()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rnd, day = str(data["round"]).strip(), str(data["date"]).strip()
        date.fromisoformat(day)
        students = data["students"]
        if not rnd or not isinstance(students, list):
            raise ValueError
    except (ValueError, KeyError, TypeError) as e:
        rep.errors.append(f"{Path(path).name}: needs round, date (YYYY-MM-DD) and a students list ({e})")
        return rep
    for st in students:
        rep.rows += 1
        ext = str((st or {}).get("ext_id", "")).strip()
        row = conn.execute("SELECT id FROM students WHERE ext_id = ?", (ext,)).fetchone() if ext else None
        if row is None:
            rep.unknown.append(ext or "(no ext_id)")
            continue
        focus = [_clip(f, 30) for f in (st.get("focus") or []) if _clip(f, 30)][:3]
        videos = [str(u).strip() for u in (st.get("videos") or []) if str(u).strip().startswith(("https://", "http://"))][:3]
        conn.execute("""INSERT INTO insights (student_id, round, date, focus, videos, student_line, parent_line, ts)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (student_id, round) DO UPDATE SET date = excluded.date, focus = excluded.focus,
                          videos = excluded.videos, student_line = excluded.student_line, parent_line = excluded.parent_line,
                          ts = excluded.ts""",
                     (row["id"], rnd, day, json.dumps(focus, ensure_ascii=False), json.dumps(videos),
                      _clip(st.get("student_line"), 120) or None, _clip(st.get("parent_line"), 160) or None, now_utc_iso()))
        rep.imported += 1
    log_event(conn, "import_insights", {"file": Path(path).name, "imported": rep.imported, "unknown": len(rep.unknown)})
    conn.commit()
    return rep
