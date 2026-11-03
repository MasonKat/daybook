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


def roster(conn: sqlite3.Connection, cfg: Config, class_id: int) -> list[dict]:
    return [{"slug": r["slug"], "display_name": r["display_name"], "active": bool(r["active"]),
             "student_link": _link(cfg, "s", r["student_token"]), "parent_link": _link(cfg, "p", r["parent_token"])}
            for r in conn.execute("SELECT * FROM students_public WHERE class_id = ? ORDER BY active DESC, id", (class_id,))]


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
        else:
            raise TeacherError("kind must be screeners or tests")
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


def publish(conn: sqlite3.Connection, cfg: Config, out: Path | None = None) -> dict:
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
