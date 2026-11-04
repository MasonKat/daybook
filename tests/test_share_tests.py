"""F21.1: weekly-test results are research data, private by default: the parent page, the weekly message and the
parent-facing contact drafts show them only when both the student and the parent agreed (students.share_tests = yes)."""
from datetime import datetime, time
from pathlib import Path

from console import contact as CT, generate, importers, metrics as MT, ops
from console.config import Config, strings
from console.db import KST

from conftest import TODAY

NOW = datetime.combine(TODAY, time(21), KST)
SCREENERS = Path(__file__).resolve().parent.parent / "demo" / "screeners"


def page(tmp_path, out, s, kind="p"):
    return (tmp_path / out / kind / s[f"{'parent' if kind == 'p' else 'student'}_token"] / "index.html").read_text(encoding="utf-8")


def test_default_hides_the_test_row_yes_shows_it(conn, cfg, klass, tmp_path):
    cls, studs = klass
    importers.import_screeners(conn, SCREENERS)
    conn.commit()
    assert [r[0] for r in conn.execute("SELECT DISTINCT share_tests FROM students")] == ["no"]      # the default
    assert generate.publish(conn, cfg, tmp_path / "a", today=TODAY, now=NOW).ok
    p = page(tmp_path, "a", studs[0])
    assert "주간 테스트" not in p and "<svg" not in p and "확보 효율" not in p and "도전 도달" not in p
    s_page = page(tmp_path, "a", studs[0], "s")
    ops.edit_student(conn, studs[0]["slug"], share_tests="yes")
    conn.commit()
    assert generate.publish(conn, cfg, tmp_path / "b", today=TODAY, now=NOW).ok
    p = page(tmp_path, "b", studs[0])
    assert "주간 테스트" in p and p.count("<svg") == 1 and "확보 효율" in p
    assert "주간 테스트" not in page(tmp_path, "b", studs[1])                    # only that child
    assert "주간 테스트" in s_page or me_has_trend(conn, cfg, studs[0]["id"])       # the student's own results stay theirs
    ev = conn.execute("SELECT payload_json FROM events WHERE type = 'share_tests'").fetchall()
    assert len(ev) == 1 and '"share_tests": "yes"' in ev[0][0] and '"was": "no"' in ev[0][0]


def me_has_trend(conn, cfg, sid):
    ctx = generate.build_context(conn, cfg, TODAY, NOW)
    conn.rollback()
    return next(s for s in ctx["students"] if s["id"] == sid)["trend"] is not None


def test_the_students_own_page_keeps_their_results(conn, cfg, klass, tmp_path):
    _, studs = klass
    importers.import_screeners(conn, SCREENERS)
    conn.commit()
    ctx = generate.build_context(conn, cfg, TODAY, NOW)
    me = next(s for s in ctx["students"] if s["id"] == studs[0]["id"])
    assert me["share_tests"] is False and me["trend"] is not None                 # the data is there for the student


def test_weekly_message_mentions_scores_only_when_shared(conn, cfg, klass):
    from console.weekly import messages
    cls, studs = klass
    importers.import_screeners(conn, SCREENERS)
    conn.commit()
    msgs = messages(conn, cfg, cls, TODAY)
    assert msgs and not any("주간 테스트" in m for m in msgs)
    ops.edit_student(conn, studs[0]["slug"], share_tests="yes")
    conn.commit()
    msgs = messages(conn, cfg, cls, TODAY)
    assert sum("주간 테스트" in m for m in msgs) == 1


def test_parent_facing_contact_drafts_quote_no_test_numbers_unless_shared(conn, cfg, klass):
    cls, studs = klass
    for i, eff in enumerate((0.9, 0.9, 0.9, 0.6)):
        conn.execute("INSERT INTO tests (student_id, test_name, date, efficiency, reach) VALUES (?, ?, ?, ?, ?)",
                     (studs[0]["id"], f"r{i}", f"2026-10-{10 + i:02d}", eff, 0.5))
    conn.commit()
    s = conn.execute("SELECT * FROM students WHERE id = ?", (studs[0]["id"],)).fetchone()
    S = strings()["parent_msg"]
    test_flag = next(f for f in CT.rules_for(conn, cfg, s, cls, TODAY) if f["rule"] == "test")
    assert test_flag["fact"] == S["fact_test_private"] and "%" in test_flag["reason"]     # the teacher still sees why
    assert not any("확보 효율" in f for f in CT.good_facts(conn, s, cls, TODAY))
    conn.execute("UPDATE tests SET efficiency = 0.92 WHERE test_name = 'r3'")
    ops.edit_student(conn, studs[0]["slug"], share_tests="yes")
    s = conn.execute("SELECT * FROM students WHERE id = ?", (studs[0]["id"],)).fetchone()
    assert any("확보 효율" in f for f in CT.good_facts(conn, s, cls, TODAY))


def test_export_and_metrics_are_unaffected(conn, cfg, klass, tmp_path):
    importers.import_screeners(conn, SCREENERS)
    conn.commit()
    before = MT.weekly(conn, cfg, None, 4, TODAY)
    conn.execute("UPDATE students SET share_tests = 'yes'")
    assert MT.weekly(conn, cfg, None, 4, TODAY) == before                          # teacher-only numbers, both ways


def test_the_letter_has_the_second_tick_box(conn, klass, tmp_path):
    from console import cards
    cls, _ = klass
    html = cards.render(conn, "parents", cls["name"])
    P = strings()["parent_msg"]
    assert P["letter_slip_tests"] == "주간 테스트 결과를 학부모 페이지에서 보기:"
    assert (P["letter_slip_tests_student"], P["letter_slip_tests_parent"]) == ("학생 동의", "보호자 동의")
    tpl = (Path(__file__).resolve().parent.parent / "templates" / "cards_parents.html").read_text(encoding="utf-8")
    assert "letter_slip_tests_student" in tpl and "letter_slip_tests_parent" in tpl
    assert "주간 테스트 결과를 학부모 페이지에서 보기" in html and "학생 동의" in html and "보호자 동의" in html


def test_cli_share_tests(conn, klass, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from console.cli import cli
    _, studs = klass
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli, ["--db", db, "roster", "edit", studs[1]["slug"], "--share-tests", "yes"])
    assert r.exit_code == 0 and conn.execute("SELECT share_tests FROM students WHERE id = ?", (studs[1]["id"],)).fetchone()[0] == "yes"
    assert CliRunner().invoke(cli, ["--db", db, "roster", "edit", studs[1]["slug"], "--share-tests", "maybe"]).exit_code != 0
