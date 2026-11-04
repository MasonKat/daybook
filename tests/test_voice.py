"""F19.1 voice notes: a CLOVA Note text export -> preview (nothing stored) -> 저장 -> pages show only kept positive lines."""
import json
import shutil
import subprocess

import pytest

from console import generate, ops, practice, voice as VC, wordfilter as WF
from console.config import Config, ROOT
from console.importers import add_student

from conftest import TODAY

NODE = shutil.which("node")

# synthetic, in CLOVA's export shape: a title, a date line, speaker labels, timestamps, line breaks inside utterances
CLOVA = """고1A 수업 메모
2026.11.06 금 오후 6:02 ・ 58분 3초

참석자 1 00:03:12
칭찬 가랑이 2점 분수 나눗셈을
칠판에서 끝까지 설명했어요.
참석자 1 00:10:45 칭찬 칠 번 한 점 질문이 좋았어
참석자 1 00:15:02
칭찬 하놀 1점 숙제를 꼼꼼하게 해 왔어
개념 이차함수 꼭짓점은 완전제곱식으로 다시
장면 다 같이 분수 대결
칭찬 가람 3점 계산 실수 없이 다 맞힘 취소
참석자 1 00:40:19
칭찬 도담 1점 떠들어서 혼났지만 끝까지 풀었어
"""


@pytest.fixture
def calls(conn, klass):
    """가람, 도담, 하늘 (the fixture class) + 하눌 (one jamo from 하늘) + 민 (seat 7번)."""
    cls, studs = klass
    hanul = add_student(conn, cls["id"], "박하눌", ext_id="S904")
    min_ = add_student(conn, cls["id"], "최민", display_name="민", ext_id="S905")
    slug = lambda sid: conn.execute("SELECT slug FROM students WHERE id = ?", (sid,)).fetchone()[0]   # noqa: E731
    for sid, c in ((studs[0]["id"], "가람"), (studs[1]["id"], "도담"), (studs[2]["id"], "하늘"), (hanul["id"], "하눌"), (min_["id"], "7번")):
        ops.edit_student(conn, slug(sid), callsign=c)
    conn.execute("UPDATE students SET consent = 'yes'")
    conn.commit()
    get = lambda sid: conn.execute("SELECT * FROM students WHERE id = ?", (sid,)).fetchone()   # noqa: E731
    return cls, [get(s["id"]) for s in studs], get(hanul["id"]), get(min_["id"])


def test_numbers_and_spoken_forms():
    assert [VC.number(w) for w in ("7", "칠", "십이", "한", "열두", "다섯", "일", "가람")] == [7, 7, 12, 1, 12, 5, 1, None]
    assert VC.spoken("칠 번") == VC.spoken("7번") == "7번" and VC.spoken("일곱번") == "7번"
    assert VC.distance("가랑", "가람") == 1 and VC.distance("7번", "1번") == 99          # seat numbers never fuzzy


def test_clean_strips_labels_and_timestamps_and_splits_at_keywords():
    t = VC.clean(CLOVA)
    assert "참석자" not in t and "00:03:12" not in t and "\n" not in t
    us = VC.split(CLOVA)
    assert [u["kind"] for u in us] == ["praise"] * 3 + ["concept", "scene", "praise", "praise"]
    assert [u["dropped"] for u in us] == [False] * 5 + [True, False]                   # 취소 drops the line before it
    assert VC.split("칭찬 가람 개념을 잘 설명함")[0]["body"] == "가람 개념을 잘 설명함"     # 개념을 is not a keyword


def test_preview_matches_fuzzy_ambiguous_and_stores_nothing(conn, calls):
    cls, studs, hanul, min_ = calls
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("shoutouts", "concept_notes", "moment_drafts", "events")}
    pv = VC.preview(conn, cls["id"], CLOVA)
    rows = pv["rows"]
    p = [r for r in rows if r["kind"] == "praise"]
    assert (p[0]["match"], p[0]["student_id"], p[0]["points"], p[0]["text"]) == ("fuzzy", studs[0]["id"], 2, "분수 나눗셈을 칠판에서 끝까지 설명했어요")
    assert (p[1]["match"], p[1]["student_id"], p[1]["points"]) == ("exact", min_["id"], 1)                   # 칠 번 = 7번, 한 점 = 1
    assert p[2]["match"] == "ambiguous" and p[2]["student_id"] is None and not p[2]["keep"]                  # 하놀: 하늘? 하눌?
    assert {c["id"] for c in p[2]["candidates"]} == {studs[2]["id"], hanul["id"]}
    assert p[3]["dropped"] and not p[3]["keep"]                                                               # 취소
    assert p[4]["private"] == "negative" and p[4]["student_id"] == studs[1]["id"]                            # teacher-only
    assert pv["who"] == 1
    after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    assert after == before                                                                                    # preview stores nothing


def test_ambiguous_offers_candidates_never_a_guess(conn, calls):
    cls, studs, hanul, min_ = calls
    roster = VC.class_roster(conn, cls["id"])
    kind, cands = VC.match("하놀", roster)                     # one jamo from 하늘 and from 하눌
    assert kind == "ambiguous" and {c["callsign"] for c in cands} == {"하늘", "하눌"}
    assert VC.match("하늘이", roster)[0] == "exact"             # 이 / 가 / 한테 … are read through
    kind, cands = VC.match("누구지", roster)
    assert kind == "unknown" and 2 <= len(cands) <= 3


def test_save_then_pages_show_only_kept_positive_lines(conn, calls, tmp_path):
    cls, studs, hanul, min_ = calls
    rows = VC.preview(conn, cls["id"], CLOVA)["rows"]
    amb = [r for r in rows if r.get("match") == "ambiguous"][0]
    amb.update(student_id=hanul["id"], keep=True)                    # the teacher picks 하눌 from the chips
    n = VC.save(conn, Config(), cls["id"], TODAY, rows)
    assert (n["praise"], n["concept"], n["scene"], n["private"]) == (4, 1, 1, 1)
    saved = {r["text"]: r["private"] for r in conn.execute("SELECT text, private FROM shoutouts")}
    assert "계산 실수 없이 다 맞힘" not in saved and saved["떠들어서 혼났지만 끝까지 풀었어"] == 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'shoutout'").fetchone()[0] == 4
    assert conn.execute("SELECT type FROM events ORDER BY id DESC LIMIT 1").fetchone()[0] == "voice_import"
    assert conn.execute("SELECT COUNT(*) FROM attitude").fetchone()[0] == 0                # 🎤 is not 태도 by default
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY).ok
    page = lambda s: (tmp_path / "d" / "s" / s["student_token"] / "index.html").read_text(encoding="utf-8")   # noqa: E731
    assert "선생님 한마디" in page(studs[0]) and "분수 나눗셈을 칠판에서 끝까지 설명했어요" in page(studs[0])
    assert "숙제를 꼼꼼하게 해 왔어" in page(hanul)
    assert "떠들어서" not in page(studs[1]) and "선생님 한마디" not in page(studs[1])        # negative: teacher-only
    site = "".join(f.read_text(encoding="utf-8") for f in (tmp_path / "d").rglob("*.html"))
    assert "이차함수 꼭짓점" not in site and "분수 대결" not in site                          # 개념 / 장면 drafts stay local
    assert "가랑" not in site and "7번" not in site                                          # call signs never on a page
    practice.set_mode(conn, "manitoba")
    assert generate.publish(conn, Config(), tmp_path / "m", today=TODAY).ok
    assert "선생님 한마디" not in (tmp_path / "m" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")


def test_board_list_and_attitude_option(conn, calls):
    cls, studs, hanul, min_ = calls
    rows = VC.preview(conn, cls["id"], CLOVA)["rows"]
    VC.save(conn, Config(extra={"voice_points_to_attitude": True}), cls["id"], TODAY, rows)
    board = VC.today_board(conn, cls["id"], TODAY)
    assert [b["display_name"] for b in board] == [studs[0]["display_name"], "민"]            # 도담's line is teacher-only
    att = {r[0]: r[1] for r in conn.execute("SELECT student_id, points FROM attitude")}
    assert att[studs[0]["id"]] == 2 and att[min_["id"]] == 1


def test_filtered_and_pii_lines_stay_teacher_only(conn, calls):
    cls, studs, hanul, min_ = calls
    rows = [{"kind": "praise", "keep": True, "student_id": studs[0]["id"], "points": 1, "text": "ㅅㅂ 어려운데 끝까지 함"},
            {"kind": "praise", "keep": True, "student_id": studs[0]["id"], "points": 1, "text": f"{studs[1]['full_name']}랑 같이 풀었어"},
            {"kind": "praise", "keep": True, "student_id": 12345, "points": 1, "text": "다른 반 학생"}]
    n = VC.save(conn, Config(), cls["id"], TODAY, rows)
    assert n["private"] == 2 and n["skipped"] == 1                     # the browser is not trusted: re-checked here
    assert {r[0] for r in conn.execute("SELECT private FROM shoutouts")} == {1}


def test_callsign_rules(conn, calls):
    cls, studs, hanul, min_ = calls
    with pytest.raises(ops.OpError, match="already used"):
        ops.edit_student(conn, studs[1]["slug"], callsign="칠번")      # = 7번 when spoken
    with pytest.raises(ops.OpError):
        ops.edit_student(conn, studs[1]["slug"], callsign=studs[1]["full_name"])
    with pytest.raises(ops.OpError):
        ops.edit_student(conn, studs[1]["slug"], callsign=studs[1]["student_token"])
    ops.edit_student(conn, studs[1]["slug"], callsign="")
    assert conn.execute("SELECT callsign FROM students WHERE id = ?", (studs[1]["id"],)).fetchone()[0] is None


def test_cli_preview_then_save(conn, calls, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from console.cli import cli
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    (tmp_path / "t.txt").write_text(CLOVA, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli, ["--db", db, "voice", "import", "t.txt", "--class", "고1A"])
    assert r.exit_code == 0 and "누구?" in r.output and "(취소됨)" in r.output and "nothing stored" in r.output
    assert conn.execute("SELECT COUNT(*) FROM shoutouts").fetchone()[0] == 0
    r = CliRunner().invoke(cli, ["--db", db, "voice", "import", "t.txt", "--class", "고1A", "--save", "--date", "2026-11-06"])
    assert r.exit_code == 0 and "칭찬 3 (선생님만 1)" in r.output
    cp = CLOVA.replace("・", "·")
    (tmp_path / "w.txt").write_bytes(cp.encode("cp949"))                    # a Windows editor's CP949 file
    assert VC.read_input(str(tmp_path / "w.txt")) == cp


@pytest.mark.skipif(not NODE, reason="node is needed for the parity check")
def test_python_filter_equals_the_js_filter():
    samples = ["시발 어려움", "ㅅㅂ", "ㅅ 1 ㅂ", "씨 . 발", "시1발", "tlqkf", "f u c k", "010-1234-5678", "www.naver.com", "시이발", "조오온나",
               "느금마", "장애인같이 풀었다", "흑형", "한남", "오늘 시발점 문제", "이번 시 발표 최고", "한 남자가 문제를 풀었다", "피카소급",
               "개웃김ㅋㅋ", "병아리", "존경합니다", "장애인 차별 반대", "살이 찐다", "기이한 문제", "ㅂㅅ같은", "qudtls", "ㄱㅅㄲ"]
    words = (ROOT / "filters" / "review_words.txt").read_text(encoding="utf-8")
    script = f"""
      import({json.dumps((ROOT / 'worker' / 'src' / 'review_filter.js').as_uri())}).then((W) => {{
        const list = W.parseList({json.dumps(words, ensure_ascii=False)});
        console.log(JSON.stringify({json.dumps(samples, ensure_ascii=False)}.map((t) => W.check(t, list).ok)));
      }});"""
    js = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    lst = WF.parse_list(words)
    assert [WF.check(t, lst)[0] for t in samples] == js
