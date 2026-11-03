"""G2: `daybook backup`: the online backup API, integrity_check on the copy, keep the newest N, after every 발행."""
import sqlite3

import pytest
from click.testing import CliRunner

from console import backup as B
from console.cli import cli
from console.config import Config


def test_backup_is_a_checked_copy_and_keeps_the_newest(conn, klass, tmp_path, monkeypatch):
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    cfg = Config(extra={"backup_dir": "bk"})
    other = sqlite3.connect(db)                     # the board holds an open write transaction meanwhile
    other.execute("INSERT INTO events (type, payload_json, ts) VALUES ('x', '{}', '2026-11-06T00:00:00Z')")
    r = B.run(db, cfg)
    other.rollback(); other.close()
    assert r.integrity == "ok" and r.path.parent == tmp_path / "bk" and r.path.name.startswith("daybook-")
    copy = sqlite3.connect(r.path)
    assert copy.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 3                 # committed data only
    assert copy.execute("SELECT COUNT(*) FROM events WHERE type = 'x'").fetchone()[0] == 0
    copy.close()
    paths = [B.run(db, cfg, keep=3).path for _ in range(4)]                                  # same minute: -2, -3 …
    kept = B.existing(tmp_path / "bk")
    assert len(kept) == 3 and kept[0] == paths[-1] and paths[-1].exists()
    assert B.newest_age_days(tmp_path / "bk") < 0.01
    with pytest.raises(B.BackupError):
        B.run(tmp_path / "missing.sqlite", cfg)
    assert not list((tmp_path / "bk").glob("*.partial"))


def test_cli_backup_and_publish_backs_up(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "backup", "--to", str(tmp_path / "usb"), "--keep", "2"])
    assert r.exit_code == 0 and "integrity ok" in r.output and len(B.existing(tmp_path / "usb")) == 1
    r = CliRunner().invoke(cli, ["--db", db, "publish", "--out", str(tmp_path / "dist")])
    assert r.exit_code == 0 and "backup: " in r.output
    assert len(B.existing(tmp_path / "backups")) == 1                                        # default: next to the database


def test_backup_button(tmp_path, monkeypatch, conn, klass):
    import test_teacher
    from console import board as boardmod, pull as pullmod
    import threading
    from http.server import ThreadingHTTPServer
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pullmod, "http_put_json", lambda cfg, path, payload: {"n": 0})
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    b = boardmod.Board(db, Config(moments_dir=str(tmp_path / "m")), "고1A", test_teacher.TODAY, poll=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), boardmod.make_handler(b))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        code, j = test_teacher.call(f"http://127.0.0.1:{httpd.server_address[1]}/api/teacher/backup", {})
        assert code == 200 and j["ok"] and "integrity ok" in j["message"]
        code, j = test_teacher.call(f"http://127.0.0.1:{httpd.server_address[1]}/api/teacher/publish", {})
        assert j["ok"] and any(s.startswith("backup: ") for s in j["steps"])
    finally:
        httpd.shutdown()
    assert len(B.existing(tmp_path / "backups")) == 2
