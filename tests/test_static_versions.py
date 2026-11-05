"""Launch fix (11-05): a page built after a static change never references a stale /static/ URL (the iPhone's old service
worker served the old app.js to the new page, so ⚙ 설정 had no handler)."""
import hashlib
import re
import shutil

from console import generate
from console.config import ROOT, Config

from conftest import TODAY

REF = re.compile(r'(?:src|href)="(/static/[^"]+)"')


def _refs(out):
    return {u for p in out.rglob("*.html") for u in REF.findall(p.read_text(encoding="utf-8"))}


def _check(out):
    refs = _refs(out)
    assert refs and any(u.startswith("/static/app.js?v=") for u in refs) and any(u.startswith("/static/themes.css?v=") for u in refs)
    for u in refs:
        path, _, v = u.partition("?v=")
        assert v, f"unversioned {u}"
        assert v == hashlib.sha256((out / path.lstrip("/")).read_bytes()).hexdigest()[:8], f"stale {u}"
    sw = (out / "sw.js").read_text(encoding="utf-8")
    pre = re.search(r"var STATIC = (\[[^\]]*\]);", sw).group(1)
    assert "/static/app.js?v=" in pre and "/static/app.js?v=" + refs_v(refs, "/static/app.js") in pre
    return refs


def refs_v(refs, path):
    return next(u.split("?v=")[1] for u in refs if u.startswith(path + "?v="))


def test_a_static_change_is_a_new_url_everywhere(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conn.execute("UPDATE students SET consent = 'yes'")
    conn.commit()
    assert generate.publish(conn, Config(), tmp_path / "a", today=TODAY).ok
    before = _check(tmp_path / "a")
    st = tmp_path / "static"                                        # the next build ships a changed app.js
    shutil.copytree(ROOT / "static", st)
    (st / "app.js").write_text((st / "app.js").read_text(encoding="utf-8") + "\n/* changed */\n", encoding="utf-8")
    monkeypatch.setattr(generate, "STATIC", st)
    assert generate.publish(conn, Config(), tmp_path / "b", today=TODAY).ok
    after = _check(tmp_path / "b")
    assert refs_v(before, "/static/app.js") != refs_v(after, "/static/app.js")
    assert refs_v(before, "/static/app.css") == refs_v(after, "/static/app.css")      # unchanged files keep their URL
    sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    assert "caches.match(req)" in sw and "ignoreSearch: true }).then(function (hit) { return hit ||" in sw   # full-URL cache; /s/ network first
    assert "/sw.js\n  Cache-Control: no-cache" in generate.headers_file(Config())
