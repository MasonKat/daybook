"""F10: videos per class and grade, several video files, pins."""
import json
from datetime import datetime, time

from console import generate
from console.config import Config
from console.db import KST
from console.importers import add_student

from conftest import TODAY

NOW = datetime.combine(TODAY, time(21), KST)


def v(title, kind="long", added="2026-11-05", **kw):
    return {"title": title, "url": f"https://example.com/q/{title}/", "group": "g", "added": added, "kind": kind, **kw}


def setup(conn, klass, tmp_path, files):
    cls, studs = klass
    conn.execute("UPDATE classes SET grade = '고1' WHERE id = ?", (cls["id"],))
    conn.execute("INSERT INTO classes (name, term, weekday_mask, grade) VALUES ('고2A', '2026-10-01..2026-12-31', 18, '고2')")
    other = add_student(conn, 2, "오세린", ext_id="X1")
    conn.commit()
    d = tmp_path / "videos"
    d.mkdir()
    for name, items in files.items():
        (d / name).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    cfg = Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t", videos_path=str(d))
    return cfg, studs[0], other


def titles(ctx, sid, key):
    me = next(s for s in ctx["students"] if s["id"] == sid)
    return [x["title"] for x in me[key]]


def test_class_and_grade_tags_untagged_for_all_folder_merge_and_pins(conn, klass, tmp_path):
    cfg, g1, g2 = setup(conn, klass, tmp_path, {
        "solutions.json": [v("고1A 풀이", classes=["고1A"], pin=True, added="2026-10-01"), v("고2 풀이", grades=["고2"])],
        "shorts.json": [v("모두 쇼츠", kind="short"), v("고2 쇼츠", kind="short", grades="고2")],
        "concepts.json": [v("모두 개념", added="2026-11-06"), v("고1 개념", grades=["고1"]),
                          v("고1A 풀이", classes=["고2A"])],          # the same URL again: the first file (concepts) wins
    })
    assert [f.name for f in generate.video_files(cfg)] == ["concepts.json", "shorts.json", "solutions.json"]
    ctx = generate.build_context(conn, cfg, TODAY, NOW)
    assert titles(ctx, g1["id"], "shorts") == ["모두 쇼츠"]
    assert titles(ctx, g2["id"], "shorts") == ["모두 쇼츠", "고2 쇼츠"]
    # 고1 never sees a 고2-only item; untagged items show to all; the pinned item is on top for its class only
    assert "고2 풀이" not in titles(ctx, g1["id"], "longs") and "고2 풀이" in titles(ctx, g2["id"], "longs")
    assert titles(ctx, g1["id"], "longs") == ["모두 개념", "고1 개념"]           # 고1A 풀이 came from concepts.json (고2A)
    assert titles(ctx, g2["id"], "longs") == ["모두 개념", "고1A 풀이", "고2 풀이"]   # … so it is 고2A's, not pinned
    stories = {s["id"]: [st for st in s["stories"] if st["key"] == "videos"] for s in ctx["students"]}
    assert all("고2" not in x["title"] for st in stories[g1["id"]] for x in st["items"])


def test_pin_sorts_first(conn, klass, tmp_path):
    cfg, g1, _ = setup(conn, klass, tmp_path, {"a.json": [v("새 개념", added="2026-11-06"), v("이번 주 풀이", pin=True, added="2026-10-20",
                                                                                     classes=["고1A"])]})
    ctx = generate.build_context(conn, cfg, TODAY, NOW)
    assert titles(ctx, g1["id"], "longs") == ["이번 주 풀이", "새 개념"]


def test_unknown_class_or_grade_is_a_warning_not_a_stop(conn, klass, tmp_path):
    cfg, g1, _ = setup(conn, klass, tmp_path, {"a.json": [v("오타 반", classes=["고1Z"]), v("오타 학년", grades=["중3"]), v("정상")]})
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings if f.level == "error"]
    warns = [f for f in res.findings if f.kind == "video_tag"]
    assert [f.level for f in warns] == ["warning", "warning"] and "고1Z" in warns[0].detail + warns[1].detail
    page = (tmp_path / "dist" / "s" / g1["student_token"] / "index.html").read_text(encoding="utf-8")
    assert "정상" in page and "오타 반" not in page


def test_a_single_file_still_works(conn, klass, tmp_path):
    cls, studs = klass
    p = tmp_path / "videos.json"
    p.write_text(json.dumps([v("하나")], ensure_ascii=False), encoding="utf-8")
    cfg = Config(videos_path=str(p))
    assert [x["title"] for x in generate.load_videos(cfg)] == ["하나"]
