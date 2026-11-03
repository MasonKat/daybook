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
    assert 'id="b-three">가람<' in html and "도담" not in html.split('id="b-three"')[1][:40]   # praise by name; 2 points never
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
