"""F15.1 / F15.2: watch signals (pull, counts on pages, the Teacher tab) and the class 인기 video."""
import json
from datetime import datetime, time, timedelta

from console import generate, pull as pullmod, watch as W
from console.config import Config
from console.db import KST
from console.importers import add_student

from conftest import TODAY   # Fri 2026-11-06

NOW = datetime.combine(TODAY, time(21), KST)


def put(conn, sid, vid, day, sec=60, pct=50):
    conn.execute("INSERT OR REPLACE INTO watches (student_id, video_id, day, sec, pct, ts) VALUES (?, ?, ?, ?, ?, 't')",
                 (sid, vid, day if isinstance(day, str) else day.isoformat(), sec, pct))


def cfg_with(tmp_path, videos, **extra):
    p = tmp_path / "videos.json"
    p.write_text(json.dumps(videos, ensure_ascii=False), encoding="utf-8")
    return Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t", videos_path=str(p), extra=extra)


def vid(i, kind="short", **kw):
    return {"title": f"영상{i}", "url": f"https://example.com/v{i}/", "yt_id": f"AMPvideo{i:03d}", "added": "2026-11-01", "kind": kind, **kw}


def test_pull_keeps_the_max_per_student_video_and_day(conn, klass):
    _, studs = klass
    tok = studs[0]["student_token"]
    items = [{"token": tok, "v": "AMPvideo001", "day": "2026-11-05", "sec": 40, "pct": 70, "ts": "t"},
             {"token": tok, "v": "AMPvideo001", "day": "2026-11-05", "sec": 90, "pct": 30, "ts": "t"},
             {"token": "x" * 26, "v": "AMPvideo001", "day": "2026-11-05", "sec": 9, "pct": 9, "ts": "t"},
             {"token": tok, "v": "AMPvideo002", "day": "2026-11-05", "sec": -1, "pct": 9, "ts": "t"}]
    assert pullmod.apply_watches(conn, items) == 2
    assert [tuple(r) for r in conn.execute("SELECT video_id, sec, pct FROM watches")] == [("AMPvideo001", 90, 70)]


def test_own_count_on_the_student_page_and_a_count_on_the_parent_card(conn, klass, tmp_path):
    _, studs = klass
    put(conn, studs[0]["id"], "AMPvideo001", TODAY)
    put(conn, studs[0]["id"], "AMPvideo002", TODAY - timedelta(days=1))
    put(conn, studs[0]["id"], "AMPvideo003", TODAY, sec=5)                      # under 10 s: not "watched"
    put(conn, studs[1]["id"], "AMPvideo001", TODAY)
    conn.commit()
    cfg = cfg_with(tmp_path, [vid(1), vid(2), vid(3, "long")])
    ctx = generate.build_context(conn, cfg, TODAY, NOW)
    me = {s["id"]: s for s in ctx["students"]}
    assert me[studs[0]["id"]]["videos_week"] == 2 and me[studs[2]["id"]]["videos_week"] == 0
    assert me[studs[0]["id"]]["room"][-1]["videos"] == 2
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings]
    s_html = (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    p_html = (tmp_path / "dist" / "p" / studs[0]["parent_token"] / "index.html").read_text(encoding="utf-8")
    assert "이번 주 본 영상 2개" in s_html
    newest = p_html.split('class="msg newest"')[1]
    assert newest.count('<li class="vt ') == 2 and "영상1" in newest and ">쇼츠<" in newest      # F18.1: the child's own tiles
    off = generate.build_context(conn, cfg_with(tmp_path, [vid(1)], parent_shows_videos=False), TODAY, NOW)
    assert next(s for s in off["students"] if s["id"] == studs[0]["id"])["room"][-1]["videos"] == 0


def test_teacher_view_per_class_and_per_student(conn, klass, tmp_path):
    _, studs = klass
    for s in studs:
        put(conn, s["id"], "AMPvideo001", TODAY, sec=120, pct=80)
    put(conn, studs[0]["id"], "AMPvideo002", TODAY - timedelta(days=10))       # outside 7 days, inside 14
    conn.commit()
    videos = generate.load_videos(cfg_with(tmp_path, [vid(1, views=1240, views_7d=88), vid(2)]))
    v7 = W.teacher_view(conn, videos, TODAY, 7)
    c = v7["classes"][0]
    assert c["videos"] == [{"id": "AMPvideo001", "title": "영상1", "kind": "short", "students": 3, "minutes": 6.0, "avg_pct": 80,
                            "views": 1240, "views_7d": 88, "rx": {"fire": 0, "good": 0, "curious": 0, "hard": 0, "try": 0}}]
    assert c["popular"] == "AMPvideo001" and [s["n"] for s in c["students"]] == [1, 1, 1]
    v14 = W.teacher_view(conn, videos, TODAY, 14)
    assert {v["id"] for v in v14["classes"][0]["videos"]} == {"AMPvideo001", "AMPvideo002"}
    text = W.report_text(v14)
    assert "영상1: 3명 · 평균 80% · 6.0분 · 전체 조회 88 (7일)" in text and studs[0]["display_name"] not in text


def test_popular_needs_three_students_and_shows_only_a_badge(conn, klass, tmp_path):
    _, studs = klass
    for s in studs[:2]:
        put(conn, s["id"], "AMPvideo002", TODAY)
    conn.commit()
    cfg = cfg_with(tmp_path, [vid(1, added="2026-11-05"), vid(2, added="2026-10-01", views=1240, views_7d=88), vid(3, "long")])
    ctx = generate.build_context(conn, cfg, TODAY, NOW)
    assert not any(st["key"] == "popular" for s in ctx["students"] for st in s["stories"])        # 2 students: not yet
    put(conn, studs[2]["id"], "AMPvideo002", TODAY - timedelta(days=6))
    conn.commit()
    ctx = generate.build_context(conn, cfg, TODAY, NOW)
    for s in ctx["students"]:                          # the whole class sees it, watched or not
        ring = [st for st in s["stories"] if st["key"] == "popular"]
        assert ring and ring[0]["items"][0]["id"] == "AMPvideo002"
        assert s["shorts"][0]["id"] == "AMPvideo002" and s["shorts"][0]["hot"]               # pinned first
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW)
    assert res.ok
    html = (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert "🔥 인기" in html and "우리 반이 이번 주 많이 본 영상" in html
    assert "1240" not in html and "1,240" not in html and "3명" not in html                    # no counters, no numbers
    for s in studs:
        assert s["full_name"] not in html
    # 14-day window and no pin
    put(conn, studs[0]["id"], "AMPvideo001", TODAY - timedelta(days=10), sec=600)
    ctx = generate.build_context(conn, cfg_with(tmp_path, [vid(1), vid(2)], popular_days=14, popular_pin=False), TODAY, NOW)
    me = next(s for s in ctx["students"] if s["id"] == studs[0]["id"])
    assert [v["id"] for v in me["shorts"]] == ["AMPvideo001", "AMPvideo002"] and me["shorts"][1].get("hot")


def test_another_class_never_counts(conn, klass, tmp_path):
    _, studs = klass
    conn.execute("INSERT INTO classes (name, term, weekday_mask) VALUES ('기초B', '2026-10-01..2026-12-31', 5)")
    others = [add_student(conn, 2, n, ext_id=e) for n, e in (("오세린", "X1"), ("남다온", "X2"), ("표이안", "X3"))]
    for s in others:
        put(conn, s["id"], "AMPvideo002", TODAY)
    conn.commit()
    assert W.popular(conn, 1, TODAY) is None and W.popular(conn, 2, TODAY) == "AMPvideo002"



def test_f18_1_solved_strip_and_video_tiles(conn, klass, tmp_path):
    _, studs = klass
    sid = studs[0]["id"]
    for d, n in (("2026-11-03", 10), ("2026-11-05", 20), ("2026-10-27", 5)):
        conn.execute("INSERT INTO submissions (student_id, day, ts, source, solved) VALUES (?, ?, ?, 'phone', ?)",
                     (sid, d, f"{d}T11:00:00.000Z", n))
    for i in range(8):
        put(conn, sid, f"AMPvideo{i:03d}", TODAY - timedelta(days=i % 4), sec=60, pct=95 if i == 0 else 40)
    conn.commit()
    cfg = cfg_with(tmp_path, [vid(i, "long" if i % 2 else "short", group="고1 · 개념" if i == 1 else "고1 · 풀이") for i in range(8)])
    ctx = generate.build_context(conn, cfg, TODAY, NOW)
    w = next(s for s in ctx["students"] if s["id"] == sid)["room"][-1]
    strip = w["solved_strip"]
    assert [d["n"] for d in strip][:5] == [None, 10, None, 20, None] and strip[3]["pct"] == 100 and strip[1]["pct"] == 50
    assert strip[1]["class_day"] and w["solved"] == 30 and w["solved_delta"] == 25          # 30 vs last week's 5
    assert len(w["video_tiles"]) == 6 and w["video_more"] == 2 and w["videos"] == 8
    first = w["video_tiles"][0]
    assert first["done"] and first["badge"] == "쇼츠" and [t["badge"] for t in w["video_tiles"] if "영상1" == t["title"]] == ["개념"]
    res = generate.publish(conn, cfg, tmp_path / "d", today=TODAY, now=NOW)
    p_html = (tmp_path / "d" / "p" / studs[0]["parent_token"] / "index.html").read_text(encoding="utf-8")
    newest = p_html.split('class="msg newest"')[1]
    assert "+2" in newest and "지난주보다 ↑ 25" in newest and newest.count('class="vok"') == 1
    s_html = (tmp_path / "d" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert 'id="videos-me"' in s_html and 'class="sstrip' in s_html                           # the same components
    other = (tmp_path / "d" / "p" / studs[1]["parent_token"] / "index.html").read_text(encoding="utf-8")
    assert "이번 주 본 영상이 아직 없어요" in other and "영상0" not in other                     # never another child's
