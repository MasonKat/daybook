"""F21.3: each class has a colour and a short label; today's class is picked from the timetable; switching to another
class outside its time needs a confirmation; every saved row carries its class; history by class and day."""
from datetime import datetime

import pytest

from console import board as boardmod, ops, teacher as T
from console.config import Config
from console.db import KST
from console.importers import add_student

from conftest import TODAY   # Fri 2026-11-06


@pytest.fixture
def two(conn, tmp_path, monkeypatch):
    """고1 기본 (월수금 20:00-22:00) and 고2 (화목 18:00-20:00, its own colour)."""
    monkeypatch.chdir(tmp_path)
    for name, days, start, end in (("고1A", "월,수,금", "20:00", "22:00"), ("고2B", "화,목", "18:00", "20:00")):
        cls = ops.save_class(conn, name, create=True, term="2026-10-01..2027-02-26", days=days, start_time=start)
        ops.class_policy(conn, cls, {"end_time": end})
        add_student(conn, cls["id"], "한" + name)
    a, b = (conn.execute("SELECT * FROM classes WHERE name = ?", (n,)).fetchone() for n in ("고1A", "고2B"))
    ops.set_class_look(conn, a, label="고1 기본 월수금 20시")
    ops.set_class_look(conn, b, color="#db2777", label="고2 심화 화목 18시")
    conn.commit()
    return conn.execute("SELECT * FROM classes WHERE name = '고1A'").fetchone(), conn.execute("SELECT * FROM classes WHERE name = '고2B'").fetchone()


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def test_today_is_picked_from_the_timetable(conn, two):
    a, b = two
    assert ops.class_now(conn, at(2026, 11, 6, 20, 30))["name"] == "고1A"      # Fri, in class
    assert ops.class_now(conn, at(2026, 11, 6, 19, 35))["name"] == "고1A"      # 25 min before the start
    assert ops.class_now(conn, at(2026, 11, 5, 18, 10))["name"] == "고2B"      # Thu
    assert ops.class_now(conn, at(2026, 11, 5, 9, 0))["name"] == "고2B"        # Thu morning: today's class, nearest start
    assert ops.class_now(conn, at(2026, 11, 8, 20, 0)) is None                 # Sun: no class
    assert ops.in_window(a, at(2026, 11, 6, 22, 59)) and not ops.in_window(a, at(2026, 11, 6, 23, 1))


def test_banner_colour_and_label_per_class(conn, two):
    a, b = two
    ba, bb = ops.banner(a), ops.banner(b)
    assert (ba["label"], ba["days"], ba["window"]) == ("고1 기본 월수금 20시", "월·수·금", "20:00–22:00")
    assert bb["color"] == "#db2777" and ba["color"] in ops.PALETTE and ba["color"] != bb["color"]
    with pytest.raises(ops.OpError):
        ops.set_class_look(conn, a, color="pink")
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    bd = boardmod.Board(db, Config(), None, TODAY, poll=False, now=at(2026, 11, 6, 20, 5))
    assert bd.class_name == "고1A"                                               # auto-selected
    html = bd.page()
    assert 'style="--cb: ' + ba["color"] in html and "고1 기본 월수금 20시" in html and "월·수·금 · 20:00–22:00" in html
    assert "고2 심화 화목 18시" in html                                          # the switch lists every class by its label
    assert 'id="cbanner" style="--cb: ' + ba["color"] in bd.teacher_page()           # the Teacher tab's banner too
    bd2 = boardmod.Board(db, Config(), "고2B", TODAY, poll=False)
    assert 'style="--cb: #db2777"' in bd2.page()


def test_switch_outside_the_window_needs_a_confirmation(conn, two):
    a, b = two
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    bd = boardmod.Board(db, Config(), "고1A", TODAY, poll=False)
    r = bd.switch("고2B", now=at(2026, 11, 6, 20, 30))
    assert not r["ok"] and r["confirm"] == "지금 시간은 고1 기본 월수금 20시 수업이에요. 고2 심화 화목 18시 반으로 바꿀까요?"
    assert bd.class_name == "고1A"
    r = bd.switch("고2B", confirm=True, now=at(2026, 11, 6, 20, 30))
    assert r["ok"] and bd.class_name == "고2B" and bd.class_id == b["id"]
    assert bd.switch("고1A", now=at(2026, 11, 6, 20, 30))["ok"]                  # back to the class whose time it is: no question
    ev = conn.execute("SELECT payload_json FROM events WHERE type = 'board_switch'").fetchall()
    assert len(ev) == 2 and '"confirmed": true' in ev[0][0]


def test_history_by_class_and_day(conn, two):
    a, b = two
    sa = conn.execute("SELECT id FROM students WHERE class_id = ?", (a["id"],)).fetchone()[0]
    sess = ops.ensure_session(conn, a["id"], TODAY)
    ops.set_attitude(conn, sa, sess, 3)
    conn.execute("INSERT INTO shoutouts (student_id, class_id, day, ts, points, text) VALUES (?, ?, ?, 't', 1, '풀이 깔끔')", (sa, a["id"], TODAY.isoformat()))
    conn.commit()
    h = T.history(conn, "고1A", TODAY.isoformat())
    assert [x["points"] for x in h["attitude"]] == [3] and [x["text"] for x in h["shouts"]] == ["풀이 깔끔"]
    assert T.history(conn, "고2B", TODAY.isoformat())["attitude"] == []          # another class: its own rows only
    assert T.history(conn, "고1A", "2026-11-05")["shouts"] == []                  # another day


def test_class_edit_cli_color_and_label(conn, two, tmp_path):
    from click.testing import CliRunner
    from console.cli import cli
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "class", "edit", "고1A", "--color", "#16a34a", "--label", "고1 기본"])
    assert r.exit_code == 0
    row = conn.execute("SELECT color, label FROM classes WHERE name = '고1A'").fetchone()
    assert tuple(row) == ("#16a34a", "고1 기본")
    assert CliRunner().invoke(cli, ["--db", db, "class", "edit", "고1A", "--color", "red"]).exit_code != 0
