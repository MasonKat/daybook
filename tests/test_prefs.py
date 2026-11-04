"""F22.4 ⚙ 설정: an avatar of the student's choice (presets, or an own picture the teacher approves first) and a reorder —
never a hide — of their page sections, stored per token on the Worker and copied to SQLite by pull."""
import base64
import io
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from console import generate, prefs as PF, teacher as T
from console.config import ROOT, Config
from console.pull import pull

from conftest import TODAY


def test_the_section_ids_match_the_worker():
    src = (ROOT / "worker" / "src" / "index.js").read_text(encoding="utf-8")
    js = json.loads(re.search(r"export const PREF_SECTIONS = (\{.*?\});", src).group(1))
    assert js == PF.SECTIONS
    assert f"p:[a-z]{{2,10}}-[0-{len(PF.COLORS) - 1}]" in src and all(re.fullmatch(r"[a-z]{2,10}", d) for d in PF.DESIGN_IDS)


def test_unknown_ids_and_hiding_are_refused():
    rings = list(reversed(PF.SECTIONS["rings"]))
    assert PF.validate({"rings": rings, "avatar": "p:cat-3"}) == {"rings": rings, "avatar": "p:cat-3"}
    for bad in ({"rings": rings[1:]},                       # a hide attempt: every id must be present
                {"rings": rings[1:] + rings[1:2]},          # exactly once
                {"rings": rings[1:] + ["ads"]},             # an unknown section id
                {"today": ["routine"]}, {"theme_css": "x"}, {"avatar": "p:mickey-1"}, {"avatar": "p:cat-9"}, [1]):
        with pytest.raises(PF.PrefsError):
            PF.validate(bad)
    assert len(PF.DESIGN_IDS) >= 24 and all(PF.avatar_svg(f"p:{d}-0").startswith("<svg") for d in PF.DESIGN_IDS)


def test_the_routine_is_pinned_first_during_the_class_window():
    mine = ["vote", "test", "routine", "classwork"]
    assert PF.ordered("today", mine) == mine
    assert PF.ordered("today", mine, pin_routine=True) == ["routine", "vote", "test", "classwork"]
    assert PF.ordered("today", None) == PF.SECTIONS["today"]                          # 기본 순서로
    r = PF.ordered("rings", ["goal", "nope"])                                         # an old / partial copy: nothing goes missing
    assert sorted(r) == sorted(PF.SECTIONS["rings"]) and "nope" not in r
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "if (pin && kind === 'today') out = ['routine']" in js and "orderOf(k, prefs[k], inWin())" in js


def _png(px=64):
    b = io.BytesIO()
    Image.new("RGB", (px, px), (200, 120, 40)).save(b, "PNG")
    return b.getvalue()


def _page(tmp_path, tok, kind="s"):
    return (tmp_path / "d" / kind / tok / "index.html").read_text(encoding="utf-8")


def test_an_own_picture_shows_only_after_approval_and_only_on_own_pages(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, studs = klass
    a, b = studs[0], studs[1]
    conn.execute("UPDATE students SET consent = 'yes'")
    conn.commit()
    body = {"prefs": [{"token": a["student_token"], "prefs": json.dumps({"avatar": "own", "profile": list(reversed(PF.SECTIONS["profile"]))}),
                       "updated": "2026-11-06T10:00:00Z"},
                      {"token": b["student_token"], "prefs": json.dumps({"avatar": "p:rocket-4"}), "updated": "2026-11-06T10:00:00Z"},
                      {"token": b["student_token"], "prefs": json.dumps({"rings": ["x"]}), "updated": "z"}],      # invalid: ignored
            "avatars": [{"token": a["student_token"], "status": "pending", "ts": "2026-11-06T10:01:00Z", "image": base64.b64encode(_png()).decode()},
                        {"token": b["student_token"], "status": "pending", "ts": "t", "image": base64.b64encode(b"GIF89a").decode()}]}
    pages = {"/avatars-pull": body}

    def fetch(path):
        return json.dumps(pages.get(path.split("?")[0], {"items": [], "reviews": [], "votes": [], "sets": [], "marks": [],
                                                         "now": "2026-11-06T12:00:00Z"})).encode()
    pull(conn, fetch)
    pull(conn, fetch)                                                       # idempotent
    assert conn.execute("SELECT COUNT(*) FROM avatar_uploads").fetchone()[0] == 1   # the GIF never lands
    assert PF.load(conn, b["id"]) == {"avatar": "p:rocket-4"}
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY).ok
    assert not (tmp_path / "d" / "s" / a["student_token"] / "me.png").exists()          # pending: not shown
    assert '<span class="av ring sm me-av" data-init' in _page(tmp_path, a["student_token"])
    q = T.avatars_view(conn)["pending"]
    assert len(q) == 1 and q[0]["src"].startswith("data:image/png;base64,") and q[0]["name"] == a["display_name"]
    assert T.avatar_decide(conn, Config(), {"id": q[0]["id"], "ok": True})["ok"]
    with pytest.raises(T.TeacherError):
        T.avatar_decide(conn, Config(), {"id": q[0]["id"], "ok": False})          # already decided
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY).ok
    assert (tmp_path / "d" / "s" / a["student_token"] / "me.png").exists() and (tmp_path / "d" / "p" / a["parent_token"] / "me.png").exists()
    sa = _page(tmp_path, a["student_token"])
    assert '<img src="me.png" alt="">' in sa.split('<header class="sbar">')[1].split("</header>")[0]
    assert '<img src="me.png" alt="">' in _page(tmp_path, a["parent_token"], "p")
    blocks = sa.split('id="me-blocks">')[1]
    assert blocks.index('data-sec="notes"') < blocks.index('data-sec="watched"')     # their own profile order
    for s in studs[1:]:
        assert not (tmp_path / "d" / "s" / s["student_token"] / "me.png").exists() and "me.png" not in _page(tmp_path, s["student_token"])
    sb = _page(tmp_path, b["student_token"])
    assert PF.avatar_svg("p:rocket-4") in sb and PF.avatar_svg("p:rocket-4") not in sa


def test_no_other_students_avatar_in_manitoba_mode(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, studs = klass
    conn.execute("INSERT INTO settings (key, value, ts) VALUES ('practice_mode', 'manitoba', 't')")
    picks = ["p:cat-1", "p:owl-2", "p:frog-5"]
    for s, aid in zip(studs, picks):
        PF.save(conn, s["id"], {"avatar": aid})
    conn.commit()
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY).ok
    for s, aid in zip(studs, picks):
        html = _page(tmp_path, s["student_token"])
        assert PF.avatar_svg(aid) in html
        assert not any(PF.avatar_svg(o) in html for o in picks if o != aid)


def test_uploads_are_size_and_type_checked(conn, klass):
    _, studs = klass
    sid = studs[0]["id"]
    assert PF.add_upload(conn, sid, b"GIF89a" + b"0" * 10, "t1") is None
    assert PF.add_upload(conn, sid, b"\xff\xd8\xff" + b"0" * (61 * 1024), "t2") is None
    assert PF.add_upload(conn, sid, _png(), "t3") and PF.add_upload(conn, sid, _png(), "t3") is None
