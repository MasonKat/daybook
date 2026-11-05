"""video-library: catalog fields (old items stay valid), /v/<key>/ pages without any token, the 영상 tab's 라이브러리."""
import json
import re

from console import generate, videolib as VL
from console.config import ROOT, Config

from conftest import TODAY


def test_catalog_fields_are_validated_and_old_items_stay_valid():
    f, warn = VL.fields({"key": "11-2-2-574", "chapters": [{"t": 90, "label": "풀이"}, {"t": 0, "label": "문제 읽기"}, {"t": "x"}],
                         "book": "워크북", "no": "574", "concept_key": "cv-10-2-014", "related_keys": ["11-2-2-571", "BAD KEY"], "feed": False})
    assert f["key"] == "11-2-2-574" and [c["t"] for c in f["chapters"]] == [0, 90] and f["related_keys"] == ["11-2-2-571"]
    assert f["feed"] is False and len(warn) == 1
    assert VL.fields({"key": "../etc"})[0].get("key") is None
    old = VL.fields({"title": "옛 항목", "url": "https://youtu.be/x"})[0]
    assert old["chapters"] == [] and old["feed"] is True and "key" not in old
    items = [{"key": "a1", "yt_id": "AMPlib11571", "concept_key": "nope"}, {"key": "a1", "yt_id": ""}]
    w = VL.check(items)
    assert any("duplicate" in x for x in w) and any("nope" in x for x in w) and any("no yt_id" in x for x in w)


def test_video_pages_carry_no_token_and_the_library_lists_them(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config(videos_path=str(ROOT / "demo" / "library.demo.json"))
    assert generate.publish(conn, cfg, tmp_path / "d", today=TODAY).ok
    pages = sorted(p.parent.name for p in (tmp_path / "d" / "v").glob("*/index.html"))
    assert len(pages) == 8 and "11-2-2-574" in pages
    tokens = [r[0] for r in conn.execute("SELECT student_token FROM students UNION SELECT parent_token FROM students")]
    for p in (tmp_path / "d" / "v").rglob("*.html"):
        html = p.read_text(encoding="utf-8")
        assert not any(t in html for t in tokens) and "youtube.com/iframe_api" not in html
    page = (tmp_path / "d" / "v" / "11-2-2-574" / "index.html").read_text(encoding="utf-8")
    assert 'href="/v/cv-10-2-014/"' in page and 'href="/v/11-2-2-575/"' in page and page.count('class="vp-ch"') == 4
    tok = conn.execute("SELECT student_token FROM students LIMIT 1").fetchone()[0]
    st = (tmp_path / "d" / "s" / tok / "index.html").read_text(encoding="utf-8")
    lib = st.split('id="lib"')[1].split('<section')[0]
    assert "데모 워크북 · 기본" in lib and 'data-no="574"' in lib and "개념 로드맵" in lib and 'href="/v/cv-10-2-014/"' in lib
    feed = st.split('id="tab-videos"')[1].split('id="lib"')[0]
    assert "AMPlib11574" not in feed                                       # feed: false stays out of the 영상 feed
