"""F22.5: theme presets (AA contrast in light and dark), 밝게/어둡게/자동, 글자 크기, the background slot, attendance-only
unlocks, and the parent page keeping the academy's default look."""
import json
import re

import pytest

from console import generate, prefs as PF, themes as TH
from console.config import ROOT, Config

from conftest import TODAY


@pytest.mark.parametrize("tid", list(TH.THEMES))
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_every_palette_passes_aa_in_both_modes(tid, mode):
    for what, (fg, bg) in TH.pairs(tid, mode).items():
        assert TH.contrast(fg, bg) >= 4.5, (tid, mode, what, round(TH.contrast(fg, bg), 2))


def test_text_over_a_background_picture_stays_readable():
    assert TH.scrim_worst("light") >= 4.5 and TH.scrim_worst("dark") >= 4.5         # the worst pixel (black or white) under it


def test_presets_only_and_the_worker_agrees():
    src = (ROOT / "worker" / "src" / "index.js").read_text(encoding="utf-8")
    assert json.loads(re.search(r"export const THEME_IDS = (\[.*?\]);", src).group(1)) == list(TH.THEMES)
    assert 6 <= len(TH.THEMES) <= 8
    assert PF.validate({"theme": "sea", "mode": "dark", "size": "lg", "bg": "stars"})
    for bad in ({"theme": "hotpink"}, {"theme": "#ff00ff"}, {"mode": "sepia"}, {"size": "xl"}, {"bg": "../../x"}, {"css": "body{}"}):
        with pytest.raises(PF.PrefsError):
            PF.validate(bad)


def test_unlocks_follow_attendance_only_and_stay_open(conn, klass):
    lk = TH.locks({})
    assert TH.earned(6, 0, lk) == set() and TH.earned(7, 0, lk) == {"sunset"} and TH.earned(21, 1, lk) == {"sunset", "night", "blossom"}
    import inspect
    assert list(inspect.signature(TH.earned).parameters) == ["streak", "goal_steps", "lk"]     # no score can enter
    _, studs = klass
    sid = studs[0]["id"]
    assert TH.remember(conn, sid, {"sunset"}, "t1") == {"sunset"}
    assert TH.remember(conn, sid, set(), "t2") == {"sunset"}                               # a broken streak keeps it
    eff = TH.effective({"theme": "night", "mode": "light", "size": "lg"}, {"sunset"}, set(), lk)
    assert eff == {"theme": "", "mode": "light", "size": "lg", "bg": ""}                   # locked: the default look
    assert TH.effective({"theme": "sunset"}, {"sunset"}, set(), lk)["theme"] == "sunset"
    assert TH.effective({"theme": "night"}, set(), set(), lk, taste=True)["theme"] == "night"   # the taste site: all open
    assert TH.effective({"theme": "sea", "bg": "nope"}, set(), {"stars"}, lk) == {"theme": "sea", "mode": "auto", "size": "md", "bg": ""}


def test_css_is_presets_only_with_no_external_fonts_or_images():
    css = TH.css()
    assert "http" not in css and "@import" not in css and "@font-face" not in css and "url(" not in css
    for tid in TH.THEMES:
        assert f'[data-theme="{tid}"]' in css
    assert 'body[data-page="parent"]' not in css


def test_the_student_page_wears_the_theme_and_the_parent_page_does_not(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "brand" / "backgrounds").mkdir(parents=True)
    from PIL import Image
    Image.new("RGB", (400, 200), (30, 60, 120)).save(tmp_path / "brand" / "backgrounds" / "stars.jpg", "JPEG")
    (tmp_path / "brand" / "backgrounds" / "big.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * (130 * 1024))
    cfg = Config(extra={"brand": {"backgrounds": [{"id": "stars", "file": "brand/backgrounds/stars.jpg", "label": "별"},
                                                  {"id": "big", "file": "brand/backgrounds/big.jpg"}]}})
    _, studs = klass
    conn.execute("UPDATE students SET consent = 'yes'")
    PF.save(conn, studs[0]["id"], {"theme": "sea", "mode": "dark", "size": "lg", "bg": "stars"})
    PF.save(conn, studs[1]["id"], {"theme": "night"})                                     # locked (21 days): not applied
    conn.commit()
    assert generate.publish(conn, cfg, tmp_path / "d", today=TODAY).ok
    page = lambda kind, tok: (tmp_path / "d" / kind / tok / "index.html").read_text(encoding="utf-8")   # noqa: E731
    body = re.search(r"<body[^>]*>", page("s", studs[0]["student_token"])).group(0)
    assert 'data-theme="sea"' in body and 'data-mode="dark"' in body and 'data-size="lg"' in body and "url(/b/bg/stars.jpg)" in body
    assert (tmp_path / "d" / "b" / "bg" / "stars.jpg").exists() and not (tmp_path / "d" / "b" / "bg" / "big.jpg").exists()   # > 120 KB
    s1 = page("s", studs[1]["student_token"])
    assert "data-theme" not in re.search(r"<body[^>]*>", s1).group(0)
    data = json.loads(s1.split('id="data">')[1].split("</script>")[0])
    assert {t["id"]: t["lock"] for t in data["themes"]}["night"].startswith("연속 21일") and [b["id"] for b in data["bgs"]] == ["stars"]
    par = page("p", studs[0]["parent_token"])
    assert "themes.css" not in par and "data-theme" not in par and "--bgimg" not in par
    assert (tmp_path / "d" / "static" / "themes.css").read_text(encoding="utf-8") == TH.css()
