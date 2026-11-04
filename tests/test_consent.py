"""F17: the parent's consent slip. Without a yes: no parent page, no watch signal stored, no push subscription;
the student's own page (출석 / 과제) still works. doctor shows unknown as ⚠, never ✗."""
import json
from datetime import datetime, time

from click.testing import CliRunner

from console import doctor as D, generate, ops, pull as pullmod
from console.cli import cli
from console.config import Config
from console.db import KST
from console.importers import add_student

from conftest import TODAY

NOW = datetime.combine(TODAY, time(21), KST)


def test_unknown_gets_no_parent_page_and_no_watch_a_yes_gets_both(conn, klass, tmp_path):
    cls, studs = klass
    new = add_student(conn, cls["id"], "도하윤", ext_id="S950")                 # a new student: consent unknown
    assert new["consent"] == "unknown"
    conn.commit()
    cfg = Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t", extra={"vapid_public": "BP4z"})
    res = generate.publish(conn, cfg, tmp_path / "d", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings]
    assert (tmp_path / "d" / "s" / new["student_token"] / "index.html").exists()        # the student's routine works
    assert not (tmp_path / "d" / "p" / new["parent_token"]).exists()                      # no parent page
    s_html = (tmp_path / "d" / "s" / new["student_token"] / "index.html").read_text(encoding="utf-8")
    assert '"watch":false' in s_html and 'id="push"' not in s_html
    yes_html = (tmp_path / "d" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert '"watch":true' in yes_html and 'id="push"' in yes_html
    assert (tmp_path / "d" / "p" / studs[0]["parent_token"] / "index.html").exists()
    items = [{"token": t, "v": "AMPvideo001", "day": "2026-11-05", "sec": 60, "pct": 50, "ts": "t"}
             for t in (new["student_token"], studs[0]["student_token"])]
    assert pullmod.apply_watches(conn, items) == 1
    assert [r[0] for r in conn.execute("SELECT student_id FROM watches")] == [studs[0]["id"]]
    assert ops.no_consent_tokens(conn) == [new["student_token"]]
    assert new["parent_token"] not in ops.parent_tokmap(conn) and studs[0]["parent_token"] in ops.parent_tokmap(conn)
    # yes on the slip: both appear
    ops.edit_student(conn, new["slug"], consent="yes")
    conn.commit()
    generate.publish(conn, cfg, tmp_path / "d2", today=TODAY, now=NOW)
    assert (tmp_path / "d2" / "p" / new["parent_token"] / "index.html").exists() and ops.no_consent_tokens(conn) == []
    assert pullmod.apply_watches(conn, items[:1]) == 1
    ev = [json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM events WHERE type = 'consent'")]
    assert ev == [{"student_id": new["id"], "consent": "yes", "was": "unknown"}]


def test_cli_consent_tokmap_and_doctor(conn, klass, tmp_path, monkeypatch):
    _, studs = klass
    monkeypatch.chdir(tmp_path)
    sent = {}
    monkeypatch.setattr(pullmod, "http_put_json", lambda cfg, path, payload: sent.update(payload) or {"n": 3})
    monkeypatch.setenv("DAYBOOK_WORKER_URL", "https://w.example")
    monkeypatch.setenv("DAYBOOK_TEACHER_TOKEN", "t")
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "roster", "edit", studs[1]["slug"], "--consent", "no"])
    assert r.exit_code == 0 and sent["no_consent"] == [studs[1]["student_token"]]           # the Worker learns at once
    assert CliRunner().invoke(cli, ["--db", db, "roster", "edit", studs[1]["slug"], "--consent", "maybe"]).exit_code != 0
    line = D.check_consent(conn)[0]
    assert line.status == D.WARN and "1 of 3" in line.detail and "도담" in line.detail and "(no)" in line.detail
    conn.execute("UPDATE students SET consent = 'yes'")
    assert D.check_consent(conn)[0].status == D.OK


def test_teacher_tab_shows_and_sets_consent(conn, klass):
    from console import contact as C
    from test_teacher import call  # noqa: F401
    _, studs = klass
    st = {x["id"]: x["consent"] for x in C.status(conn, Config(), TODAY)}
    assert set(st.values()) == {"yes"}
