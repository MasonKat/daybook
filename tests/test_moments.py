"""F4: 오늘의 장면 — cleaned on the laptop (EXIF, size), gated by y/N, class-wide, at most 3 on the parent strip."""
import base64
import io
import json
import random
import threading
import urllib.request
from datetime import date, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image

from console import generate, moments as mm, ops
from console.cli import cli
from console.config import Config
from console.db import KST

from conftest import TODAY

NOW = datetime(2026, 11, 6, 22, 0, tzinfo=KST)


@pytest.fixture
def mcfg(tmp_path):
    return Config(worker_url="", moments_dir=str(tmp_path / "moments"))


def photo_with_gps(tmp_path, w=3000, h=2200) -> bytes:
    rng = random.Random(3)
    im = Image.new("RGB", (w // 10, h // 10))
    im.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(im.width * im.height)])
    im = im.resize((w, h))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"                      # Make
    exif[0x0112] = 6                                 # Orientation: rotate 90
    exif[0x8825] = {1: "N", 2: (37.0, 33.0, 0.0)}    # GPSInfo
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95, exif=exif)
    return buf.getvalue()


def test_exif_is_gone_and_size_capped(tmp_path):
    raw = photo_with_gps(tmp_path)
    assert b"PhoneMaker" in raw and len(raw) > 200 * 1024
    out = mm.prepare_image(raw)
    assert len(out) <= 200 * 1024 and b"Exif" not in out and b"PhoneMaker" not in out
    with Image.open(io.BytesIO(out)) as im:
        assert max(im.size) <= 1080 and not im.getexif()
        assert im.height > im.width                  # the orientation was applied before EXIF was dropped
    with pytest.raises(mm.MomentError):
        mm.prepare_image(b"not an image")


def test_gate_blocks_and_three_per_session(conn, klass, mcfg, tmp_path):
    cls, _ = klass
    raw = photo_with_gps(tmp_path, 800, 600)
    with pytest.raises(mm.MomentError, match="not posted"):
        mm.add_moment(conn, mcfg, cls, TODAY, raw, "칠판", confirmed=False)
    assert conn.execute("SELECT COUNT(*) FROM moments").fetchone()[0] == 0
    assert not (tmp_path / "moments").exists()                        # nothing stored before the yes
    sent = []
    for i in range(3):
        mm.add_moment(conn, mcfg, cls, TODAY, raw, f"장면 {i}", confirmed=True, push=sent.append)
    with pytest.raises(mm.MomentError, match="at most 3"):
        mm.add_moment(conn, mcfg, cls, TODAY, raw, "넷째", confirmed=True)
    assert [p["n"] for p in sent] == [1, 2, 3] and sent[0]["class"] == ops.class_key(cls["id"])
    assert base64.b64decode(sent[0]["image"])[:2] == b"\xff\xd8" and "고1A" not in json.dumps(sent, ensure_ascii=False)
    with pytest.raises(mm.MomentError, match="longer"):
        mm.add_moment(conn, mcfg, cls, date(2026, 11, 3), raw, "x" * 61, confirmed=True)


def test_cli_asks_and_only_y_uploads(conn, klass, tmp_path, monkeypatch):
    p = tmp_path / "board.jpg"
    p.write_bytes(photo_with_gps(tmp_path, 900, 700))
    cfgp = tmp_path / "daybook.json"
    cfgp.write_text(json.dumps({"moments_dir": str(tmp_path / "store")}), encoding="utf-8")
    monkeypatch.setenv("DAYBOOK_CONFIG", str(cfgp))
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "moment", "add", "고1A", "--photo", str(p), "--text", "멋진 풀이"], input="\n")
    assert r.exit_code != 0 and "not posted" in r.output and "보이지 않나요" in r.output
    r = CliRunner().invoke(cli, ["--db", db, "moment", "add", "고1A", "--photo", str(p), "--text", "멋진 풀이"], input="y\n")
    assert r.exit_code == 0, r.output
    assert conn.execute("SELECT COUNT(*) FROM moments").fetchone()[0] == 1
    assert len(list((tmp_path / "store").rglob("*.jpg"))) == 1


def test_pages_bake_moments_and_parent_strip_shows_at_most_3(conn, klass, mcfg, tmp_path):
    cls, studs = klass
    raw = photo_with_gps(tmp_path, 900, 700)
    for d in (date(2026, 11, 3), date(2026, 11, 6)):
        for i in range(2):
            mm.add_moment(conn, mcfg, cls, d, raw, f"{d.day}일 장면 {i}", confirmed=True)
    mcfg.worker_url = "https://w.example"
    res = generate.publish(conn, mcfg, tmp_path / "dist", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings]
    ck = ops.class_key(cls["id"])
    files = sorted(p.name for p in (tmp_path / "dist" / "m" / ck).iterdir())
    assert files == ["2026-11-03_1.jpg", "2026-11-03_2.jpg", "2026-11-06_1.jpg", "2026-11-06_2.jpg"]
    assert all((tmp_path / "dist" / "m" / ck / f).stat().st_size <= 100 * 1024 for f in files)
    p_html = (tmp_path / "dist" / "p" / studs[0]["parent_token"] / "index.html").read_text(encoding="utf-8")
    newest = p_html.split('<div class="msg newest">')[1]
    assert newest.count('<li><img src="/m/') == 3 and "이번 주 수업 장면" in newest
    assert "🔥" not in p_html                                            # no reaction counts for parents
    s_html = (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert s_html.count('class="slide s-moment"') == 4 and 'data-story="moments" data-hash' in s_html
    assert 'data-story="moments" data-hash="' in s_html and 'hidden>' not in s_html.split('data-story="moments"')[1][:40]


def test_board_moment_needs_the_gate(conn, klass, tmp_path):
    from http.server import ThreadingHTTPServer
    from console import board as boardmod
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    b = boardmod.Board(db_path, Config(moments_dir=str(tmp_path / "bm")), "고1A", TODAY, poll=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), boardmod.make_handler(b))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/api/moment"
    img = "data:image/jpeg;base64," + base64.b64encode(photo_with_gps(tmp_path, 700, 500)).decode()

    def post(body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        code, body = post({"image": img, "text": "칠판", "confirm": False})
        assert code == 400 and "not posted" in body["error"]
        code, body = post({"image": img, "text": "칠판", "confirm": True})
        assert code == 200 and body["moments"] == 1 and body["message"] == "장면을 올렸어요"
    finally:
        httpd.shutdown()
