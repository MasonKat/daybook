"""F15.4: the mascot slot: shown small next to the wordmark when brand.mascot exists; taste only when public."""
from datetime import datetime, time

from console import brand, demo, generate, importers
from console.config import ROOT, Config
from console.db import KST

from conftest import TODAY

NOW = datetime.combine(TODAY, time(21), KST)


def test_mascot_on_pages_board_and_taste_only_when_public(conn, tmp_path):
    demo.seed(conn, today=TODAY)
    importers.import_screeners(conn, ROOT / "demo" / "screeners")
    png = brand.write_placeholder(tmp_path / "brand" / "m.png")
    base = dict(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t", videos_path=str(ROOT / "videos.json"))
    cfg = Config(**base, extra={"brand": {"mascot": str(png)}})
    res = generate.publish(conn, cfg, tmp_path / "d", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings]
    data = (tmp_path / "d" / "b" / "mascot.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) <= brand.MAX_BYTES
    for kind in ("s", "p"):
        html = (next((tmp_path / "d" / kind).iterdir()) / "index.html").read_text(encoding="utf-8")
        assert '<img class="mascot" src="/b/mascot.png"' in html
    t = generate.publish(conn, cfg, tmp_path / "t", today=TODAY, now=NOW, taste=True)
    assert t.ok and not (tmp_path / "t" / "b").exists()
    assert 'class="mascot"' not in (tmp_path / "t" / "s" / "kong" / "index.html").read_text(encoding="utf-8")
    pub = Config(**base, extra={"brand": {"mascot": str(png), "mascot_public": True}})
    t = generate.publish(conn, pub, tmp_path / "t2", today=TODAY, now=NOW, taste=True)
    assert t.ok and 'class="mascot"' in (tmp_path / "t2" / "s" / "kong" / "index.html").read_text(encoding="utf-8")
    none = generate.publish(conn, Config(**base, extra={"brand": {"mascot": str(tmp_path / "missing.png")}}), tmp_path / "n",
                            today=TODAY, now=NOW)
    assert none.ok and not (tmp_path / "n" / "b").exists()                         # fallback: nothing
    assert brand.data_uri(cfg).startswith("data:image/png;base64,") and brand.data_uri(Config()) == ""


def test_board_shows_the_mascot(conn, klass, tmp_path):
    from console import board as boardmod
    png = brand.write_placeholder(tmp_path / "m.png")
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    b = boardmod.Board(db, Config(moments_dir=str(tmp_path / "mm"), extra={"brand": {"mascot": str(png)}}), "고1A", TODAY, poll=False)
    assert '<img class="mascot" src="data:image/png;base64,' in b.page()
