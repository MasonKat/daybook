"""Publish + the no-PII lint (§11): a seeded phone, full_name and ext_id each stop publish; dist/ is untouched."""
import re
from datetime import datetime
from pathlib import Path

import pytest

from console import generate, lint, ops
from console.db import KST

from conftest import TODAY

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=KST)


def publish(conn, cfg, tmp_path):
    return generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW)


def test_clean_publish_renders_every_page(conn, cfg, klass, tmp_path):
    _, studs = klass
    res = publish(conn, cfg, tmp_path)
    assert res.ok, [str(f) for f in res.findings]
    dist = tmp_path / "dist"
    for s in studs:
        assert (dist / "s" / s["student_token"] / "index.html").exists()
        assert (dist / "s" / s["student_token"] / "manifest.webmanifest").exists()
        assert (dist / "p" / s["parent_token"] / "index.html").exists()
    assert (dist / "go" / "index.html").exists()
    for html in dist.rglob("*.html"):
        text = html.read_text(encoding="utf-8")
        assert re.findall(r"<title>(.*?)</title>", text) == ["AMP 학습 기록"]
        assert '<meta name="robots" content="noindex,nofollow">' in text
        assert html.stat().st_size <= 100 * 1024
        for s in studs:
            assert s["full_name"] not in text and s["ext_id"] not in text


def test_pages_show_only_their_own_student(conn, cfg, klass, tmp_path):
    _, studs = klass
    assert publish(conn, cfg, tmp_path).ok
    dist = tmp_path / "dist"
    for s in studs:
        for kind, tok in (("s", s["student_token"]), ("p", s["parent_token"])):
            text = (dist / kind / tok / "index.html").read_text(encoding="utf-8")
            assert s["display_name"] in text
            for other in studs:
                if other["id"] != s["id"]:
                    assert other["display_name"] not in text
                    assert other["student_token"] not in text and other["parent_token"] not in text


def test_student_page_never_contains_parent_token_and_vice_versa(conn, cfg, klass, tmp_path):
    _, studs = klass
    publish(conn, cfg, tmp_path)
    s = studs[0]
    assert s["parent_token"] not in (tmp_path / "dist" / "s" / s["student_token"] / "index.html").read_text(encoding="utf-8")
    assert s["student_token"] not in (tmp_path / "dist" / "p" / s["parent_token"] / "index.html").read_text(encoding="utf-8")


def _assert_stopped(res, kind, dist):
    assert not res.ok
    assert kind in {f.kind for f in lint.errors(res.findings)}
    # the previous good dist/ is untouched
    assert (dist / "go" / "index.html").exists()
    assert not (dist.parent / ".dist-staging").exists()


def test_lint_catches_seeded_phone(conn, cfg, klass, tmp_path):
    _, studs = klass
    assert publish(conn, cfg, tmp_path).ok
    ops.add_note(conn, studs[0]["id"], "2026-W45", "어머님 연락처 010-1234-5678 로 연락 주세요")
    conn.commit()
    _assert_stopped(publish(conn, cfg, tmp_path), "phone", tmp_path / "dist")


@pytest.mark.parametrize("phone", ["01012345678", "011-123-4567", "019-9876-5432"])
def test_phone_pattern_variants(phone):
    assert lint.PHONE_RE.search(f"x {phone} y")


def test_lint_catches_seeded_full_name(conn, cfg, klass, tmp_path):
    _, studs = klass
    assert publish(conn, cfg, tmp_path).ok
    conn.execute("UPDATE students SET display_name = full_name WHERE id = ?", (studs[1]["id"],))
    conn.commit()
    _assert_stopped(publish(conn, cfg, tmp_path), "full_name", tmp_path / "dist")


def test_lint_catches_seeded_ext_id(conn, cfg, klass, tmp_path):
    _, studs = klass
    assert publish(conn, cfg, tmp_path).ok
    ops.add_note(conn, studs[2]["id"], "2026-W45", "S903 확인")
    conn.commit()
    _assert_stopped(publish(conn, cfg, tmp_path), "ext_id", tmp_path / "dist")


def test_lint_catches_parent_phone_and_titles_and_third_party(tmp_path, conn, klass):
    _, studs = klass
    conn.execute("UPDATE students SET parent_phone = '010 2222 3333' WHERE id = ?", (studs[0]["id"],))
    d = tmp_path / "site"
    (d / "x").mkdir(parents=True)
    (d / "x" / "index.html").write_text(
        '<html><head><title>가람 기록</title><meta property="og:title" content="가람">'
        '<script src="https://cdn.example/x.js"></script></head><body>01022223333</body></html>', encoding="utf-8")
    (d / "a.css").write_text("@import url(https://fonts.example/css);", encoding="utf-8")
    kinds = {f.kind for f in lint.errors(lint.lint_dir(d, lint.private_rows(conn), "AMP 학습 기록"))}
    assert {"title", "og", "third_party", "parent_phone", "phone"} <= kinds


def test_lint_size_limit(tmp_path):
    (tmp_path / "big.html").write_text("<title>AMP 학습 기록</title>" + "x" * (101 * 1024), encoding="utf-8")
    kinds = {f.kind for f in lint.lint_dir(tmp_path, [], "AMP 학습 기록")}
    assert "size" in kinds


def test_parent_page_blocks(conn, cfg, klass, tmp_path):
    from console import importers
    cls, studs = klass
    s = studs[0]
    sid = ops.ensure_session(conn, cls["id"], TODAY)
    ops.set_attitude(conn, s["id"], sid, 3)
    ops.teacher_submit(conn, s["id"], TODAY)
    ops.add_note(conn, s["id"], "2026-W45", "이번 주도 잘했어요.")
    importers.import_screeners(conn, Path(__file__).resolve().parent.parent / "demo" / "screeners")
    conn.commit()
    assert publish(conn, cfg, tmp_path).ok
    html = (tmp_path / "dist" / "p" / s["parent_token"] / "index.html").read_text(encoding="utf-8")
    newest = html.split('<div class="msg newest">')[1]
    order = [html.index("AMP 수학 · 가람")] + [len(html) - len(newest) + newest.index(k) for k in (
        "주간 리포트", ">제출<", "주간 테스트", ">태도<", "선생님 한 줄", "이 방은 주간 기록 전용입니다", "업데이트 2026-11-06 10:00")]
    assert order == sorted(order)
    assert "<svg" in newest and "<polyline" in newest
    assert re.search(r'class="dir (up|keep|adjust)">(상승|유지|조정)<', html)
    assert "이번 주 +3 · 누적 3점" in newest
    assert "제출 1일" in newest and "(수업일 2일 중 1일)" in newest      # Tue + Fri so far, Fri submitted
    assert 'class="bar"' in newest and "피자까지" in newest
    assert "이번 주도 잘했어요." in newest
    assert "<a " not in html and "<input" not in html and "<textarea" not in html  # no links out, no chat input
    assert re.findall(r"<script[^>]*>", html) == ['<script src="/static/app.js" defer>']  # only scroll-to-bottom
    assert "정답률" not in html


def test_go_page_has_no_token(conn, cfg, klass, tmp_path):
    _, studs = klass
    publish(conn, cfg, tmp_path)
    go = (tmp_path / "dist" / "go" / "index.html").read_text(encoding="utf-8")
    assert "먼저 선생님이 준 내 링크를 한 번 열어 주세요" in go
    assert not re.search(r"[a-z2-7]{26}", go)


def test_headers_and_robots(conn, cfg, klass, tmp_path):
    publish(conn, cfg, tmp_path)
    h = (tmp_path / "dist" / "_headers").read_text(encoding="utf-8")
    assert "X-Robots-Tag: noindex" in h and "connect-src 'self' https://w.example" in h
    assert (tmp_path / "dist" / "robots.txt").read_text(encoding="utf-8").strip().endswith("Disallow: /")


def test_inactive_and_rotated_pages_disappear(conn, cfg, klass, tmp_path):
    cls, studs = klass
    publish(conn, cfg, tmp_path)
    old = studs[0]["student_token"]
    ops.rotate_tokens(conn, studs[0])
    conn.execute("UPDATE students SET active = 0 WHERE id = ?", (studs[1]["id"],))
    conn.commit()
    assert publish(conn, cfg, tmp_path).ok
    assert not (tmp_path / "dist" / "s" / old).exists()
    assert not (tmp_path / "dist" / "s" / studs[1]["student_token"]).exists()
    assert len(list((tmp_path / "dist" / "s").iterdir())) == 2
