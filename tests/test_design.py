"""Design pass v1: stories, reels data, profile tiles, parent room history, and the new lint rules."""
import json
import re
from datetime import date, datetime, timedelta

from console import generate, lint, ops
from console.config import Config
from console.db import KST
from console.thumbs import make_demo_thumbs, make_thumb

from conftest import TODAY

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=KST)


def videos_cfg(tmp_path, items):
    p = tmp_path / "videos.json"
    p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return Config(worker_url="https://w.example", videos_path=str(p))


def test_videos_json_optional_fields_and_thumbs(conn, klass, tmp_path):
    make_demo_thumbs(tmp_path / "thumbs", n=2)
    cfg = videos_cfg(tmp_path, [
        {"title": "A", "url": "https://example.com/1", "group": "고1 · 개념", "added": "2026-11-05",
         "yt_id": "AMPdemo0001", "thumb": "thumbs/l1.jpg"},
        {"title": "B", "url": "https://example.com/2", "group": "고1 · 풀이", "added": "2026-10-01", "yt_id": "bad id"},
        {"title": "C", "url": "javascript:alert(1)", "added": "2026-11-01"},
    ])
    vids = generate.load_videos(cfg)
    assert [v["title"] for v in vids] == ["A", "B"]          # unsafe URL dropped, newest first
    assert vids[0]["yt_id"] == "AMPdemo0001" and vids[0]["thumb"] == "/t/AMPdemo0001.jpg"
    assert vids[1]["yt_id"] == "" and vids[1]["thumb"] == ""   # invalid yt_id ignored -> link-out
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings]
    t = tmp_path / "dist" / "t" / "AMPdemo0001.jpg"
    assert t.exists() and t.stat().st_size <= 60 * 1024
    from PIL import Image
    with Image.open(t) as im:
        assert im.width == 480
    _, studs = klass
    html = (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert 'data-yt="AMPdemo0001"' in html and "youtube" not in html.lower()   # iframe only on tap (app.js)
    assert 'data-story="videos"' in html      # A was added within the last 7 days
    assert "frame-src https://www.youtube-nocookie.com" in (tmp_path / "dist" / "_headers").read_text(encoding="utf-8")


def test_make_thumb_caps_size(tmp_path):
    from PIL import Image
    import random
    rng = random.Random(1)
    im = Image.new("RGB", (2000, 1500))
    im.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(2000 * 1500)])
    src = tmp_path / "noise.png"
    im.save(src)
    make_thumb(src, tmp_path / "out.jpg")
    assert (tmp_path / "out.jpg").stat().st_size <= 60 * 1024


def test_student_stories_tiles_and_messages(conn, cfg, klass, tmp_path):
    cls, studs = klass
    s = studs[0]
    sid = ops.ensure_session(conn, cls["id"], date(2026, 11, 3))
    ops.set_attitude(conn, s["id"], sid, 3)
    ops.teacher_submit(conn, s["id"], date(2026, 11, 3))
    ops.add_note(conn, s["id"], "2026-W44", "첫 메시지")
    ops.add_note(conn, s["id"], "2026-W45", "둘째 메시지")
    conn.commit()
    cfg.videos_path = str(tmp_path / "none.json")
    assert generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW).ok
    html = (tmp_path / "dist" / "s" / s["student_token"] / "index.html").read_text(encoding="utf-8")
    keys = re.findall(r'class="story" data-story="(\w+)" data-hash="\w+">', html)   # visible rings only
    assert keys == ["streak", "goal", "teacher"]                # no new videos, no moments in this config
    assert re.search(r'data-story="moments" data-hash="\w+" hidden>', html)     # the 장면 ring waits for a live moment
    assert html.index("첫 메시지") < html.index("둘째 메시지")     # newest at the bottom
    tiles = re.findall(r'<li class="tile (\w+)" data-date="([\d-]+)"', html)
    assert tiles[0] == ("open", "2026-11-06")                  # newest first, today open
    st = dict((d, c) for c, d in tiles)
    assert st["2026-11-03"] == "hl" and st["2026-11-04"] == "off" and st["2026-10-30"] == "miss"   # hl = 3 points
    ops.teacher_submit(conn, s["id"], date(2026, 11, 5))
    assert re.search(r'data-date="2026-11-03">.*?♥', html)       # 3 points that day
    assert "<img" not in html.split('id="tiles"')[1].split("</ol>")[0]   # tiles never show photos
    assert "<input" in html and "<textarea" not in html          # only the file input
    for other in studs[1:]:
        assert other["display_name"] not in html


def test_parent_room_keeps_8_weeks_with_earlier_collapsed(conn, cfg, klass, tmp_path):
    cls, studs = klass
    s = studs[0]
    conn.execute("UPDATE classes SET term = '2026-09-01..2026-12-31' WHERE id = ?", (cls["id"],))
    from console import importers
    from pathlib import Path
    importers.import_screeners(conn, Path(__file__).resolve().parent.parent / "demo" / "screeners")
    for w in range(10):
        ops.add_note(conn, s["id"], f"2026-W{36 + w:02d}", f"노트 {36 + w}")
    conn.commit()
    assert generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW).ok
    html = (tmp_path / "dist" / "p" / s["parent_token"] / "index.html").read_text(encoding="utf-8")
    assert html.count('class="sep"') == 8
    assert html.count('<details class="past">') == 7          # earlier weeks: one line each, closed
    assert html.count('<details class="past" open') == 0
    assert html.count("<svg") == 1                             # the trend chart only in the newest week
    assert "노트 45" in html and "노트 38" in html and "노트 37" not in html   # W38..W45
    sums = re.findall(r'<summary class="bubble sum">(.*?)</summary>', html)
    assert len(sums) == 7 and all(re.fullmatch(r"\d+/\d+–\d+/\d+ · 제출 \d+일( · 테스트 (상승|유지|조정))? · 태도 \+\d+", x) for x in sums)
    assert "<input" not in html


def test_lint_brand_words_and_photo_img(tmp_path):
    (tmp_path / "a.html").write_text('<title>AMP 학습 기록</title><p style="color:#FEE500">Kakao</p>', encoding="utf-8")
    (tmp_path / "b.html").write_text('<title>AMP 학습 기록</title><img src="https://w.example/photo?key=photo:2026-11-06:x">'
                                     '<img src="/t/AMPdemo0001.jpg">', encoding="utf-8")
    kinds = [f.kind for f in lint.errors(lint.lint_dir(tmp_path, [], "AMP 학습 기록"))]
    assert kinds.count("brand") == 2 and kinds.count("photo_img") == 1


def test_lint_thumbnail_size(tmp_path):
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "x.jpg").write_bytes(b"\xff" * (61 * 1024))
    assert "image_size" in {f.kind for f in lint.lint_dir(tmp_path, [], "AMP 학습 기록")}


def test_korean_messenger_words_are_allowed(tmp_path):
    (tmp_path / "a.html").write_text("<title>AMP 학습 기록</title><p>답장·문의는 카카오톡으로</p>", encoding="utf-8")
    assert not lint.errors(lint.lint_dir(tmp_path, [], "AMP 학습 기록"))
