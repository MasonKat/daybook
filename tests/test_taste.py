"""F1: demo student 콩 and the taste build (dist-taste/: only 콩, no Worker anywhere)."""
import re
from datetime import datetime, timedelta

import pytest

from console import demo, generate, importers, lint, models as M
from console.config import ROOT, Config
from console.db import KST

NOW = datetime(2026, 11, 6, 21, 0, tzinfo=KST)
TODAY = NOW.date()


@pytest.fixture
def demo_db(conn):
    demo.seed(conn, today=TODAY)
    importers.import_screeners(conn, ROOT / "demo" / "screeners")
    return conn


def test_kong_has_the_rich_month(demo_db):
    conn = demo_db
    k = conn.execute("SELECT * FROM students WHERE slug = '콩'").fetchone()
    assert k["display_name"] == "콩" and k["full_name"] == "강콩" and k["ext_id"] == "S913"
    cls = conn.execute("SELECT * FROM classes WHERE id = ?", (k["class_id"],)).fetchone()
    subs = {datetime.fromisoformat(r[0]).date() for r in conn.execute("SELECT day FROM submissions WHERE student_id = ?", (k["id"],))}
    cd = M.class_days(conn, cls, TODAY)
    assert TODAY not in subs                                         # today is left open for the visitor
    assert M.streak(subs, cd, TODAY) == 21                           # 21 days with one honest gap
    first = TODAY - timedelta(days=22)
    gaps = [first + timedelta(days=i) for i in range(22) if first + timedelta(days=i) not in subs]
    assert len(gaps) == 1 and gaps[0] not in cd
    hearts = conn.execute("SELECT COUNT(*) FROM attitude WHERE student_id = ? AND points = 3", (k["id"],)).fetchone()[0]
    assert hearts == 5
    assert conn.execute("SELECT COUNT(*) FROM notes WHERE student_id = ?", (k["id"],)).fetchone()[0] == 3
    eff = [r[0] for r in conn.execute("SELECT efficiency FROM tests WHERE student_id = ? ORDER BY date", (k["id"],))]
    assert len(eff) == 3 and eff == sorted(eff) and M.direction(eff, M.EFFICIENCY_THRESHOLD) == "up"


def test_taste_build_only_kong_and_no_worker(demo_db, tmp_path):
    cfg = Config(worker_url="http://127.0.0.1:8788", pages_url="http://127.0.0.1:8790", teacher_token="x",
                 videos_path=str(ROOT / "videos.json"))
    res = generate.publish(demo_db, cfg, tmp_path / "dist-taste", today=TODAY, now=NOW, taste=True)
    assert res.ok, [str(f) for f in res.findings]
    out = tmp_path / "dist-taste"
    pages = sorted(p.relative_to(out).as_posix() for p in out.rglob("index.html"))
    assert pages == ["index.html", "p/kong/index.html", "s/kong/index.html"]
    others = [r["display_name"] for r in demo_db.execute("SELECT display_name FROM students WHERE slug != '콩'")]
    for f in out.rglob("*"):
        if f.suffix in (".html", ".json", ".webmanifest", ".css", ".js") or f.name == "_headers":
            text = f.read_text(encoding="utf-8")
            assert "127.0.0.1:8788" not in text and "강콩" not in text, f
            if f.suffix == ".html":
                assert "체험판 · 기록되지 않아요" in text
                assert not any(n in text for n in others), f
    landing = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="/s/kong/#today"' in landing and 'href="/p/kong/"' in landing
    assert "학생 화면 보기" in landing and "학부모 화면 보기" in landing
    student = (out / "s" / "kong" / "index.html").read_text(encoding="utf-8")
    assert '"taste":true' in student and '"worker":""' in student
    assert "connect-src 'self';" in (out / "_headers").read_text(encoding="utf-8")


def test_taste_lint_catches_a_worker_url_and_other_names(demo_db, tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "index.html").write_text(
        '<title>AMP 학습 기록</title><p>가람</p><script>fetch("https://w.example.workers.dev/submit")</script>',
        encoding="utf-8")
    kinds = [f.kind for f in lint.lint_taste(tmp_path, lint.private_rows(demo_db), "콩", ["https://w.example.workers.dev"])]
    assert kinds.count("taste_worker") >= 2 and "taste_name" in kinds


def test_taste_refuses_a_real_database(conn, klass, cfg, tmp_path):
    with pytest.raises(ValueError, match="demo database"):
        generate.publish(conn, cfg, tmp_path / "t", today=TODAY, now=NOW, taste=True)
    assert not (tmp_path / "t").exists()


def test_taste_lint_rejects_test_text(demo_db, tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "index.html").write_text(
        '<title>AMP 학습 기록</title><p class="mtext">e2e 오늘의 장면</p><p>Dummy line</p>'
        '<script>var tests = 1;</script><div data-x="test">주간 테스트</div>', encoding="utf-8")
    found = [f.detail.lower() for f in lint.lint_taste(tmp_path, lint.private_rows(demo_db), "콩", []) if f.kind == "taste_test_text"]
    assert found == ["e2e", "dummy"]          # script code, attributes and Korean 테스트 are not visible test text


def test_push_tokmap_after_publish(demo_db, monkeypatch):
    from console import pull
    sent = {}
    monkeypatch.setattr(pull, "http_put_json", lambda cfg, path, payload: sent.update(path=path, payload=payload) or {"n": len(payload["tokmap"])})
    msg = pull.push_tokmap(demo_db, Config(worker_url="https://w.example", teacher_token="t"))
    assert sent["path"] == "/tokmap" and len(sent["payload"]["tokmap"]) == 13 and "13 student links" in msg
    assert pull.push_tokmap(demo_db, Config()) == ""                  # no Worker configured: nothing to do
    def boom(*a):
        raise pull.PullError("offline")
    monkeypatch.setattr(pull, "http_put_json", boom)
    assert pull.push_tokmap(demo_db, Config(worker_url="https://w.example", teacher_token="t")).startswith("warning")


def test_board_polls_only_in_class_time():
    from datetime import date
    from console import ops
    w = ops.board_window(date(2026, 11, 6), "18:00")
    assert (w[0].strftime("%H:%M"), w[1].strftime("%H:%M")) == ("17:30", "20:00")
    assert ops.board_window(date(2026, 11, 6), None) is None
