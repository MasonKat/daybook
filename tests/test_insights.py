"""F12: the weekly-test bridge: a student-safe slice of the teacher's own analysis (insights)."""
import json
from datetime import datetime, time

from console import generate, importers
from console.config import ROOT, Config
from console.db import KST

from conftest import TODAY   # Fri 2026-11-06

NOW = datetime.combine(TODAY, time(21), KST)
RND = "Adaptive_Screeners_3S_Nov06_G10"


def write(tmp_path, students, date="2026-11-06", name="ins.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"round": RND, "date": date, "students": students}, ensure_ascii=False), encoding="utf-8")
    return p


def build(conn, tmp_path, videos=None, **extra):
    vp = tmp_path / "videos.json"
    vp.write_text(json.dumps(videos or [], ensure_ascii=False), encoding="utf-8")
    cfg = Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t", videos_path=str(vp), extra=extra)
    return cfg, generate.build_context(conn, cfg, TODAY, NOW, scrub=__import__("console.lint", fromlist=["x"]).text_scrubber(conn))


def me(ctx, sid):
    return next(s for s in ctx["students"] if s["id"] == sid)


def test_the_example_imports_by_ext_id(conn, klass):
    rep = importers.import_insights(conn, ROOT / "mappings" / "insights.example.json")
    assert (rep.rows, rep.imported, rep.unknown, rep.errors) == (1, 1, [], [])
    r = conn.execute("SELECT * FROM insights").fetchone()
    assert r["student_id"] == klass[1][1]["id"] and json.loads(r["focus"]) == ["나머지정리 활용", "항등식 계수비교"]


def test_unknown_ext_id_is_reported_and_skipped_and_caps_apply(conn, klass, tmp_path):
    p = write(tmp_path, [{"ext_id": "S999", "focus": ["x"]},
                         {"ext_id": "S901", "focus": ["a", "b", "c", "d"], "videos": ["https://e.x/1", "ftp://no", "https://e.x/2",
                                                                               "https://e.x/3", "https://e.x/4"]}])
    rep = importers.import_insights(conn, p)
    assert rep.imported == 1 and rep.unknown == ["S999"]
    r = conn.execute("SELECT * FROM insights").fetchone()
    assert json.loads(r["focus"]) == ["a", "b", "c"] and json.loads(r["videos"]) == ["https://e.x/1", "https://e.x/2", "https://e.x/3"]
    bad = tmp_path / "bad.json"
    bad.write_text('{"round": "x"}', encoding="utf-8")
    assert importers.import_insights(conn, bad).errors
    importers.import_insights(conn, write(tmp_path, [{"ext_id": "S901", "focus": ["새로"]}]))     # re-import replaces
    assert conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0] == 1
    assert json.loads(conn.execute("SELECT focus FROM insights").fetchone()[0]) == ["새로"]


def test_review_ring_parent_line_and_matched_videos(conn, klass, tmp_path):
    _, studs = klass
    conn.execute("UPDATE students SET share_tests = 'yes'")          # F21.1: the parent line sits in the test row
    importers.import_insights(conn, write(tmp_path, [{"ext_id": "S901", "focus": ["계수비교"],
                                                      "videos": ["https://example.com/q/v1/", "https://example.com/q/other/"],
                                                      "student_line": "부호 실수가 두 번 있었어요.", "parent_line": "계수비교 복습을 권해요."}]))
    cfg, ctx = build(conn, tmp_path, [{"title": "계수비교 풀이", "url": "https://example.com/q/v1/", "added": "2026-11-01"}])
    st = next(x for x in me(ctx, studs[0]["id"])["stories"] if x["key"] == "review")
    assert st["focus"] == ["계수비교"] and st["line"] == "부호 실수가 두 번 있었어요."
    assert [v["title"] for v in st["items"]] == ["계수비교 풀이", "추천 영상 보기"]          # matched, else a plain link
    week = me(ctx, studs[0]["id"])["room"][-1]
    assert week["insight"] == "계수비교 복습을 권해요." and week["focus"] == []               # no chips for parents by default
    assert not any(x["key"] == "review" for x in me(ctx, studs[1]["id"])["stories"])       # no insight: no ring
    _, ctx2 = build(conn, tmp_path, parent_shows_focus=True)
    assert me(ctx2, studs[0]["id"])["room"][-1]["focus"] == ["계수비교"]
    # published pages: the ring and the line, never the round id
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings]
    s_html = (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    p_html = (tmp_path / "dist" / "p" / studs[0]["parent_token"] / "index.html").read_text(encoding="utf-8")
    assert "이번 주 복습" in s_html and "부호 실수가 두 번" in s_html and "계수비교 복습을 권해요." in p_html
    assert RND not in s_html and RND not in p_html


def test_the_ring_hides_when_there_is_no_insight_this_week(conn, klass, tmp_path):
    _, studs = klass
    importers.import_insights(conn, write(tmp_path, [{"ext_id": "S901", "focus": ["지난주"], "parent_line": "지난주 한 줄"}],
                                          date="2026-10-28"))
    _, ctx = build(conn, tmp_path)
    m = me(ctx, studs[0]["id"])
    assert not any(x["key"] == "review" for x in m["stories"])
    assert m["room"][-1]["insight"] == "" and any(w["insight"] == "지난주 한 줄" for w in m["room"])   # last week's card keeps it


def test_the_scrubber_drops_a_line_with_a_phone_number_or_a_name(conn, klass, tmp_path):
    _, studs = klass
    importers.import_insights(conn, write(tmp_path, [{"ext_id": "S901", "focus": ["한가람 집중", "부호"],
                                                      "student_line": "궁금하면 010-1234-5678로 연락해요",
                                                      "parent_line": "한가람 학생은 잘하고 있어요"}]))
    _, ctx = build(conn, tmp_path)
    st = next(x for x in me(ctx, studs[0]["id"])["stories"] if x["key"] == "review")
    assert st["line"] == "" and st["focus"] == ["부호"]                                    # the full name and the phone are gone
    assert me(ctx, studs[0]["id"])["room"][-1]["insight"] == ""
