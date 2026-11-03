"""F6: the Teacher tab of the local board app (127.0.0.1 only, never deployed).

Every action goes through the same functions as the CLI (ops, importers, report, weekly, generate, pull), so the
teacher can do the Saturday routine without a terminal. The HTTP layer is in board.py.
"""
from __future__ import annotations

import base64
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from . import generate, importers, models as M, ops
from .config import ROOT, Config
from .pull import PullError, http_fetch, pull, push_tokmap

MAX_IMPORT_BYTES = 5 * 1024 * 1024


class TeacherError(ValueError):
    pass


def _link(cfg: Config, kind: str, token: str) -> str:
    return f"{cfg.pages}/{kind}/{token}/" if cfg.pages else f"/{kind}/{token}/"


def roster(conn: sqlite3.Connection, cfg: Config, class_id: int | None = None) -> list[dict]:
    """F11: every student of every class (the tab filters). Full names are shown here, on the laptop only."""
    q = """SELECT s.*, c.name AS class_name FROM students s LEFT JOIN classes c ON c.id = s.class_id"""
    q += " WHERE s.class_id = ?" if class_id else ""
    return [{"slug": r["slug"], "display_name": r["display_name"], "full_name": r["full_name"], "active": bool(r["active"]),
             "class_name": r["class_name"] or "", "track": r["track"] or "", "ext_id": r["ext_id"] or "",
             "left_on": r["left_on"] or "",
             "student_link": _link(cfg, "s", r["student_token"]), "parent_link": _link(cfg, "p", r["parent_token"])}
            for r in conn.execute(q + " ORDER BY s.active DESC, s.class_id, s.id", (class_id,) if class_id else ())]


def roster_edit(conn: sqlite3.Connection, cfg: Config, body: dict) -> dict:
    display = body.get("display_name")
    active = body.get("active")
    changed = ops.edit_student(conn, str(body.get("slug", "")), display if isinstance(display, str) else None,
                               active=active if isinstance(active, bool) else None)
    conn.commit()
    msg = push_tokmap(conn, cfg) if "active" in changed else ""
    return {"ok": True, "changed": sorted(changed), "message": msg}


def notes(conn: sqlite3.Connection, cfg: Config, cls: sqlite3.Row, day: date) -> dict:
    from .report import suggest_notes
    week = M.iso_week(day)
    drafts = {slug: d for slug, _, d in suggest_notes(conn, cfg, cls, day, include_existing=True)}
    out = []
    for r in conn.execute("SELECT id, slug, display_name FROM students_public WHERE class_id = ? AND active = 1 ORDER BY id",
                          (cls["id"],)):
        n = conn.execute("SELECT text FROM notes WHERE student_id = ? AND week = ? ORDER BY ts DESC, id DESC LIMIT 1",
                         (r["id"], week)).fetchone()
        out.append({"slug": r["slug"], "display_name": r["display_name"], "note": n["text"] if n else "",
                    "suggestion": drafts.get(r["slug"], "")})
    return {"week": week, "students": out}


def note_save(conn: sqlite3.Connection, body: dict) -> dict:
    s = ops.get_student(conn, str(body.get("slug", "")))
    ops.add_note(conn, s["id"], str(body.get("week", "")), str(body.get("text", "")))
    conn.commit()
    return {"ok": True}


def _decode_files(files) -> list[tuple[str, bytes]]:
    out, total = [], 0
    for f in files or []:
        name = Path(str(f.get("name", ""))).name
        if not name:
            continue
        raw = str(f.get("data", ""))
        raw = raw.split(",", 1)[1] if raw.startswith("data:") else raw
        data = base64.b64decode(raw, validate=True)
        total += len(data)
        if total > MAX_IMPORT_BYTES:
            raise TeacherError("files are too large")
        out.append((name, data))
    if not out:
        raise TeacherError("no file")
    return out


def import_files(conn: sqlite3.Connection, body: dict) -> dict:
    """Screener rounds (round_<set>_<date>.json) or a tests CSV, through the same importers as the CLI."""
    kind = body.get("kind")
    files = _decode_files(body.get("files"))
    tmp = Path(tempfile.mkdtemp(prefix="daybook-import-"))
    try:
        for name, data in files:
            (tmp / name).write_bytes(data)
        if kind == "screeners":
            rep = importers.import_screeners(conn, tmp)
        elif kind == "tests":
            if len(files) != 1 or not files[0][0].lower().endswith(".csv"):
                raise TeacherError("choose one .csv file")
            rep = importers.import_tests_csv(conn, tmp / files[0][0])
        elif kind == "insights":                                     # F12
            if len(files) != 1 or not files[0][0].lower().endswith(".json"):
                raise TeacherError("choose one .json file")
            ir = importers.import_insights(conn, tmp / files[0][0])
            return {"ok": not ir.errors, "files": 1, "rows": ir.rows, "imported": ir.imported,
                    "unmatched": [f"unknown ext_id {u}" for u in ir.unknown], "errors": ir.errors}
        else:
            raise TeacherError("kind must be screeners, tests or insights")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": True, "files": rep.files, "rows": rep.rows, "imported": rep.imported,
            "unmatched": rep.unmatched, "errors": rep.errors}


def moments(conn: sqlite3.Connection, class_id: int) -> list[dict]:
    return [{"date": r["date"], "text": r["text"] or "", "image_key": r["image_key"]} for r in conn.execute(
        """SELECT m.*, se.date FROM moments m JOIN sessions se ON se.id = m.session_id
           WHERE m.class_id = ? ORDER BY se.date DESC, m.id DESC LIMIT 30""", (class_id,))]


def cancel(conn: sqlite3.Connection, cls: sqlite3.Row, body: dict) -> dict:
    try:
        day = date.fromisoformat(str(body.get("date", "")))
    except ValueError as e:
        raise TeacherError("date must be YYYY-MM-DD") from e
    undo = bool(body.get("undo"))
    ops.cancel_class(conn, cls, day, None if undo else (str(body.get("reason") or "휴강")))
    conn.commit()
    cancelled = [{"date": d.isoformat(), "reason": r} for d, r in sorted(M.cancelled_days(conn, cls["id"]).items())]
    return {"ok": True, "cancelled": cancelled}


def publish(conn: sqlite3.Connection, cfg: Config, out: Path | None = None, db_path: str | None = None) -> dict:
    """Saturday button: pull (when a Worker is configured) -> publish -> lint, then send the student links."""
    steps = []
    if cfg.worker and cfg.teacher_token:
        try:
            rep = pull(conn, http_fetch(cfg))
            steps.append(f"pull: new {rep.new}, updated {rep.updated}, check-ins {rep.checkins}")
        except PullError as e:
            steps.append(f"pull skipped: {e}")
    res = generate.publish(conn, cfg, Path(out or "dist"))
    findings = [str(f) for f in res.findings]
    if res.ok:
        steps.append(f"published {res.n_students} students into {res.out}/ (lint passed)")
        msg = push_tokmap(conn, cfg)
        if msg:
            steps.append(msg)
        if db_path:
            steps.append(backup_now(db_path, cfg)["message"])            # G2: every 발행 ends with a backup
    else:
        steps.append("publish STOPPED: the no-PII lint failed; dist/ was not changed")
    return {"ok": res.ok, "steps": steps, "findings": findings}


def deploy(cfg: Config, runner=None, out: str = "dist") -> dict:
    """Runs the teacher's own `wrangler pages deploy` (their login) and returns its output."""
    project = str(cfg.extra.get("pages_project") or "daybook")
    if not re.fullmatch(r"[a-z0-9-]{1,58}", project):
        raise TeacherError("pages_project must be lowercase letters, digits and -")
    npx = shutil.which("npx") or "npx"          # npx.cmd on Windows: the full path runs without a shell
    cmd = [npx, "wrangler", "pages", "deploy", str(Path("..") / out), "--project-name", project, "--branch", "main"]
    run = runner or (lambda c: subprocess.run(c, cwd=ROOT / "worker", capture_output=True, timeout=600,
                                              encoding="utf-8", errors="replace"))
    try:
        r = run(cmd)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "output": f"could not run wrangler: {e}"}
    return {"ok": r.returncode == 0, "output": ((r.stdout or "") + (r.stderr or ""))[-6000:],
            "command": " ".join(["npx"] + cmd[1:])}


def weekly(conn: sqlite3.Connection, cfg: Config, cls: sqlite3.Row, day: date) -> list[str]:
    from .weekly import messages
    return messages(conn, cfg, cls, day)


# ---------------------------------------------------------------- F13: the parent-contact desk (laptop only)

def contacts(conn: sqlite3.Connection, cfg: Config, cls: sqlite3.Row, day: date) -> dict:
    """Everything the Teacher tab's contact sections show: 연락 필요, 이번 주 할 일, 연락 현황, monthly good news."""
    from . import contact as C
    return {"flags": C.flags(conn, cfg, day), "plan": C.plan_week(conn, cfg, day), "status": C.status(conn, cfg, day),
            "month_end": C.last_week_of_month(day), "good_news": C.good_news(conn, cfg, day),
            "policy": C.policy(cls), "settings": C.settings(cfg)}


def class_flags(conn: sqlite3.Connection, cfg: Config, class_id: int, day: date) -> dict:
    """The board's "선생님 보기" toggle: this class's flags, names and reasons only (never on the projected view)."""
    from . import contact as C
    return {"flags": [{"display_name": f["display_name"], "level": f["level"], "rules": f["rules"]}
                      for f in C.flags(conn, cfg, day, class_id)]}


def contact_log(conn: sqlite3.Connection, body: dict) -> dict:
    from . import contact as C
    s = ops.get_student(conn, str(body.get("slug", "")))
    cid = C.log_contact(conn, s["id"], str(body.get("kind", "call")), str(body.get("summary", "") or ""),
                        str(body.get("direction", "out") or "out"), str(body.get("follow_up") or "") or None)
    conn.commit()
    return {"ok": True, "id": cid}


def contact_snooze(conn: sqlite3.Connection, body: dict, day: date) -> dict:
    from . import contact as C
    s = ops.get_student(conn, str(body.get("slug", "")))
    until = C.snooze(conn, s["id"], str(body.get("rule", "all") or "all"), day)
    conn.commit()
    return {"ok": True, "until": until}


def contact_profile(conn: sqlite3.Connection, body: dict) -> dict:
    from . import contact as C
    s = ops.get_student(conn, str(body.get("slug", "")))
    new = C.set_profile(conn, s["id"], body.get("channel") or None, body.get("style") or None, body.get("greeting") or None)
    conn.commit()
    return {"ok": True, **new}


def policy_save(conn: sqlite3.Connection, cls: sqlite3.Row, body: dict) -> dict:
    """F13.2 per-class thresholds and the class hours (also `daybook class edit`)."""
    sets = ops.class_policy(conn, cls, {k: body.get(k) for k in ops.POLICY_FIELDS if k in body})
    conn.commit()
    return {"ok": True, "changed": sorted(sets)}


def solved_rows(conn: sqlite3.Connection, class_id: int, day: date) -> list[dict]:
    """F13.5: this week's self-reported 푼 문제 수 for the class, so the teacher can correct one."""
    mon, sun = M.week_bounds(day)
    return [{"slug": r["slug"], "display_name": r["display_name"], "day": r["day"], "solved": r["solved"]}
            for r in conn.execute("""SELECT s.slug, s.display_name, sb.day, sb.solved FROM submissions sb
                                     JOIN students_public s ON s.id = sb.student_id
                                     WHERE s.class_id = ? AND s.active = 1 AND sb.status != 'deleted' AND sb.day BETWEEN ? AND ?
                                     ORDER BY sb.day DESC, s.id""", (class_id, mon.isoformat(), sun.isoformat()))]


def solved_fix(conn: sqlite3.Connection, body: dict) -> dict:
    s = ops.get_student(conn, str(body.get("slug", "")))
    ops.set_solved(conn, s["id"], str(body.get("day", "")), body.get("solved"))
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- F14: weekly-test rounds

def rounds_view(conn: sqlite3.Connection) -> dict:
    from . import rounds as R
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM classes")}
    out = []
    for r in conn.execute("SELECT * FROM rounds ORDER BY open_at DESC"):
        p = R.progress(conn, r["id"])
        out.append({"id": r["id"], "label": r["label"], "grade": r["grade"] or "", "n": r["n"], "open_at": r["open_at"],
                    "close_at": r["close_at"], "open": R.is_open(r), "classes": [names.get(c, "?") for c in R.class_ids(r)],
                    "done": p["done"], "total": p["total"]})
    return {"rounds": out, "classes": [names[k] for k in sorted(names)], "grades": list(R.GRADES)}


def round_progress(conn: sqlite3.Connection, round_id: str) -> dict:
    from . import rounds as R
    try:
        return R.progress(conn, round_id)
    except R.RoundError as e:
        raise TeacherError(str(e)) from e


def round_action(conn: sqlite3.Connection, cfg: Config, body: dict) -> dict:
    """add | edit | close | export, then the rounds go to the Worker (same functions as `daybook round …`)."""
    from . import rounds as R
    act, rid_ = str(body.get("action", "")), str(body.get("id", "")).strip()
    try:
        if act == "add":
            R.add(conn, rid_, body.get("grade") or None, body.get("classes") or [], int(body.get("n") or 0),
                  str(body.get("open_at", "")), str(body.get("close_at", "")), body.get("label") or None)
        elif act == "edit":
            R.edit(conn, rid_, grade=body.get("grade") or None, classes=body.get("classes") or None, n=body.get("n"),
                   open_at=body.get("open_at") or None, close_at=body.get("close_at") or None, label=body.get("label") or None)
        elif act == "close":
            R.close_now(conn, rid_)
        elif act == "export":
            fmt = body.get("format") or "csv"
            path = R.export(conn, rid_, fmt)
            conn.commit()
            return {"ok": True, "message": str(path), "path": str(path)}
        else:
            raise TeacherError("unknown action")
    except (R.RoundError, ValueError) as e:
        conn.rollback()
        raise TeacherError(str(e)) from e
    conn.commit()
    return {"ok": True, "message": R.push(conn, cfg)}


# ---------------------------------------------------------------- F11: the teacher manages the members alone

def classes_view(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for c in conn.execute("SELECT * FROM classes ORDER BY id"):
        out.append({"name": c["name"], "grade": c["grade"] or "", "term": c["term"] or "",
                    "days": ",".join(M.WEEKDAYS_KO[i] for i in M.mask_days(c["weekday_mask"])),
                    "reward_label": c["reward_label"] or "", "goal": c["reward_goal_points"] or 0,
                    "start_time": c["start_time"] or "", "students": M.active_count(conn, c["id"])})
    return out


def intro_messages(conn: sqlite3.Connection, cfg: Config, slug: str) -> dict:
    """안내 문구: one formal message for the parent and one for the student, each with their own link. Copy only."""
    from . import contact as C
    from .config import strings
    S = strings()
    s = ops.get_student(conn, slug)
    g = C.profile(conn, s["id"])["greeting"]
    parent = S["parent_msg"]["link_intro"].format(g=g, name=s["display_name"], link=_link(cfg, "p", s["parent_token"]))
    student = S["link_intro_student"].format(name=s["display_name"], link=_link(cfg, "s", s["student_token"]))
    return {"parent": parent, "student": student}


def _links(conn, cfg, slug) -> dict:
    s = ops.get_student(conn, slug)
    return {"slug": s["slug"], "display_name": s["display_name"], "student_link": _link(cfg, "s", s["student_token"]),
            "parent_link": _link(cfg, "p", s["parent_token"]), **intro_messages(conn, cfg, slug)}


def student_add(conn: sqlite3.Connection, cfg: Config, body: dict) -> dict:
    """학생 추가: the same add_student as `daybook roster add`; full name and phone stay on the laptop."""
    full = " ".join(str(body.get("full_name", "")).split())
    if not full:
        raise TeacherError("full name is required")
    cls = ops.get_class(conn, str(body.get("class", "")))
    ext = str(body.get("ext_id", "") or "").strip() or None
    if ext and conn.execute("SELECT 1 FROM students WHERE ext_id = ?", (ext,)).fetchone():
        raise TeacherError(f"student ID {ext} is already used")
    phone = str(body.get("parent_phone", "") or "").strip() or None
    if phone and not ops.PHONE_OK.match(phone):
        raise TeacherError("phone: digits and - only")
    display = str(body.get("display_name", "") or "").strip() or None
    s = importers.add_student(conn, cls["id"], full, display_name=display, ext_id=ext, parent_phone=phone,
                              track=str(body.get("track", "") or "").strip() or None)
    if s["display_name"] == s["full_name"]:
        conn.rollback()
        raise TeacherError("the display name must not be the full name (e.g. the given name only)")
    conn.commit()
    return {"ok": True, **_links(conn, cfg, s["slug"]), "message": push_tokmap(conn, cfg)}


def student_move(conn: sqlite3.Connection, cfg: Config, body: dict) -> dict:
    """반 이동: the student's history stays theirs; the Worker learns the new class key."""
    slug = str(body.get("slug", ""))
    ops.edit_student(conn, slug, class_name=str(body.get("class", "")))
    conn.commit()
    return {"ok": True, "message": push_tokmap(conn, cfg)}


def student_leave(conn: sqlite3.Connection, cfg: Config, body: dict) -> dict:
    """퇴원 처리 with a date, or its undo (`undo: true`)."""
    d = None if body.get("undo") else str(body.get("date") or M.kst_today().isoformat())
    s = ops.set_left(conn, str(body.get("slug", "")), d)
    conn.commit()
    return {"ok": True, "active": bool(s["active"]), "left_on": s["left_on"] or "", "message": push_tokmap(conn, cfg)}


def student_rotate(conn: sqlite3.Connection, cfg: Config, body: dict) -> dict:
    """링크 재발급: the old link stops working; the new links (and messages) come back for copying."""
    which = str(body.get("which", "both"))
    if which not in ("both", "student", "parent"):
        raise TeacherError("which: both | student | parent")
    slug = str(body.get("slug", ""))
    ops.rotate_tokens(conn, ops.get_student(conn, slug), which)
    conn.commit()
    return {"ok": True, **_links(conn, cfg, slug), "message": push_tokmap(conn, cfg)}


def class_save(conn: sqlite3.Connection, body: dict) -> dict:
    """반 만들기 / 반 수정."""
    def opt(k):
        v = body.get(k)
        return None if v is None else str(v)
    goal = body.get("goal")
    cls = ops.save_class(conn, str(body.get("name", "")), create=bool(body.get("create")), new_name=opt("new_name"),
                         term=opt("term") or None, days=opt("days") or None, reward_label=opt("reward_label"),
                         goal=int(goal) if goal not in (None, "") else None, grade=opt("grade"), start_time=opt("start_time"))
    conn.commit()
    return {"ok": True, "name": cls["name"]}


def roster_preview(conn: sqlite3.Connection, cfg: Config, body: dict) -> dict:
    """가져오기 미리보기 (and, with `write: true`, the import itself) for a roster CSV with mappings/roster.json."""
    files = _decode_files(body.get("files"))
    if len(files) != 1 or not files[0][0].lower().endswith(".csv"):
        raise TeacherError("choose one .csv file")
    cls = ops.get_class(conn, str(body["class"])) if body.get("class") else None
    mapping = importers.load_mapping(ROOT / "mappings" / "roster.json")
    tmp = Path(tempfile.mkdtemp(prefix="daybook-roster-"))
    try:
        (tmp / files[0][0]).write_bytes(files[0][1])
        rep = importers.import_roster(conn, tmp / files[0][0], mapping, cls["id"] if cls else None,
                                      dry_run=not body.get("write"))
    except (ValueError, UnicodeDecodeError) as e:
        raise TeacherError(f"cannot read the CSV: {e}") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = {"ok": True, "written": bool(body.get("write")), "rows": rep.rows, "warnings": rep.warnings,
           "counts": {k: sum(1 for r in rep.rows if r["status"] == k) for k in ("new", "updated", "unchanged", "skipped")}}
    if body.get("write") and (rep.added or rep.updated):
        out["message"] = push_tokmap(conn, cfg)
    return out


# ---------------------------------------------------------------- G1: 점검

def doctor(conn: sqlite3.Connection, cfg: Config, db_path: str) -> dict:
    from . import doctor as D
    lines = D.run(conn, cfg, db_path, "dist")
    return {"ok": D.ok(lines), "lines": D.as_json(lines)}


# ---------------------------------------------------------------- G2: 백업

def backup_now(db_path: str, cfg: Config) -> dict:
    from . import backup as B
    try:
        r = B.run(db_path, cfg, keep=int(cfg.extra.get("backup_keep", 30)))
        return {"ok": True, "message": f"backup: {r.path.name} (integrity {r.integrity})", "path": str(r.path)}
    except (B.BackupError, OSError) as e:
        return {"ok": False, "message": f"warning: backup failed ({e})", "error": str(e)}
