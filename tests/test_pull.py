"""`daybook pull` against a fake Worker response; then the parent page shows the ✓ and the time."""
import json
import re
from datetime import datetime

from console import generate, ops
from console.db import KST, last_event
from console.pull import apply_items, pull

from conftest import TODAY

NOW = datetime(2026, 11, 6, 22, 0, tzinfo=KST)


def item(tok, day="2026-11-06", ts="2026-11-06T08:15:00.000Z", key=None, n=1, set_=""):
    return {"token": tok, "day": day, "ts": ts, "updated": ts, "key": key or f"photo:{day}:{tok}", "n": n, "set": set_}


class FakeWorker:
    def __init__(self, items):
        self.items, self.calls = items, []

    def __call__(self, path):
        self.calls.append(path)
        return json.dumps({"now": "2026-11-06T13:00:00.000Z", "items": self.items}).encode()


def test_pull_inserts_and_parent_page_shows_check_and_time(conn, cfg, klass, tmp_path):
    _, studs = klass
    s = studs[0]
    fw = FakeWorker([item(s["student_token"]), item("unknowntokenunknowntokenun")])
    rep = pull(conn, fw)
    assert (rep.new, rep.updated, rep.unknown_tokens) == (1, 0, 1)
    row = conn.execute("SELECT * FROM submissions").fetchone()
    assert row["source"] == "phone" and row["day"] == "2026-11-06" and row["student_id"] == s["id"]
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='submission'").fetchone()[0] == 1
    assert last_event(conn, "pull")["next_since"] == "2026-11-06T12:55:00.000Z"
    assert generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW).ok
    html = (tmp_path / "dist" / "p" / s["parent_token"] / "index.html").read_text(encoding="utf-8")
    # 08:15 UTC = 17:15 KST on Friday
    assert re.search(r'<span class="dot">✓</span><span class="tm tnum">17:15</span>', html)


def test_second_pull_uses_since_and_is_idempotent(conn, klass):
    _, studs = klass
    tok = studs[0]["student_token"]
    fw = FakeWorker([item(tok)])
    pull(conn, fw)
    pull(conn, fw)
    assert fw.calls[0] == "/submissions"
    assert fw.calls[1].startswith("/submissions?since=2026-11-06T12%3A55")
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1


def test_replacement_keeps_first_ts(conn, klass):
    _, studs = klass
    tok = studs[0]["student_token"]
    apply_items(conn, [item(tok, key="2026-11-06/x.png")])
    rep = apply_items(conn, [item(tok, ts="2026-11-06T08:15:00.000Z", key="2026-11-06/x.jpg", n=2)])
    assert rep.updated == 1
    row = conn.execute("SELECT * FROM submissions").fetchone()
    assert row["photo_key"] == "2026-11-06/x.jpg" and row["ts"] == "2026-11-06T08:15:00.000Z"


def test_phone_upload_after_teacher_mark_becomes_phone(conn, klass):
    _, studs = klass
    ops.teacher_submit(conn, studs[0]["id"], TODAY)
    apply_items(conn, [item(studs[0]["student_token"])])
    assert conn.execute("SELECT source FROM submissions").fetchone()[0] == "phone"


def test_rotated_token_still_maps(conn, klass):
    _, studs = klass
    old = studs[0]["student_token"]
    ops.rotate_tokens(conn, studs[0])
    rep = apply_items(conn, [item(old)])
    assert rep.new == 1 and rep.unknown_tokens == 0


def test_submission_links_to_session(conn, klass):
    cls, studs = klass
    sid = ops.ensure_session(conn, cls["id"], TODAY)
    apply_items(conn, [item(studs[0]["student_token"])])
    assert conn.execute("SELECT session_id FROM submissions").fetchone()[0] == sid
