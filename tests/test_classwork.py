"""F21.1 (share_classwork), F21.2 (수업 문제 + the skill map), F21.4 (서술형) and F21.5 (photo-grading import)."""
import json
from datetime import datetime, time, timedelta

import pytest

from console import classwork as CW, generate, ops, pull as pullmod, rounds as R
from console.config import Config, strings
from console.db import KST
from console.ops import class_key

from conftest import TODAY

NOW = datetime.combine(TODAY, time(21), KST)
TAGS = {"sets": {"쎈 A": {"1": "이차방정식/판별식", "2": "이차방정식/판별식", "3": "부등식/산술·기하평균", "4": "명제/역과 대우"},
                 "Adaptive_Screeners_1S_Nov06_G10": {"2": "이차방정식/판별식", "5": "명제/역과 대우"},
                 "11/6 서술형": {"1": "부등식/산술·기하평균"}},
        "_comment": "문제 1. x^2+2x+5=0의 판별식을 구하시오 (quiz text must never travel)"}


@pytest.fixture
def cw(conn, klass, tmp_path):
    cls, studs = klass
    (tmp_path / "tags.json").write_text(json.dumps(TAGS, ensure_ascii=False), encoding="utf-8")
    cfg = Config(extra={"classwork_tags": str(tmp_path / "tags.json")})
    return cls, studs, cfg


def put_set(conn, cls, sid, label, n, day=TODAY):
    conn.execute("INSERT INTO classwork_sets (sid, class_id, day, label, n, ts) VALUES (?, ?, ?, ?, ?, 't')", (sid, cls["id"], day.isoformat(), label, n))


def put_marks(conn, sid, student_id, marks):
    conn.execute("INSERT OR REPLACE INTO classwork_marks (sid, student_id, marks, updated) VALUES (?, ?, ?, 't')", (sid, student_id, marks))


def tag(sm, full):
    return next(t for u in sm["units"] for t in u["tags"] if t["full"] == full)


def test_dash_is_its_own_mark_never_x_and_the_rate_leaves_it_out(conn, cw):
    cls, studs, cfg = cw
    put_set(conn, cls, "a" * 16, "쎈 A", 4)
    put_marks(conn, "a" * 16, studs[0]["id"], "omox")                     # 판별식: ○ –; 산술·기하: ○; 역과 대우: ✕
    put_set(conn, cls, "b" * 16, "쎈 A", 4, TODAY - timedelta(days=1))
    put_marks(conn, "b" * 16, studs[0]["id"], "oxmm")
    sm = CW.skill_map(conn, cfg, studs[0]["id"])
    d = tag(sm, "이차방정식/판별식")
    assert (d["o"], d["x"], d["m"]) == (2, 1, 1) and d["rate"] == 67               # ○ / (○ + ✕): – is not ✕
    assert sm["totals"] == {"o": 3, "x": 2, "m": 3, "p": 0}
    assert [u["unit"] for u in sm["units"]] == ["명제", "부등식", "이차방정식"]


def test_fewer_than_three_attempts_is_data_too_few(conn, cw, tmp_path):
    cls, studs, cfg = cw
    put_set(conn, cls, "a" * 16, "쎈 A", 4)
    put_marks(conn, "a" * 16, studs[0]["id"], "oooo")
    sm = CW.skill_map(conn, cfg, studs[0]["id"])
    assert tag(sm, "명제/역과 대우")["status"] == "few" and tag(sm, "이차방정식/판별식")["status"] == "few"
    conn.commit()
    assert generate.publish(conn, cfg, tmp_path / "d", today=TODAY, now=NOW).ok
    html = (tmp_path / "d" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert "내 실력 지도" in html and "데이터 부족" in html


def test_parent_page_only_when_share_classwork_yes(conn, cw, tmp_path):
    cls, studs, cfg = cw
    put_set(conn, cls, "a" * 16, "쎈 A", 4)
    put_marks(conn, "a" * 16, studs[0]["id"], "oxmo")
    conn.commit()
    assert generate.publish(conn, cfg, tmp_path / "a", today=TODAY, now=NOW).ok
    parent = lambda out: (tmp_path / out / "p" / studs[0]["parent_token"] / "index.html").read_text(encoding="utf-8")   # noqa: E731
    assert "수업 중 문제 풀이" not in parent("a") and "판별식" not in parent("a")
    assert "판별식" in (tmp_path / "a" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")   # own page: always
    ops.edit_student(conn, studs[0]["slug"], share_classwork="yes")
    conn.commit()
    assert generate.publish(conn, cfg, tmp_path / "b", today=TODAY, now=NOW).ok
    assert "수업 중 문제 풀이" in parent("b") and "판별식" in parent("b")
    ev = conn.execute("SELECT payload_json FROM events WHERE type = 'share_classwork'").fetchone()[0]
    assert '"share_classwork": "yes"' in ev and '"was": "no"' in ev
    assert "주간 테스트" in parent("a")                                          # the weekly test stays as it was


def test_no_quiz_text_or_tags_in_worker_requests_or_published_files(conn, cw, tmp_path, monkeypatch):
    cls, studs, cfg = cw
    cfg.worker_url, cfg.teacher_token = "https://w", "t"
    sent = []
    monkeypatch.setattr(pullmod, "http_put_json", lambda c, path, payload, method="PUT": sent.append((path, payload)) or {"ok": True, "sid": "c" * 16, "until": "x", "n": 1})
    r = CW.open_set(conn, cfg, cls["id"], "쎈 A")
    assert r["n"] == 4 and r["tagged"] == 4                                   # N from the tag file
    assert sent[0] == ("/classwork-open", {"class": class_key(cls["id"]), "label": "쎈 A", "n": 4})
    put_marks(conn, "c" * 16, studs[0]["id"], "oxmo")
    pullmod.push_tokmap(conn, cfg)
    blob = json.dumps(sent, ensure_ascii=False)
    assert "판별식" not in blob and "x^2" not in blob and "산술" not in blob
    conn.commit()
    assert generate.publish(conn, Config(extra=cfg.extra), tmp_path / "d", today=TODAY, now=NOW).ok
    site = "".join(f.read_text(encoding="utf-8", errors="ignore") for f in (tmp_path / "d").rglob("*") if f.is_file() and f.suffix in (".html", ".js", ".json"))
    assert "x^2" not in site and "판별식을 구하시오" not in site


def test_weekly_test_and_classwork_add_up_per_tag(conn, cw):
    cls, studs, cfg = cw
    R.add(conn, "Adaptive_Screeners_1S_Nov06_G10", "1학년", "고1A", 6, "2026-11-06 20:00", "2026-11-08 23:59")
    conn.execute("""INSERT INTO test_reports (student_id, round_id, incorrect, missed, hardest, reasons, ts, updated)
                    VALUES (?, 'Adaptive_Screeners_1S_Nov06_G10', '[2]', '[5]', '[]', '[]', 't', 't')""", (studs[0]["id"],))
    put_set(conn, cls, "a" * 16, "쎈 A", 4)
    put_marks(conn, "a" * 16, studs[0]["id"], "ooxo")
    sm = CW.skill_map(conn, cfg, studs[0]["id"])
    d, e = tag(sm, "이차방정식/판별식"), tag(sm, "명제/역과 대우")
    assert (d["o"], d["x"], d["m"]) == (2, 1, 0)                                # 2 from class + quiz 2 of the round (✕)
    assert (e["o"], e["x"], e["m"]) == (1, 0, 1)                                # quiz 5 missed in the round: –
    assert sm["totals"] == {"o": 3, "x": 1, "m": 0, "p": 0}                     # the totals are the in-class quizzes only


def test_essay_scores_partial_and_the_teacher_wins(conn, cw):
    cls, studs, cfg = cw
    rows = [{"student_id": studs[0]["id"], "score": 10}, {"student_id": studs[1]["id"], "score": 4, "note": "풀이 중간까지"},
            {"student_id": studs[2]["id"], "mark": "✕"}]
    assert CW.essay_save(conn, cls["id"], TODAY.isoformat(), "11/6 서술형", [], 10, rows) == 3
    got = {r["student_id"]: (r["mark"], r["score"]) for r in conn.execute("SELECT * FROM quiz_marks WHERE source = 'essay'")}
    assert got == {studs[0]["id"]: ("o", 10.0), studs[1]["id"]: ("p", 4.0), studs[2]["id"]: ("x", None)}
    assert CW.essay_save(conn, cls["id"], TODAY.isoformat(), "11/6 서술형", [], 10, rows) == 0            # unchanged: no event
    sm = CW.skill_map(conn, cfg, studs[1]["id"])
    t = tag(sm, "부등식/산술·기하평균")
    assert (t["p"], t["o"], t["x"]) == (1, 0, 0) and sm["totals"]["p"] == 1      # △ = 부분, its own state
    # chosen tags override the file; a quick-entry ○ / △ / ✕ without a max
    CW.essay_save(conn, cls["id"], TODAY.isoformat(), "보충 서술형", ["명제/역과 대우"], None, [{"student_id": studs[0]["id"], "mark": "△"}])
    assert tag(CW.skill_map(conn, cfg, studs[0]["id"]), "명제/역과 대우")["p"] == 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'essay_mark'").fetchone()[0] == 4
    view = CW.essay_view(conn, cls["id"], TODAY.isoformat(), "11/6 서술형")
    assert view["max"] == 10 and [s["mark"] for s in view["students"]] == ["o", "p", "x"]


def test_photo_import_threshold_priority_and_disagreement(conn, cw, tmp_path):
    cls, studs, cfg = cw
    put_set(conn, cls, "a" * 16, "쎈 A", 4)
    put_marks(conn, "a" * 16, studs[0]["id"], "oxmo")                         # the student's own marks
    csvp = tmp_path / "results.csv"
    csvp.write_text("date,class,student,set,quiz,read_answer,mark,confidence,source\n"
                    f"{TODAY},고1A,{studs[0]['slug']},쎈 A,1,③,○,0.97,photo\n"           # agrees, sure: stored
                    f"{TODAY},고1A,{studs[0]['slug']},쎈 A,2,②,○,0.95,photo\n"           # sure but unlike the self ✕: queued + flag
                    f"{TODAY},고1A,{studs[1]['slug']},쎈 A,3,⑤,✕,0.60,photo\n"           # unsure: queued
                    f"{TODAY},고1A,{studs[1]['slug']},쎈 A,4,①,○,0.92,photo\n"           # sure, no self mark: stored
                    f"{TODAY},고1A,nobody,쎈 A,1,①,○,0.99,photo\n", encoding="utf-8")
    n = CW.import_photo(conn, cfg, csvp)
    assert n == {"stored": 2, "queued": 2, "flagged": 1, "skipped": 1}
    q = CW.queue(conn, cfg)
    assert [(x["quiz"], x["flag"]) for x in q] == [(2, True), (3, False)]
    sm1 = CW.skill_map(conn, cfg, studs[1]["id"])
    assert sm1["totals"] == {"o": 1, "x": 0, "m": 0, "p": 0}                    # the queued ✕ counts nowhere until decided
    assert CW.skill_map(conn, cfg, studs[0]["id"])["totals"] == {"o": 2, "x": 1, "m": 1, "p": 0}   # self wins over the photo
    CW.decide(conn, q[0]["id"], "o")                                             # the teacher's choice wins over both
    assert CW.skill_map(conn, cfg, studs[0]["id"])["totals"] == {"o": 3, "x": 0, "m": 1, "p": 0}
    CW.decide(conn, q[1]["id"], "x")
    assert CW.queue(conn, cfg) == [] and CW.skill_map(conn, cfg, studs[1]["id"])["totals"]["x"] == 1


def test_queued_rows_never_reach_a_page(conn, cw, tmp_path):
    cls, studs, cfg = cw
    ops.edit_student(conn, studs[1]["slug"], share_classwork="yes")
    csvp = tmp_path / "r.csv"
    csvp.write_text(f"date,class,student,set,quiz,read_answer,mark,confidence,source\n{TODAY},고1A,{studs[1]['slug']},쎈 A,4,①,✕,0.5,photo\n",
                    encoding="utf-8")
    CW.import_photo(conn, cfg, csvp)
    assert generate.publish(conn, cfg, tmp_path / "d", today=TODAY, now=NOW).ok
    html = (tmp_path / "d" / "s" / studs[1]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert 'id="skmap"' not in html and "역과 대우" not in html


def test_pull_stores_sets_and_marks(conn, cw):
    cls, studs, cfg = cw
    body = {"sets": [{"sid": "d" * 16, "class": class_key(cls["id"]), "label": "쎈 A", "n": 4, "open_from": "2026-11-06T09:00:00.000Z",
                      "open_until": "x", "created": "c"}],
            "marks": [{"sid": "d" * 16, "token": studs[0]["student_token"], "marks": "oxmo", "updated": "2026-11-06T10:00:00.000Z"},
                      {"sid": "d" * 16, "token": studs[1]["student_token"], "marks": "oxpo", "updated": "u"},          # p is not a self mark
                      {"sid": "d" * 16, "token": studs[2]["student_token"], "marks": "ox", "updated": "u"}]}
    assert CW.apply_pull(conn, body) == (1, 1)
    assert CW.apply_pull(conn, body) == (0, 1)
    assert conn.execute("SELECT day FROM classwork_sets").fetchone()[0] == "2026-11-06"


def test_letter_has_the_second_box_and_the_cli(conn, cw, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from console import cards
    from console.cli import cli
    cls, studs, _ = cw
    html = cards.render(conn, "parents", cls["name"])
    assert "수업 중 문제 풀이 기록을 학부모 페이지에서 보기" in html and "학생 동의" in html and "보호자 동의" in html
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli, ["--db", db, "roster", "edit", studs[1]["slug"], "--share-classwork", "yes"])
    assert r.exit_code == 0 and conn.execute("SELECT share_classwork FROM students WHERE id = ?", (studs[1]["id"],)).fetchone()[0] == "yes"
    (tmp_path / "r.csv").write_text("date,class,student,set,quiz,read_answer,mark,confidence,source\n", encoding="utf-8")
    r = CliRunner().invoke(cli, ["--db", db, "classwork", "import", "r.csv"])
    assert r.exit_code == 0 and "stored 0" in r.output
    r = CliRunner().invoke(cli, ["--db", db, "classwork", "export", "고1A"])
    assert r.exit_code == 0 and "skills_고1A" in r.output
    assert strings()["parent_msg"]["letter_slip_cw"].startswith("수업 중 문제 풀이 기록")
