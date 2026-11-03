"""F7: AMP Live on the laptop: only kind + answer leave it; the class bonus at >= 80 %; results become effort rows."""
import json
import threading
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer

import pytest

from console import board as boardmod, live as L, models as M, ops
from console.config import ROOT, Config
from console.pull import PullReport, apply_live

from conftest import TODAY


def test_question_sets_and_what_leaves_the_laptop(tmp_path):
    cfg = Config()
    sets = L.list_sets(cfg)
    assert any(s["name"] == "demo-round.json" and s["n"] == 5 for s in sets)
    data = L.find_set(cfg, "demo-round.json")
    payload = L.worker_payload(7, data["questions"])
    blob = json.dumps(payload, ensure_ascii=False)
    assert all(q["q"] not in blob for q in data["questions"]) and "choices" not in blob and "인수분해" not in blob
    assert payload["questions"][0] == {"kind": "choice", "answer": "3"} and payload["questions"][1] == {"kind": "num", "answer": "6"}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"questions": [{"q": "x", "answer": "9"}]}), encoding="utf-8")
    with pytest.raises(L.LiveError):
        L.load_set(bad)


def test_bonus_once_at_80_percent(conn, klass):
    cls, _ = klass
    cfg = Config(extra={"live_bonus": 5})
    sid = ops.ensure_session(conn, cls["id"], TODAY)
    before = M.class_total(conn, cls["id"])
    st = {"revealed": True, "q": 1, "correct": 2, "size": 3}               # 2/3 < 80 %
    assert L.maybe_bonus(conn, cfg, cls["id"], sid, TODAY, "1234", st) == 0
    st["correct"] = 3                                                     # 3/3 >= 80 %
    assert L.maybe_bonus(conn, cfg, cls["id"], sid, TODAY, "1234", st) == 5
    assert L.maybe_bonus(conn, cfg, cls["id"], sid, TODAY, "1234", st) == 0      # once per question
    assert L.maybe_bonus(conn, cfg, cls["id"], sid, TODAY, "1234", {**st, "revealed": False, "q": 2}) == 0
    assert M.class_total(conn, cls["id"]) == before + 5
    assert L.needed(20) == 16 and L.needed(1) == 1


def test_results_become_effort_rows(conn, klass):
    _, studs = klass
    rep = PullReport()
    items = [{"token": studs[0]["student_token"], "room": "4821", "date": "2026-11-06", "answered": 9, "correct": 7, "ts": "2026-11-06T10:00:00Z"},
             {"token": "unknown", "room": "4821", "date": "2026-11-06", "answered": 1, "correct": 1, "ts": "x"}]
    apply_live(conn, items, rep)
    apply_live(conn, items, rep)                                           # idempotent
    rows = conn.execute("SELECT * FROM effort").fetchall()
    assert len(rows) == 1 and rows[0]["source"] == "live" and rows[0]["set_id"] == "live:2026-11-06:4821"
    assert (rows[0]["attempted"], rows[0]["correct"]) == (9, 7) and rep.live == 1


def test_moment_card_is_a_jpeg_with_numbers_only():
    jpg = L.moment_card("데모", 3, 90)
    assert jpg[:2] == b"\xff\xd8" and len(jpg) < 200 * 1024


@pytest.fixture
def live_server(conn, klass, tmp_path, monkeypatch):
    """The board with a fake Worker room (kept in Python) behind http_post_json / http_fetch."""
    import console.pull as P
    room = {"q": 0, "accepting": False, "revealed": False, "answered": 0, "correct": 0, "size": 3, "open": True}
    sent = []

    def post(cfg, path, payload):
        sent.append((path, payload))
        if path == "/live/open":
            return {"ok": True, "room": "4821", "size": 3}
        if path == "/live/next":
            room.update(q=room["q"] + 1, accepting=True, revealed=False, answered=3, correct=3)
        if path == "/live/reveal":
            room.update(accepting=False, revealed=True)
        if path == "/live/close":
            room.update(open=False)
            return {"ok": True}
        return {"ok": True, **room, "correct": room["correct"] if room["revealed"] else None}

    monkeypatch.setattr(P, "http_post_json", post)
    monkeypatch.setattr(P, "http_put_json", lambda cfg, path, payload, method="PUT": sent.append((path, payload)) or {"ok": True})
    monkeypatch.setattr(boardmod, "http_fetch", lambda cfg: (lambda path: json.dumps({"ok": True, **room, "correct": room["correct"] if room["revealed"] else None}).encode()))
    monkeypatch.chdir(tmp_path)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    b = boardmod.Board(db, Config(worker_url="https://w.example", teacher_token="t", moments_dir=str(tmp_path / "m")), "고1A", TODAY, poll=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), boardmod.make_handler(b))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", sent
    httpd.shutdown()


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_board_live_flow(live_server, conn, klass):
    url, sent = live_server
    cls, _ = klass
    html = urllib.request.urlopen(url + "/live").read().decode("utf-8")
    assert "방 코드" in html and "/static/live.js" in html
    sets = json.loads(urllib.request.urlopen(url + "/api/live/sets").read())
    assert any(s["name"] == "demo-round.json" for s in sets["sets"])
    st = _post(url + "/api/live/open", {"set": "demo-round.json"})
    assert st["room"] == "4821" and st["open"] and st["q"] == 0
    opened = [p for path, p in sent if path == "/live/open"][0]
    assert "x + 5" not in json.dumps(opened, ensure_ascii=False)          # the text stayed on the laptop
    st = _post(url + "/api/live/next", {})
    assert st["question"]["text"].startswith("x + 5") and st["question"]["answer"] is None   # no key before reveal
    before = M.class_total(conn, cls["id"])
    st = _post(url + "/api/live/reveal", {})
    assert st["question"]["answer"] == "3" and st["bonus"] == 5 and st["class_total"] == before + 5
    assert _post(url + "/api/live/reveal", {})["bonus"] == 0              # once
    st = _post(url + "/api/live/moment", {})
    assert any(path == "/moment-put" for path, _ in sent)
    assert conn.execute("SELECT text FROM moments").fetchone()[0].startswith("AMP Live · 1번")
    assert _post(url + "/api/live/close", {})["open"] is False
    assert "학생" not in json.dumps(st, ensure_ascii=False) and "가람" not in json.dumps(st, ensure_ascii=False)
