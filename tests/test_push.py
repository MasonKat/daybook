"""F15.3: opt-in Web Push from the console (keys, send, count, after 발행) and the pages' opt-in box."""
import json
import re
from datetime import datetime, time

import pytest

from console import demo, generate, importers, push as P, vapid
from console.config import ROOT, Config
from console.db import KST, log_event

from conftest import TODAY

NOW = datetime.combine(TODAY, time(21), KST)
RFC_PUB = "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"


def pcfg(tmp_path, **extra):
    return Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t",
                  path=str(tmp_path / "daybook.json"), extra={"vapid_public": RFC_PUB, **extra})


class FakeWorker:
    def __init__(self, replies=None):
        self.calls, self.replies = [], list(replies or [])

    def __call__(self, payload):
        self.calls.append(payload)
        return self.replies.pop(0) if self.replies else {"ok": True, "sent": 2, "removed": 0, "failed": 0, "capped": 0, "more": False}


def test_vapid_keys_are_p256_and_match_the_rfc_example(tmp_path):
    assert vapid.public_from_private("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw") == RFC_PUB
    pub, priv = vapid.generate()
    raw = vapid.unb64u(pub)
    assert len(raw) == 65 and raw[0] == 4 and len(vapid.unb64u(priv)) == 32
    assert vapid.on_curve((int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big")))
    cfg = Config(path=str(tmp_path / "daybook.json"))
    (tmp_path / "daybook.json").write_text('{"worker_url": "https://w"}', encoding="utf-8")
    pub, path = P.write_keys(cfg, "mailto:t@example.com")
    data = json.loads((tmp_path / "daybook.json").read_text(encoding="utf-8"))
    assert data["worker_url"] == "https://w" and data["vapid_public"] == pub and vapid.public_from_private(data["vapid_private"]) == pub
    with pytest.raises(P.PushError):
        P.write_keys(cfg, "mailto:t@example.com")                      # never silently ends every subscription
    with pytest.raises(P.PushError):
        P.write_keys(cfg, "teacher", force=True)


def test_send_batches_logs_and_keeps_texts_generic(conn, klass, tmp_path):
    cfg = pcfg(tmp_path)
    fw = FakeWorker([{"ok": True, "sent": 10, "removed": 1, "failed": 0, "capped": 3, "more": True},
                     {"ok": True, "sent": 4, "removed": 0, "failed": 1, "capped": 3, "more": False}])
    r = P.send(conn, cfg, "video", ["고1A"], None, 2, fw)
    assert (r["sent"], r["removed"], r["failed"], r["capped"]) == (14, 1, 1, 3) and len(fw.calls) == 2
    assert fw.calls[0]["body"] == "새 영상 2개가 올라왔어요" and fw.calls[0]["title"] == "AMP 학습 기록"
    assert len(fw.calls[0]["classes"]) == 1 and "고1A" not in json.dumps(fw.calls[0], ensure_ascii=False)   # class keys only
    ev = json.loads(conn.execute("SELECT payload_json FROM events WHERE type = 'push_send'").fetchone()[0])
    assert ev["sent"] == 14 and ev["kind"] == "video"
    P.send(conn, cfg, "report", None, None, 0, FakeWorker([{"ok": True, "quiet": True, "sent": 0, "more": False}]))
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'push_quiet'").fetchone()[0] == 1   # a skip is logged
    with pytest.raises(P.PushError):
        P.send(conn, cfg, "report", None, "가람아 010-1234-5678로 연락해", 0, FakeWorker())        # a phone number
    with pytest.raises(P.PushError):
        P.send(conn, cfg, "report", None, "한가람 학생 기록이 올라왔어요", 0, FakeWorker())        # a full name
    with pytest.raises(P.PushError):
        P.send(conn, cfg, "spam", None, None, 0, FakeWorker())
    with pytest.raises(P.PushError):
        P.send(conn, Config(worker_url="https://w", teacher_token="t"), "report", None, None, 0, FakeWorker())   # no keys
    with pytest.raises(P.PushError):
        P.send(conn, cfg, "report", None, None, 0, FakeWorker([{"ok": False, "error": "no_vapid"}]))


def test_after_publish_picks_one_student_kind_then_the_parent_report(conn, klass, tmp_path):
    vp = tmp_path / "videos.json"
    vp.write_text(json.dumps([{"title": "새 쇼츠", "url": "https://e.x/1", "added": "2026-11-05"}], ensure_ascii=False), encoding="utf-8")
    cfg = pcfg(tmp_path)
    cfg.videos_path = str(vp)
    log_event(conn, "publish", {}, "2026-10-30T12:00:00.000Z")
    log_event(conn, "publish", {}, "2026-11-06T12:00:00.000Z")
    fw = FakeWorker()
    lines = P.after_publish(conn, cfg, fw)
    assert [c["kind"] for c in fw.calls] == ["video", "report"] and fw.calls[0]["body"] == "새 영상 1개가 올라왔어요"
    assert all(line.startswith("push (") for line in lines)
    vp.write_text("[]", encoding="utf-8")
    from console import ops
    ops.add_note(conn, klass[1][0]["id"], "2026-W45", "이번 주 잘했어요")
    fw = FakeWorker()
    P.after_publish(conn, cfg, fw)
    assert [c["kind"] for c in fw.calls] == ["message", "report"]
    conn.execute("DELETE FROM notes")
    fw = FakeWorker()
    P.after_publish(conn, cfg, fw)
    assert [c["kind"] for c in fw.calls] == ["report"]
    assert P.after_publish(conn, pcfg(tmp_path, push_after_publish=False), FakeWorker()) == []
    assert P.after_publish(conn, Config(), FakeWorker()) == []


def test_count_names_classes_but_never_people(conn, klass, tmp_path):
    from console.ops import class_key
    cls, _ = klass
    fetch = lambda path: json.dumps({"ok": True, "students": 3, "parents": 1, "vapid": True,
                                     "by_class": {class_key(cls["id"]): {"s": 3, "p": 1}}}).encode()
    assert P.count(conn, pcfg(tmp_path), fetch) == {"students": 3, "parents": 1, "vapid": True, "by_class": {"고1A": {"s": 3, "p": 1}}}


def test_pages_get_the_opt_in_box_only_with_a_key_never_in_taste(conn, tmp_path):
    demo.seed(conn, today=TODAY)
    importers.import_screeners(conn, ROOT / "demo" / "screeners")
    cfg = pcfg(tmp_path, videos_path=str(ROOT / "videos.json"))
    res = generate.publish(conn, cfg, tmp_path / "d", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings]
    s = next((tmp_path / "d" / "s").iterdir()) / "index.html"
    p = next((tmp_path / "d" / "p").iterdir()) / "index.html"
    for f, role in ((s, "s"), (p, "p")):
        html = f.read_text(encoding="utf-8")
        assert f'id="push" data-role="{role}" data-vapid="{RFC_PUB}"' in html and "알림 받기" in html
    sw = (tmp_path / "d" / "sw.js").read_text(encoding="utf-8")
    assert re.search(r"daybook-shell-[0-9a-f]{10}", sw) and "addEventListener('push'" in sw   # cache name follows the files
    taste = generate.publish(conn, cfg, tmp_path / "t", today=TODAY, now=NOW, taste=True)
    assert taste.ok and 'id="push"' not in (tmp_path / "t" / "s" / "kong" / "index.html").read_text(encoding="utf-8")
    off = generate.publish(conn, Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t"),
                           tmp_path / "o", today=TODAY, now=NOW)
    assert off.ok and 'id="push"' not in (next((tmp_path / "o" / "s").iterdir()) / "index.html").read_text(encoding="utf-8")


def test_the_worker_learns_parent_links_for_push_only(conn, klass, monkeypatch):
    from console import pull as pullmod
    sent = {}
    monkeypatch.setattr(pullmod, "http_put_json", lambda cfg, path, payload: sent.update(payload) or {"n": 3})
    pullmod.push_tokmap(conn, Config(worker_url="https://w", teacher_token="t"))
    _, studs = klass
    assert set(sent["parents"]) == {s["parent_token"] for s in studs} and set(sent["tokmap"]) == {s["student_token"] for s in studs}
