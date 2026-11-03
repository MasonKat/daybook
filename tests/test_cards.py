"""G4: printable 안내 cards. No printed paper ever carries a token, as text or as a QR."""
import re

from click.testing import CliRunner

from console import cards as C, demo
from console.cli import cli

from conftest import TODAY


def test_no_card_carries_a_token_a_link_a_qr_or_private_data(conn, tmp_path):
    demo.seed(conn, today=TODAY)
    rows = conn.execute("SELECT * FROM students").fetchall()
    secrets = [r["student_token"] for r in rows] + [r["parent_token"] for r in rows] + [r["full_name"] for r in rows] \
        + [r["ext_id"] for r in rows if r["ext_id"]]
    for cls in conn.execute("SELECT * FROM classes"):
        n = conn.execute("SELECT COUNT(*) FROM students WHERE class_id = ? AND active = 1", (cls["id"],)).fetchone()[0]
        for kind in ("students", "parents"):
            html = C.render(conn, kind, cls["name"])
            hit = [x for x in secrets if x in html]
            assert not hit, (cls["name"], kind, hit[:2])
            assert not re.search(r"https?://|/s/|/p/|pages\.dev", html), (cls["name"], kind)        # no link at all
            assert not re.search(r"qr|<img|data:image|<canvas", html, re.I), (cls["name"], kind)     # no QR / image
            if kind == "students":
                assert html.count('<div class="card">') == n and "내 링크는 선생님 카톡으로 받아요" in html
                assert "잃어버리면 선생님께 (새 링크 발급)" in html and "홈 화면에 추가" in html
            else:
                assert html.count('<section class="letter">') == n
                assert "{원장·학원 문구}" in html and "{개인정보 안내 문구}" in html and "토요일" in html
                assert "등수나 순위" in html and "카카오톡 1:1" in html


def test_cli_cards(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "cards", "students", "고1A"])
    assert r.exit_code == 0 and (tmp_path / "cards" / "고1A-students.html").exists()
    assert "가람" in (tmp_path / "cards" / "고1A-students.html").read_text(encoding="utf-8")
    r = CliRunner().invoke(cli, ["--db", db, "cards", "parents", "고1A"])
    assert r.exit_code == 0 and "도담 학생의 공부 기록" in (tmp_path / "cards" / "고1A-parents.html").read_text(encoding="utf-8")
    assert CliRunner().invoke(cli, ["--db", db, "cards", "students", "없는반"]).exit_code != 0
