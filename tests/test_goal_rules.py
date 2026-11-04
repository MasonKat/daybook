"""F22.1: per-class goal rules (coefficient, labelled milestones, a term or month cycle, the rubric) and the rule sheet."""
import json
from datetime import date, datetime, time

import pytest

from console import board as boardmod, generate, models as M, ops
from console.config import Config
from console.db import KST
from console.importers import add_student

from conftest import TODAY


def mk(conn, name, coef, days="화,금"):
    cls = ops.save_class(conn, name, create=True, term="2026-11-01..2027-01-31", days=days, start_time="18:00")
    conn.execute("UPDATE classes SET goal_coef = ? WHERE id = ?", (coef, cls["id"]))
    sur = {"가반": "김", "나반": "이"}[name]
    for given in ("가람", "도담", "하늘", "새벽"):
        add_student(conn, cls["id"], sur + given)
    conn.commit()
    return conn.execute("SELECT * FROM classes WHERE id = ?", (cls["id"],)).fetchone()


def score(conn, cls, day, pts):
    sess = ops.ensure_session(conn, cls["id"], day)
    for s in conn.execute("SELECT id FROM students WHERE class_id = ?", (cls["id"],)).fetchall():
        ops.set_attitude(conn, s[0], sess, pts)


def test_the_coefficient_is_per_class(conn):
    a, b = mk(conn, "가반", 2.0), mk(conn, "나반", 3.0)
    ga, gb = M.reward_for(conn, a, TODAY).goal, M.reward_for(conn, b, TODAY).goal
    assert gb == pytest.approx(ga * 1.5, abs=1) and ga > 0


def test_milestones_fire_their_labels(conn):
    a = mk(conn, "가반", 2.0)
    ops.set_goal_rules(conn, a, "33:간식, 66:자유석 하루, 100:치킨 데이")
    conn.execute("UPDATE classes SET reward_goal_points = 30 WHERE id = ?", (a["id"],))
    a = conn.execute("SELECT * FROM classes WHERE id = ?", (a["id"],)).fetchone()
    score(conn, a, date(2026, 11, 3), 3)                       # 4 students x 3 = 12 points
    r = M.reward_for(conn, a, TODAY)
    assert [(m["label"], m["points"], m["reached"]) for m in r.ladder] == [("간식", 10, True), ("자유석 하루", 20, False), ("치킨 데이", 30, False)]
    assert r.milestones == [10, 20]
    with pytest.raises(ops.OpError):
        ops.parse_milestones("10:a,20:b,30:c,40:d")
    with pytest.raises(ops.OpError):
        ops.parse_milestones("150:너무 많이")


def test_month_cycle_restarts_on_the_first_and_keeps_history(conn):
    a = mk(conn, "가반", 2.0)
    ops.set_goal_rules(conn, a, "50:간식, 100:보드게임 데이", "month")
    a = conn.execute("SELECT * FROM classes WHERE id = ?", (a["id"],)).fetchone()
    score(conn, a, date(2026, 11, 3), 3)                       # November: 12 points
    nov = M.reward_for(conn, a, date(2026, 11, 20), freeze=True)
    assert nov.cycle == "month" and nov.total == 12 and nov.since == date(2026, 11, 1) and nov.goal > 0
    frozen = conn.execute("SELECT goal FROM goal_cycles WHERE class_id = ? AND start = '2026-11-01'", (a["id"],)).fetchone()[0]
    assert frozen == nov.goal
    score(conn, a, date(2026, 12, 1), 1)                       # December 1st: a new cycle
    dec = M.reward_for(conn, a, date(2026, 12, 1), freeze=True)
    assert dec.total == 4 and dec.since == date(2026, 12, 1)
    hist = M.cycle_history(conn, a, date(2026, 12, 1))
    assert hist[0]["start"] == "2026-11-01" and hist[0]["total"] == 12 and hist[0]["goal"] == frozen
    assert M.class_total(conn, a["id"]) == 16                  # the term total still adds everything (never decreases)


def test_rule_sheet_renders_for_students_and_on_the_board(conn, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = mk(conn, "가반", 2.0)
    ops.set_goal_rules(conn, a, "33:간식, 66:자유석 하루, 100:치킨 데이", None, ["출석 + 과제", "", ""])
    score(conn, a, date(2026, 11, 3), 2)
    conn.commit()
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY, now=datetime.combine(TODAY, time(21), KST)).ok
    tok = conn.execute("SELECT student_token FROM students WHERE class_id = ? LIMIT 1", (a["id"],)).fetchone()[0]
    html = (tmp_path / "d" / "s" / tok / "index.html").read_text(encoding="utf-8")
    sheet = html.split('class="rules"')[1].split('</details>')[0]
    assert "치킨 데이" in sheet and "출석 + 과제" in sheet and M.DEFAULT_RUBRIC["3"] in sheet          # a custom 1점, default 2·3점
    assert "AMP Live 반 정답 80% 이상 → +5" in sheet and "점수는 깎이지 않아요" in sheet
    for s in conn.execute("SELECT display_name FROM students"):
        assert s[0] not in sheet                                # never a name
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    page = boardmod.Board(db, Config(), "가반", TODAY, poll=False).page()
    assert "규칙 보기" in page and "자유석 하루" in page


def test_cli_class_edit_rules(conn, tmp_path):
    from click.testing import CliRunner
    from console.cli import cli
    mk(conn, "가반", 2.0)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "class", "edit", "가반", "--milestones", "50:간식,100:피자", "--cycle", "month",
                                 "--rubric", "출석|발표|끝까지"])
    assert r.exit_code == 0
    c = conn.execute("SELECT milestones, cycle, rubric FROM classes WHERE name = '가반'").fetchone()
    assert json.loads(c[0])[1] == {"pct": 100, "label": "피자"} and c[1] == "month" and json.loads(c[2])["2"] == "발표"
    assert CliRunner().invoke(cli, ["--db", db, "class", "edit", "가반", "--milestones", "abc"]).exit_code != 0
