"""F16: practice_mode. korea (default) keeps the Korean design; manitoba is an opt-in practice layer."""
import json

from console import board as boardmod, ops, practice
from console.config import Config

from conftest import TODAY
from test_teacher import call, tserver  # noqa: F401


def test_default_is_korea_and_the_switch_is_logged(conn, klass):
    assert practice.mode(conn, Config()) == "korea"
    assert practice.mode(conn, Config(extra={"practice_mode": "manitoba"})) == "manitoba"      # daybook.json start value
    practice.set_mode(conn, "manitoba")
    assert practice.mode(conn, Config(extra={"practice_mode": "korea"})) == "manitoba"         # the switch wins
    practice.set_mode(conn, "korea")
    assert [json.loads(r[0])["mode"] for r in conn.execute("SELECT payload_json FROM events WHERE type = 'practice_mode'")] == ["manitoba", "korea"]


def test_board_names_three_in_korea_and_hides_them_in_manitoba(tserver, conn, klass):  # noqa: F811
    url, b, sent = tserver
    _, studs = klass
    call(url + "/api/attitude", {"student_id": studs[0]["id"], "points": 3})
    call(url + "/api/attitude", {"student_id": studs[1]["id"], "points": 2})
    html = call(url + "/")[1]
    three = html.split('id="b-three"')[1].split("</p>")[0]
    assert "</span>가람</span>" in three and "도담" not in three                     # praise by name (F22.4: + avatar); 2 points never
    assert call(url + "/api/state")[1]["three"] == "가람"
    assert "three" not in call(url + "/api/teacher/flags")[1]
    code, j = call(url + "/api/teacher/practice", {"mode": "manitoba"})
    assert j["mode"] == "manitoba"
    html = call(url + "/")[1]
    assert 'id="b-three"' not in html and "오늘 3점" not in html                               # nothing by name on the projector
    st = call(url + "/api/state")[1]
    assert st["three"] == "" and st["show_three"] is False
    assert call(url + "/api/teacher/flags")[1]["three"] == ["가람"]                            # 선생님 보기 only
    assert call(url + "/api/teacher/practice", {"mode": "canada"})[0] == 400


# ---------------------------------------------------------------- F16.3: drafts and the comment lint

from console import comments  # noqa: E402

W = {"c": 2, "d": 2, "k": 3, "week_sum": 6, "has_tests": True, "dir_eff": "up"}


def kinds(conn, text, sid, mode, cfg=None):
    return {w["kind"] for w in comments.lint(conn, cfg or Config(), text, sid, mode)}


def test_korea_drafts_are_warm_korean_and_praise_is_never_flagged(conn, klass):
    _, studs = klass
    d = comments.draft(W, 9, 2, "korea")
    assert d.startswith("이번 주 수업일 2일 모두 과제를 냈어요.") and "대단해요" in d and len(d.split(".")) <= 5
    assert "강점" not in d and "Strength" not in d
    assert kinds(conn, "이번 주 정말 최고였어요! 항상 열심히 해요.", studs[0]["id"], "korea") == set()   # normal Korean praise
    assert kinds(conn, "도담이보다 잘했어요.", studs[0]["id"], "korea") == {"other_student"}            # another student's name
    assert kinds(conn, "궁금하면 010-1234-5678", studs[0]["id"], "korea") == {"phone"}
    assert kinds(conn, "Adaptive_Screeners_1S_Nov06_G10 결과", studs[0]["id"], "korea") == {"raw_id"}
    assert kinds(conn, "가람이 반 1등이에요", studs[0]["id"], "korea") == {"rank"}                      # a rank next to a name
    assert kinds(conn, "1등급 목표로 해요", studs[0]["id"], "korea") == set()                         # 등급 is not a rank


def test_manitoba_drafts_strength_challenge_next_step_and_the_full_lint(conn, klass):
    _, studs = klass
    d = comments.draft({**W, "d": 1}, 2, 2, "manitoba")
    assert d.startswith("강점:") and "과제:" in d and "다음 단계:" in d
    en = comments.draft({**W, "d": 1}, 2, 2, "manitoba", "en")
    assert en.startswith("Strength:") and "Challenge:" in en and "Next step:" in en and 40 <= len(en.split()) <= 60
    for case in ({**W, "d": 2}, {**W, "d": 2, "dir_eff": "adjust"}, {**W, "k": 0, "d": 0}):
        assert 40 <= len(comments.draft(case, 0, 2, "manitoba", "en").split()) <= 60
    sid = studs[0]["id"]
    assert kinds(conn, "정말 최고였어요", sid, "manitoba") == {"absolute"}
    assert kinds(conn, "She always does her best", sid, "manitoba") == {"absolute"}
    assert kinds(conn, "반에서 가장 빨라요", sid, "manitoba") == {"comparison"}
    assert kinds(conn, "faster than others", sid, "manitoba") == {"comparison"}
    assert kinds(conn, "이해한 것 같아요", sid, "manitoba") == {"hedge"}
    assert kinds(conn, "It appears to help, however", sid, "manitoba") == {"hedge"}
    assert kinds(conn, "확보 효율이 올랐어요", sid, "manitoba") == {"jargon"}
    assert kinds(conn, "도형 단원 좋아요", sid, "manitoba", Config(extra={"comment_jargon": ["도형"]})) == {"jargon"}
    assert kinds(conn, "1위 했어요", sid, "manitoba") == {"rank"}
    assert kinds(conn, "도담이 010-1234-5678", sid, "manitoba") >= {"other_student", "phone"}


def test_warnings_never_block_a_save(tserver, conn, klass):  # noqa: F811
    url, b, sent = tserver
    _, studs = klass
    call(url + "/api/teacher/practice", {"mode": "manitoba"})
    code, r = call(url + "/api/teacher/note", {"slug": studs[0]["slug"], "week": "2026-W45", "text": "항상 최고예요"})
    assert code == 200 and r["ok"] and {w["kind"] for w in r["warnings"]} == {"absolute"}
    code, j = call(url + "/api/teacher/notes")
    assert j["students"][1]["suggestion"].startswith("강점:")
    assert call(url + "/api/teacher/comment-lint", {"slug": studs[0]["slug"], "text": "반에서 1등"})[1]["warnings"]
