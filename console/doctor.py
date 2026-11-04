"""G1: `daybook doctor`: one preflight before every class and every publish. Each line is ✓ / ⚠ / ✗ with a one-line fix.

Exit code 0 when there is no ✗. It only reads (the database, the Worker's /health and /tokmap-info, dist/); it never
sends a token, a name or a record anywhere.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import backup as B
from . import models as M
from .config import Config
from .db import SCHEMA_VERSION

MIN_WORKER_VERSION = 10       # 출석, 장면/Live, 푼 문제 수, 주간 테스트, /tokmap-info, /watch, push, consent, 학생 리뷰
OK, WARN, FAIL = "✓", "⚠", "✗"
REDEPLOY = "redeploy: cd worker && npx wrangler deploy"


@dataclass
class Line:
    status: str
    label: str
    detail: str = ""
    fix: str = ""


def tokmap_hash(tm: dict[str, str]) -> str:
    """The same hash as the Worker's /tokmap-info: sha256 of sorted "token:class" lines, first 16 hex digits."""
    text = "\n".join(f"{t}:{c}" for t, c in sorted(tm.items()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ago(ts: str, now: datetime) -> str:
    t = M.utc_iso_to_kst(ts)
    mins = int((now - t).total_seconds() // 60)
    when = t.strftime("%m/%d %H:%M")
    if mins < 60:
        return f"{when} ({max(0, mins)} min ago)"
    if mins < 48 * 60:
        return f"{when} ({mins // 60} h ago)"
    return f"{when} ({mins // 1440} days ago)"


def check_config(cfg: Config) -> list[Line]:
    out = [Line(OK, "daybook.json", cfg.path) if cfg.path else
           Line(FAIL, "daybook.json", "not found", "copy daybook.example.json to daybook.json and fill it in")]
    for key, fix in (("worker_url", "set worker_url (the Worker's workers.dev address) in daybook.json"),
                     ("pages_url", "set pages_url (https://<project>.pages.dev) in daybook.json"),
                     ("teacher_token", "set teacher_token (the same value as the Worker secret TEACHER_TOKEN)")):
        val = getattr(cfg, key)
        out.append(Line(OK, key, "set" if key == "teacher_token" else val) if val else Line(FAIL, key, "missing", fix))
    return out


def check_db(conn: sqlite3.Connection) -> list[Line]:
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    out = [Line(OK, "database schema", f"v{v}") if v == SCHEMA_VERSION else
           Line(WARN, "database schema", f"v{v}, this version expects v{SCHEMA_VERSION}", "run `daybook init` once (it migrates, keeping data)")]
    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
    out.append(Line(OK, "database integrity", "ok") if ic == "ok" else
               Line(FAIL, "database integrity", ic, "stop; restore the newest backup (daybook backup folder) and call for help"))
    return out


def check_worker(conn: sqlite3.Connection, cfg: Config, fetch: Callable[[str], bytes] | None) -> list[Line]:
    from .ops import tokmap
    from .pull import PullError, http_fetch
    if not cfg.worker or not cfg.teacher_token:
        return [Line(FAIL, "Worker", "not configured", "set worker_url and teacher_token in daybook.json")]
    try:
        fetch = fetch or http_fetch(cfg)
        h = json.loads(fetch("/health"))
    except (PullError, ValueError) as e:
        return [Line(FAIL, "Worker /health", str(e), "check worker_url and the internet; else " + REDEPLOY)]
    out = [Line(OK, "Worker /health", cfg.worker)]
    out.append(Line(OK, "Worker D1", "answers") if h.get("d1") else
               Line(FAIL, "Worker D1", "the database binding does not answer",
                    "check database_id in worker/wrangler.toml, then " + REDEPLOY))
    ver = int(h.get("version") or 0)
    out.append(Line(OK, "Worker version", f"v{ver}") if ver >= MIN_WORKER_VERSION else
               Line(WARN, "Worker version", f"v{ver} (needs v{MIN_WORKER_VERSION}: 출석, 주간 테스트, 푼 문제 수, 학생 리뷰)", REDEPLOY))
    try:
        info = json.loads(fetch("/tokmap-info"))
    except (PullError, ValueError) as e:
        if "401" in str(e):
            out.append(Line(FAIL, "student links on the Worker", "the teacher token is refused",
                            "teacher_token in daybook.json must equal the Worker secret (npx wrangler secret put TEACHER_TOKEN)"))
        else:
            out.append(Line(WARN, "student links on the Worker", "cannot be read (older Worker)", REDEPLOY))
        return out
    tm = tokmap(conn)
    if info.get("n") == len(tm) and info.get("hash") == tokmap_hash(tm):
        out.append(Line(OK, "student links on the Worker", f"{len(tm)} = the active roster"))
    else:
        out.append(Line(WARN, "student links on the Worker", f"Worker {info.get('n')} vs roster {len(tm)} (or different links)",
                        "press 발행 (or `daybook publish`) to send the current links"))
    return out


def check_today(conn: sqlite3.Connection, now: datetime) -> list[Line]:
    from . import rounds as R
    today = now.date()
    out = []
    for cls in conn.execute("SELECT * FROM classes ORDER BY id"):
        sess = conn.execute("SELECT * FROM sessions WHERE class_id = ? AND date = ?", (cls["id"], today.isoformat())).fetchone()
        if today not in M.class_days(conn, cls, today) and not sess:
            continue
        start = (sess["start_time"] if sess else None) or cls["start_time"]
        name = f"today {cls['name']}"
        out.append(Line(OK, name + ": start time", start) if start else
                   Line(WARN, name + ": start time", "not set", f"daybook class edit {cls['name']} --start 18:00 (or 반 수정)"))
        out.append(Line(OK, name + ": session", f"code {sess['code']}") if sess and sess["code"] else
                   Line(WARN, name + ": session", "not started", f"daybook session start {cls['name']} (or open the board)"))
        open_r = [r for r in conn.execute("SELECT * FROM rounds") if R.is_open(r, now) and cls["id"] in R.class_ids(r)]
        out.append(Line(OK, name + ": 주간 테스트", ", ".join(r["label"] for r in open_r) if open_r else "no open round"))
    if not out:
        out.append(Line(OK, "today", "no class today"))
    return out


def check_consent(conn: sqlite3.Connection) -> list[Line]:
    """F17: consent slips. unknown / no is a ⚠ (never ✗: the class routine works without it); the parent page,
    watch signals and push wait for a yes."""
    rows = conn.execute("""SELECT s.display_name, c.name AS class_name, s.consent FROM students s LEFT JOIN classes c ON c.id = s.class_id
                           WHERE s.active = 1 ORDER BY s.class_id, s.id""").fetchall()
    pending = [r for r in rows if r["consent"] != "yes"]
    if not rows:
        return [Line(OK, "consent", "no students")]
    if not pending:
        return [Line(OK, "consent", f"all {len(rows)} parents said yes")]
    names = ", ".join(f"{r['class_name']} {r['display_name']} ({r['consent']})" for r in pending[:12]) + (" …" if len(pending) > 12 else "")
    return [Line(WARN, "consent", f"{len(pending)} of {len(rows)} not yes: {names}",
                 "record the slips: roster edit <slug> --consent yes|no (or 연락 현황); no parent page until yes")]


def check_history(conn: sqlite3.Connection, cfg: Config, db_path: str | Path, now: datetime) -> list[Line]:
    out = []
    pub = conn.execute("SELECT ts FROM events WHERE type = 'publish' ORDER BY id DESC LIMIT 1").fetchone()
    out.append(Line(OK, "last publish", _ago(pub["ts"], now)) if pub else
               Line(WARN, "last publish", "never", "press 발행 (or `daybook publish`)"))
    dep = conn.execute("SELECT ts FROM events WHERE type = 'deploy' ORDER BY id DESC LIMIT 1").fetchone()
    out.append(Line(OK, "last deploy", _ago(dep["ts"], now)) if dep else
               Line(OK, "last deploy", "unknown (only the Teacher tab's 배포 button records it)"))
    folder = B.backup_dir(cfg, db_path)
    age = B.newest_age_days(folder, now.replace(tzinfo=None) if now.tzinfo else now)
    if age is None:
        out.append(Line(WARN, "last backup", f"none in {folder}", "daybook backup (or the 백업 button); 발행 also backs up"))
    elif age > B.MAX_AGE_DAYS:
        out.append(Line(WARN, "last backup", f"{age:.0f} days old ({folder})", "daybook backup"))
    else:
        out.append(Line(OK, "last backup", f"{age * 24:.0f} h old ({folder})"))
    rem = B.is_removable(folder)
    if rem is True:
        out.append(Line(OK, "backup drive", "removable (keep it BitLocker-encrypted)"))
    elif rem is False:
        out.append(Line(WARN, "backup drive", "not removable",
                        'set "backup_dir" in daybook.json to a folder on a BitLocker-encrypted USB drive'))
    else:
        out.append(Line(OK, "backup drive", "unknown on this system (use a BitLocker-encrypted USB drive)"))
    return out


def check_dist(conn: sqlite3.Connection, dist: str | Path) -> list[Line]:
    from . import lint
    from .config import strings
    d = Path(dist)
    if not (d / "index.html").exists():
        return [Line(WARN, "dist/ no-PII lint", f"no site in {d}", "press 발행 (or `daybook publish`)")]
    errs = lint.errors(lint.lint_dir(d, lint.private_rows(conn), strings()["page_title"]))
    if errs:
        return [Line(FAIL, "dist/ no-PII lint", f"{len(errs)} finding(s), e.g. {errs[0].kind} in {errs[0].file}",
                     "do not deploy; fix the source (usually a note or a display name) and press 발행 again")]
    return [Line(OK, "dist/ no-PII lint", "passed")]


def run(conn: sqlite3.Connection, cfg: Config, db_path: str | Path, dist: str | Path = "dist",
        fetch: Callable[[str], bytes] | None = None, now: datetime | None = None) -> list[Line]:
    now = now or M.kst_now()
    return (check_config(cfg) + check_db(conn) + check_worker(conn, cfg, fetch) + check_today(conn, now)
            + check_consent(conn) + check_history(conn, cfg, db_path, now) + check_dist(conn, dist))


def ok(lines: list[Line]) -> bool:
    return not any(x.status == FAIL for x in lines)


def as_json(lines: list[Line]) -> list[dict]:
    return [asdict(x) for x in lines]


def text(lines: list[Line]) -> str:
    out = []
    for x in lines:
        out.append(f"{x.status} {x.label}" + (f" — {x.detail}" if x.detail else ""))
        if x.fix and x.status != OK:
            out.append(f"    → {x.fix}")
    return "\n".join(out)
