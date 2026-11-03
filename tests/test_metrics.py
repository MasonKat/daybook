"""F16.4: weekly metrics, class level only, with the demo."""
import csv
from datetime import date, timedelta

from click.testing import CliRunner

from console import contact as C, demo, metrics as MT, ops
from console.cli import cli
from console.config import Config

from conftest import TODAY


def test_metrics_with_the_demo(conn, tmp_path, monkeypatch):
    demo.seed(conn, today=TODAY)
    cfg = Config()
    rows = MT.weekly(conn, cfg, None, 8, TODAY)
    assert len(rows) == 8 and [r["week"] for r in rows][-1] == "2026-W45"
    last = rows[-1]
    for k in ("late_rate", "record_rate", "contact_coverage_4w", "retention"):
        assert 0 <= float(last[k]) <= 1, k
    assert last["students"] == 22 and last["slc"] == 2 and last["test_insufficient"] == 22 and last["test_improved_share"] == ""
    assert float(last["late_rate"]) > 0                                          # the late star alone makes sure of it
    # retention follows 퇴원 (the roster at the baseline date is the reference)
    slug = conn.execute("SELECT slug FROM students WHERE ext_id = 'S922'").fetchone()[0]
    ops.set_left(conn, slug, (TODAY - timedelta(days=1)).isoformat())
    assert MT.weekly(conn, cfg, None, 1, TODAY)[0]["retention"] == f"{21 / 22:.3f}"
    # test trend: own first 3 rounds vs own last 3 (6+ rounds needed)
    sid = conn.execute("SELECT id FROM students WHERE ext_id = 'S901'").fetchone()[0]
    conn.execute("DELETE FROM tests WHERE student_id = ?", (sid,))
    for i, e in enumerate((.5, .5, .5, .6, .7, .8)):
        conn.execute("INSERT INTO tests (student_id, date, test_name, efficiency) VALUES (?, ?, ?, ?)", (sid, f"2026-10-0{i + 1}", f"r{i}", e))
    r = MT.weekly(conn, cfg, "고1A", 1, TODAY)[0]
    assert (r["test_improved_share"], r["test_insufficient"]) == ("1.000", 4) and r["class"] == "고1A"
    # trigger -> call within 7 days: the late star is flagged this week; a call afterwards counts
    star = conn.execute("SELECT id FROM students WHERE ext_id = 'S916'").fetchone()[0]
    before = MT.weekly(conn, cfg, "심화C", 1, TODAY)[0]["trigger_call_7d"]
    C.log_contact(conn, star, "call", "", tag="trigger", ts="2026-11-06T09:00:00.000Z")
    after = MT.weekly(conn, cfg, "심화C", 1, TODAY)[0]["trigger_call_7d"]
    assert float(after) > float(before or 0)
    # the CSV: exact columns, identical bytes everywhere, no name anywhere
    conn.commit()
    monkeypatch.chdir(tmp_path)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    res = CliRunner().invoke(cli, ["--db", db, "metrics", "weekly", "--date", TODAY.isoformat()])
    assert res.exit_code == 0, res.output
    path = tmp_path / "reports" / "metrics_2026-W45.csv"
    raw = path.read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8-sig")
    assert next(csv.reader(text.splitlines())) == MT.COLUMNS and len(text.splitlines()) == 9
    for full, disp, ext in conn.execute("SELECT full_name, display_name, ext_id FROM students"):
        assert full not in text and ext not in text and f",{disp}," not in text


def test_meeting_log(conn, klass, tmp_path, monkeypatch):
    _, studs = klass
    monkeypatch.chdir(tmp_path)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    assert CliRunner().invoke(cli, ["--db", db, "meeting", "log", studs[0]["slug"], "--kind", "slc", "--date", "2026-11-05"]).exit_code == 0
    assert conn.execute("SELECT kind, day FROM meetings").fetchone()[:] == ("slc", "2026-11-05")
    assert MT.weekly(conn, Config(), "고1A", 1, TODAY)[0]["slc"] == 1
