"""G1: `daybook doctor`, one test per check, with a fake Worker."""
import json
import os
import time as _time
from datetime import datetime, timedelta

from click.testing import CliRunner

from console import doctor as D, generate, ops
from console.cli import cli
from console.config import Config
from console.db import KST
from console.pull import PullError

NOW = datetime(2026, 11, 6, 17, 30, tzinfo=KST)    # a Friday: the klass fixture meets Tue + Fri


def cfg(tmp_path, **kw):
    return Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t",
                  path=str(tmp_path / "daybook.json"), extra={"backup_dir": str(tmp_path / "bk"), **kw})


def worker(conn, version=6, d1=True, tokmap=None, health_error=None, info_status=None):
    def fetch(path):
        if path == "/health":
            if health_error:
                raise PullError(health_error)
            return json.dumps({"ok": True, "version": version, "d1": d1}).encode()
        if path == "/tokmap-info":
            if info_status:
                raise PullError(f"Worker answered {info_status} for /tokmap-info")
            tm = ops.tokmap(conn) if tokmap is None else tokmap
            return json.dumps({"ok": True, "n": len(tm), "hash": D.tokmap_hash(tm)}).encode()
        raise AssertionError(path)
    return fetch


def by_label(lines):
    return {x.label: x for x in lines}


def test_config_lines():
    lines = by_label(D.check_config(Config()))
    assert lines["daybook.json"].status == D.FAIL and "daybook.example.json" in lines["daybook.json"].fix
    assert all(lines[k].status == D.FAIL for k in ("worker_url", "pages_url", "teacher_token"))
    good = by_label(D.check_config(Config(worker_url="https://w", pages_url="https://p", teacher_token="secret", path="/x/daybook.json")))
    assert all(x.status == D.OK for x in good.values()) and "secret" not in json.dumps(D.as_json(list(good.values())))


def test_db_lines(conn):
    lines = by_label(D.check_db(conn))
    assert lines["database schema"].status == D.OK and lines["database integrity"].detail == "ok"
    conn.execute("PRAGMA user_version = 1")
    assert by_label(D.check_db(conn))["database schema"].status == D.WARN


def test_worker_lines(conn, klass, tmp_path):
    c = cfg(tmp_path)
    lines = by_label(D.check_worker(conn, c, worker(conn)))
    assert [lines[k].status for k in ("Worker /health", "Worker D1", "Worker version", "student links on the Worker")] == [D.OK] * 4
    assert by_label(D.check_worker(conn, c, worker(conn, health_error="cannot reach the Worker")))["Worker /health"].status == D.FAIL
    assert by_label(D.check_worker(conn, c, worker(conn, d1=False)))["Worker D1"].status == D.FAIL
    old = by_label(D.check_worker(conn, c, worker(conn, version=4)))["Worker version"]
    assert old.status == D.WARN and "npx wrangler deploy" in old.fix
    stale = by_label(D.check_worker(conn, c, worker(conn, tokmap={"x" * 26: "k"})))["student links on the Worker"]
    assert stale.status == D.WARN and "발행" in stale.fix
    bad = by_label(D.check_worker(conn, c, worker(conn, info_status=401)))["student links on the Worker"]
    assert bad.status == D.FAIL and "TEACHER_TOKEN" in bad.fix
    assert D.check_worker(conn, Config(), None)[0].status == D.FAIL


def test_today_lines(conn, klass):
    cls, _ = klass
    lines = by_label(D.check_today(conn, NOW))
    assert lines["today 고1A: start time"].status == D.WARN and lines["today 고1A: session"].status == D.WARN
    assert lines["today 고1A: 주간 테스트"].detail == "no open round"
    conn.execute("UPDATE classes SET start_time = '18:00' WHERE id = ?", (cls["id"],))
    ops.start_session(conn, conn.execute("SELECT * FROM classes WHERE id = ?", (cls["id"],)).fetchone(), NOW.date())
    from console import rounds as R
    R.add(conn, "Adaptive_Screeners_1S_Nov06_G10", "1학년", "고1A", 20, "2026-11-06 17:00", "2026-11-08 23:59")
    lines = by_label(D.check_today(conn, NOW))
    assert [lines[f"today 고1A: {k}"].status for k in ("start time", "session", "주간 테스트")] == [D.OK] * 3
    assert lines["today 고1A: 주간 테스트"].detail == "11/6 주간 테스트"
    assert D.check_today(conn, NOW + timedelta(days=1))[0].detail == "no class today"      # Saturday


def test_history_lines(conn, klass, tmp_path):
    c = cfg(tmp_path)
    db = tmp_path / "t.sqlite"
    lines = by_label(D.check_history(conn, c, db, NOW))
    assert lines["last publish"].status == D.WARN and lines["last backup"].status == D.WARN
    assert lines["last deploy"].detail.startswith("unknown")
    from console.db import log_event
    log_event(conn, "publish", {}, (NOW - timedelta(hours=3)).astimezone().isoformat())
    log_event(conn, "deploy", {}, (NOW - timedelta(hours=2)).astimezone().isoformat())
    (tmp_path / "bk").mkdir()
    old = tmp_path / "bk" / "daybook-20261020-2000.sqlite"
    old.write_bytes(b"x")
    t = (NOW - timedelta(days=10)).timestamp()
    os.utime(old, (t, t))
    lines = by_label(D.check_history(conn, c, db, NOW))
    assert lines["last publish"].status == D.OK and "3 h ago" in lines["last publish"].detail
    assert lines["last deploy"].status == D.OK and lines["last backup"].status == D.WARN and "10 days" in lines["last backup"].detail
    new = tmp_path / "bk" / "daybook-20261106-1700.sqlite"
    new.write_bytes(b"x")
    t = (NOW - timedelta(hours=1)).timestamp()
    os.utime(new, (t, t))
    assert by_label(D.check_history(conn, c, db, NOW))["last backup"].status == D.OK
    assert "backup drive" in lines


def test_dist_line(conn, klass, tmp_path):
    assert D.check_dist(conn, tmp_path / "nothing")[0].status == D.WARN
    c = cfg(tmp_path)
    res = generate.publish(conn, c, tmp_path / "dist", today=NOW.date(), now=NOW)
    assert res.ok and D.check_dist(conn, tmp_path / "dist")[0].status == D.OK
    _, studs = klass
    page = next((tmp_path / "dist" / "p").iterdir()) / "index.html"
    page.write_text(page.read_text(encoding="utf-8") + studs[0]["full_name"], encoding="utf-8")
    line = D.check_dist(conn, tmp_path / "dist")[0]
    assert line.status == D.FAIL and "full_name" in line.detail


def test_cli_exit_code(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "doctor"])
    assert r.exit_code == 1 and "✗ daybook.json" in r.output and "→ " in r.output     # no config: ✗, with a fix
    r = CliRunner().invoke(cli, ["--db", db, "doctor", "--json"])
    assert any(x["status"] == "✗" for x in json.loads(r.output))
