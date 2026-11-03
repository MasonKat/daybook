"""F5: handout QR codes never carry a token."""
import json
from datetime import date

import pytest
from click.testing import CliRunner

from console import ops, qr
from console.cli import cli
from console.config import Config


def test_go_url_only(conn, klass):
    cfg = Config(pages_url="https://daybook.pages.dev")
    assert qr.go_url(cfg, "Adaptive_Screeners_3S_Nov06_G10") == "https://daybook.pages.dev/go/?set=Adaptive_Screeners_3S_Nov06_G10"
    with pytest.raises(qr.QRError):
        qr.go_url(Config(), "X")                                 # no pages_url
    with pytest.raises(qr.QRError):
        qr.go_url(cfg, "../s/abc")                              # not a set id


def test_qr_svg_and_sheet_have_no_token(conn, klass, tmp_path, monkeypatch):
    import segno  # noqa: F401
    cls, studs = klass
    ops.ensure_session(conn, cls["id"], date(2026, 11, 6), "Adaptive_Screeners_3S_Nov06_G10")
    conn.commit()
    cfgp = tmp_path / "daybook.json"
    cfgp.write_text(json.dumps({"pages_url": "https://daybook.pages.dev"}), encoding="utf-8")
    monkeypatch.setenv("DAYBOOK_CONFIG", str(cfgp))
    monkeypatch.chdir(tmp_path)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "qr", "고1A", "--set", "Adaptive_Screeners_3S_Nov06_G10", "--out", "q.png"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "q.png").read_bytes()[:4] == b"\x89PNG"
    r = CliRunner().invoke(cli, ["--db", db, "qr", "sheet", "고1A"])
    assert r.exit_code == 0, r.output
    html = (tmp_path / "qr" / "고1A-sheet.html").read_text(encoding="utf-8")
    assert "<svg" in html and "11/6 과제" in html and "@page" in html
    for s in studs:
        assert s["student_token"] not in html and s["parent_token"] not in html and s["full_name"] not in html
    # the QR decodes to the /go/ URL: segno encodes the URL verbatim, and the sheet carries no other URL
    assert "/s/" not in html and "/p/" not in html
