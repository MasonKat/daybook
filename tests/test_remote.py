"""F19.4 the teacher remote (encrypted class lists, pulled shout-outs, undo, the class target, the board within one poll)
and the F19 addendum (natural phrasing, the last N점 wins, 확인 필요 for negative words, casual slang passes)."""
import json
from datetime import date

import pytest

from console import board as boardmod, generate, models as M, ops, pull as pullmod, remote as RM, voice as VC, wordfilter as WF
from console.config import Config
from console.ops import class_key

from conftest import TODAY


@pytest.fixture
def named(conn, klass):
    cls, studs = klass
    for s, c in zip(studs, ("가람", "7번", "아이스")):
        ops.edit_student(conn, s["slug"], callsign=c)
    conn.commit()
    return cls, [conn.execute("SELECT * FROM students WHERE id = ?", (s["id"],)).fetchone() for s in studs]


def item(tok, i, text="풀이 깔끔", points=2, deleted=0, cls=None, day="2026-11-06"):
    return {"id": f"{i:016x}", "token": tok, "class": cls or "", "day": day, "points": points, "text": text,
            "ts": "2026-11-06T09:00:00.000Z", "updated": "2026-11-06T09:00:00.000Z", "deleted": deleted}


# ---------------------------------------------------------------- the encrypted class lists

def test_encrypt_round_trip_and_tamper():
    k = bytes(range(32))
    blob = RM.encrypt(k, "가람 7번 {}".encode() * 40)
    assert RM.decrypt(k, blob) == "가람 7번 {}".encode() * 40
    raw = bytearray(RM._unb64u(blob)); raw[20] ^= 1
    with pytest.raises(RM.RemoteError):
        RM.decrypt(k, RM._b64u(bytes(raw)))
    with pytest.raises(RM.RemoteError):
        RM.decrypt(bytes(32), blob)


def test_roster_labels_seats_and_no_names_in_the_clear(conn, named, tmp_path, monkeypatch):
    cls, studs = named
    r = RM.roster(conn, Config())
    c = r["classes"][0]
    assert c["key"] == class_key(cls["id"]) and c["days"] == [1, 4] and r["chips"] == RM.DEFAULT_CHIPS
    assert [(s["label"], s["seat"]) for s in c["students"]] == [("가람", None), ("7번", 7), ("아이스", None)]
    assert all(s["t"] == st["student_token"] for s, st in zip(c["students"], studs))
    ops.edit_student(conn, studs[2]["slug"], callsign="")
    assert RM.roster(conn, Config())["classes"][0]["students"][2]["label"] == studs[2]["display_name"]   # no call sign: the name
    cfg = Config(worker_url="https://w", teacher_token="t", path=str(tmp_path / "daybook.json"))
    assert RM.push(conn, cfg) == ""                                                  # not set up: nothing sent
    url = RM.link(cfg)
    assert url.startswith("https://w/t/remote/#t=t&k=") and json.loads((tmp_path / "daybook.json").read_text(encoding="utf-8"))["remote_key"]
    sent = {}
    monkeypatch.setattr(pullmod, "http_put_json", lambda cfg, path, payload: sent.update({path: payload}) or {"ok": True})
    assert "encrypted" in RM.push(conn, cfg)
    blob = sent["/remote-roster"]["blob"]
    assert "가람" not in blob and studs[0]["full_name"] not in blob
    assert json.loads(RM.decrypt(RM.key(cfg), blob))["classes"][0]["students"][0]["label"] == "가람"
    k1 = RM.key(cfg); RM.ensure_key(cfg, rotate=True)
    assert RM.key(cfg) != k1


# ---------------------------------------------------------------- pulled shout-outs

def test_pull_remote_rows_undo_class_target_and_holds(conn, named):
    cls, studs = named
    t = [s["student_token"] for s in studs]
    before = M.reward_for(conn, cls, TODAY).total
    items = [item(t[0], 1), item(t[1], 2, "지각 안 했네 굿", 1), item(t[2], 3, "ㅅㅂ 그래도 끝까지", 1),
             item("*", 4, "다 같이 맞힘", 3, cls=class_key(cls["id"])), item("x" * 26, 5)]
    assert VC.apply_remote(conn, Config(), items) == 4
    assert VC.apply_remote(conn, Config(), items) == 0                                # replays never duplicate
    rows = {r["ref"]: (r["private"], r["hold"], r["source"]) for r in conn.execute("SELECT * FROM shoutouts")}
    assert rows == {"remote:0000000000000001": (0, 0, "remote"), "remote:0000000000000002": (1, 1, "remote"),
                    "remote:0000000000000003": (1, 0, "remote")}                    # 확인 필요; the F18 filter: teacher-only
    assert M.reward_for(conn, cls, TODAY).total == before + 3                         # 반 전체: the class goal
    assert conn.execute("SELECT SUM(points) FROM shoutouts").fetchone()[0] == 4      # and nothing for one student
    board = VC.today_board(conn, cls["id"], TODAY)
    assert [b["display_name"] for b in board] == [studs[0]["display_name"], "반 전체"]
    sid = conn.execute("SELECT id FROM shoutouts WHERE ref = 'remote:0000000000000002'").fetchone()[0]
    VC.decide(conn, sid, True)                                                        # the teacher: 공개
    assert len(VC.today_board(conn, cls["id"], TODAY)) == 3
    VC.apply_remote(conn, Config(), [item(t[0], 1, deleted=1), item("*", 4, deleted=1, cls=class_key(cls["id"]))])   # 취소
    assert not conn.execute("SELECT 1 FROM shoutouts WHERE ref = 'remote:0000000000000001'").fetchone()
    assert M.reward_for(conn, cls, TODAY).total == before
    kinds = [r[0] for r in conn.execute("SELECT type FROM events WHERE type LIKE 'shout%' OR type = 'class_shout' ORDER BY id")]
    assert kinds.count("shoutout") == 3 and "shoutout_show" in kinds and kinds.count("shoutout_undo") == 2


def test_tap_save_appears_on_the_board_within_one_poll(conn, named, tmp_path, monkeypatch):
    cls, studs = named
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.chdir(tmp_path)
    b = boardmod.Board(db_path, Config(), "고1A", TODAY, poll=False)
    with b.conn() as c2:
        assert b.state_json(c2)["shouts"] == []
    pages = {"/submissions": {"items": [], "now": "2026-11-06T12:00:00.000Z"}, "/shouts-pull": {"items": [item(studs[1]["student_token"], 9, "질문 좋았어", 1)]}}
    def fetch(path):
        if path.split("?")[0] in pages:
            return json.dumps(pages[path.split("?")[0]]).encode()
        raise pullmod.PullError("404")
    with b.conn() as c2:
        pullmod.pull(c2, fetch, cfg=Config())                                         # what one board poll does
        assert b.state_json(c2)["shouts"] == [{"display_name": studs[1]["display_name"], "text": "질문 좋았어", "points": 1}]


def test_remote_lines_reach_the_students_page_in_korea_mode_only(conn, named, tmp_path):
    from console import practice
    cls, studs = named
    VC.apply_remote(conn, Config(), [item(studs[0]["student_token"], 1, "발표 최고")])
    conn.commit()
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY).ok
    assert "발표 최고" in (tmp_path / "d" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    practice.set_mode(conn, "manitoba")
    assert generate.publish(conn, Config(), tmp_path / "m", today=TODAY).ok
    assert "발표 최고" not in (tmp_path / "m" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the addendum: natural phrasing and points

ROSTER = [{"id": 1, "display_name": "가람", "callsign": "가람"}, {"id": 2, "display_name": "민", "callsign": "아이스"},
          {"id": 3, "display_name": "도담", "callsign": "도담"}]


def parse(text):
    out = []
    for u in VC.split(text, ROSTER):
        p = VC._praise(u["body"], ROSTER, u.get("at", 0), u.get("width", 0)) if u["natural"] else VC._praise(u["body"], ROSTER)
        out.append((p["target"], p["candidates"][0]["id"] if p["candidates"] and p["match"] in ("exact", "fuzzy") else None,
                    p["points"], p["text"]))
    return out


def test_the_teachers_own_phrasing():
    got = parse("나이스 가람! 1점 적립 - 지각 안했네\n좋아, 아이스 그걸 맞췄네 1점!\n도담, 잘했어용, 빠르게 해석했네!\n"
                "아까비, 전체가 맞추면 2점 줄려했는데, 일단 1점\n칭찬 가람 3점 추가 풀이 깔끔\n칭찬 도담 추가 2점 발표 최고\n"
                "칭찬 다 같이 2점 조용히 집중\n우리 반 3점 적립")
    assert got == [("student", 1, 1, "나이스 지각 안했네"), ("student", 2, 1, "좋아, 그걸 맞췄네"), ("student", 3, 1, "잘했어용, 빠르게 해석했네"),
                   ("class", None, 1, "아까비, 맞추면 2점 줄려했는데"), ("student", 1, 3, "풀이 깔끔"), ("student", 3, 2, "발표 최고"),
                   ("class", None, 2, "조용히 집중"), ("class", None, 3, "")]


def test_points_last_wins_and_spaced_forms():
    assert [n for _, _, n in VC.points_in("2점 줄려했는데, 일단 1점")] == [2, 1]
    assert VC._praise("가람 2점 줄려했는데, 일단 1점", ROSTER)["points"] == 1                       # the LAST N점
    assert [n for _, _, n in VC.points_in("이 점 잘했어")] == []                                    # "this point", not 2
    assert [n for _, _, n in VC.points_in("두 점 추가") + VC.points_in("한 점!")] == [2, 1]
    assert VC.points_in("꼭짓점 구하기 만점") == []


def test_negative_words_need_the_teachers_decision(conn, named):
    cls, studs = named
    pv = VC.preview(conn, cls["id"], "나이스 가람! 1점 적립 - 지각 안했네\n칭찬 7번 1점 오늘 지각했네\n칭찬 아이스 2점 끝까지 해냄")
    r = {x["student_id"]: x for x in pv["rows"]}
    assert (r[studs[0]["id"]]["private"], r[studs[0]["id"]]["publish"]) == ("negative", True)      # negated: leans 공개
    assert (r[studs[1]["id"]]["private"], r[studs[1]["id"]]["publish"]) == ("negative", False)     # leans 비공개
    assert (r[studs[2]["id"]]["private"], r[studs[2]["id"]]["publish"]) == ("", True)
    r[studs[1]["id"]]["publish"] = True                                                            # the teacher decides
    r[studs[0]["id"]]["publish"] = False
    VC.save(conn, Config(), cls["id"], TODAY, pv["rows"])
    got = {x["student_id"]: x["private"] for x in conn.execute("SELECT student_id, private FROM shoutouts")}
    assert got == {studs[0]["id"]: 1, studs[1]["id"]: 0, studs[2]["id"]: 0}
    from console.lint import text_scrubber
    ctx = generate.build_context(conn, Config(), TODAY, M.kst_now(), scrub=text_scrubber(conn))   # 공개 survives the publish
    me = {s["id"]: s for s in ctx["students"]}
    assert me[studs[1]["id"]]["shouts"] and not me[studs[0]["id"]]["shouts"]


def test_class_target_in_the_import_feeds_the_class_goal(conn, named):
    cls, _ = named
    before = M.reward_for(conn, cls, TODAY).total
    pv = VC.preview(conn, cls["id"], "칭찬 전체 2점 다 같이 맞힘")
    assert pv["rows"][0]["target"] == "class" and pv["rows"][0]["keep"] and pv["who"] == 0
    n = VC.save(conn, Config(), cls["id"], TODAY, pv["rows"])
    assert n["class"] == 1 and M.reward_for(conn, cls, TODAY).total == before + 2
    assert conn.execute("SELECT COUNT(*) FROM shoutouts").fetchone()[0] == 0


def test_casual_endings_and_slang_pass_the_filter():
    for t in ("잘했어용", "ㅇㅋ", "아까비", "ㅎㅎ", "나이스", "좋아, 그걸 맞췄네 1점!", "잘했어용, 빠르게 해석했네!", "오키", "ㅋㅋㅋ 굿"):
        assert WF.check(t, WF.default_list())[0], t


def test_teacher_tab_remote_setup_shows_a_qr(conn, named, tmp_path, monkeypatch):
    from console import teacher as T
    monkeypatch.setattr(pullmod, "http_put_json", lambda cfg, path, payload: {"ok": True})
    cfg = Config(worker_url="https://w", teacher_token="t", path=str(tmp_path / "daybook.json"))
    assert T.remote_view(cfg) == {"ok": True, "ready": True, "set_up": False}
    j = T.remote_setup(conn, cfg, {})
    assert j["link"].startswith("https://w/t/remote/#t=t&k=") and j["qr"].lstrip().startswith("<svg") and T.remote_view(cfg)["set_up"]
    with pytest.raises(T.TeacherError):
        T.remote_setup(conn, Config(), {})


def test_taste_remote_is_simulated_with_8_synthetic_students(tmp_path):
    from console import demo
    from console import db as dbmod
    c = dbmod.connect(tmp_path / "demo.sqlite"); dbmod.init(c)
    demo.seed(c, today=TODAY)
    assert generate.publish(c, Config(), tmp_path / "t", today=TODAY, taste=True).ok
    html = (tmp_path / "t" / "remote" / "index.html").read_text(encoding="utf-8")
    cfg = json.loads(html.split('id="cfg">')[1].split("</script>")[0])
    assert cfg["taste"] and [s["label"] for s in cfg["roster"]["classes"][0]["students"]] == generate.TASTE_REMOTE
    assert (tmp_path / "t" / "static" / "remote.js").read_text(encoding="utf-8").startswith("(function () {")
