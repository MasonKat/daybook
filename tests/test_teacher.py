"""F6: the Teacher tab. One test per action, through the board's HTTP layer (same functions as the CLI)."""
import base64
import json
import subprocess
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from console import board as boardmod, pull as pullmod
from console.config import Config

from conftest import TODAY

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def tserver(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = []
    monkeypatch.setattr(pullmod, "http_put_json", lambda cfg, path, payload: sent.append((path, payload)) or {"n": len(payload.get("tokmap", {}))})
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    cfg = Config(worker_url="https://w.example", pages_url="https://daybook.pages.dev", teacher_token="t",
                 moments_dir=str(tmp_path / "moments"))
    b = boardmod.Board(db_path, cfg, "고1A", TODAY, poll=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), boardmod.make_handler(b))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", b, sent
    httpd.shutdown()


def call(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})},
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req) as r:
            ct = r.headers.get("Content-Type", "")
            raw = r.read()
            return r.status, (json.loads(raw) if "json" in ct else raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode("utf-8", "replace")


def b64(data: bytes) -> str:
    return "data:application/octet-stream;base64," + base64.b64encode(data).decode()


def test_page_and_guards(tserver):
    url, _, _ = tserver
    code, html = call(url + "/teacher")
    assert code == 200 and "명단" in html and "/static/teacher.js" in html
    assert call(url + "/static/teacher.js")[0] == 200
    # DNS-rebinding guard: another Host is refused, also for reads
    assert call(url + "/api/teacher/roster", headers={"Host": "evil.example"})[0] == 403
    # cross-origin writes are refused
    assert call(url + "/api/teacher/publish", {}, headers={"Origin": "https://evil.example"})[0] == 403


def test_roster_list_and_edit(tserver, klass, conn):
    url, _, sent = tserver
    _, studs = klass
    code, j = call(url + "/api/teacher/roster")
    assert code == 200 and len(j["students"]) == 3
    s0 = j["students"][0]
    assert s0["student_link"] == f"https://daybook.pages.dev/s/{studs[0]['student_token']}/"
    assert s0["parent_link"].endswith(f"/p/{studs[0]['parent_token']}/")
    # F11: full names are shown in this local tab (never on a page; test_members checks dist/)
    assert s0["full_name"] == studs[0]["full_name"] and s0["class_name"] == "고1A"
    n0 = len(sent)                                                       # the board's own /session at start
    code, j = call(url + "/api/teacher/roster", {"slug": studs[0]["slug"], "display_name": "가람이"})
    assert code == 200 and j["changed"] == ["display_name"] and len(sent) == n0      # a name change sends nothing
    code, j = call(url + "/api/teacher/roster", {"slug": studs[1]["slug"], "active": False})
    assert j["changed"] == ["active"] and sent and sent[-1][0] == "/tokmap" and len(sent[-1][1]["tokmap"]) == 2
    assert conn.execute("SELECT display_name FROM students WHERE id = ?", (studs[0]["id"],)).fetchone()[0] == "가람이"
    assert call(url + "/api/teacher/roster", {"slug": "nobody", "active": True})[0] == 404
    assert call(url + "/api/teacher/roster", {"slug": studs[0]["slug"], "display_name": " "})[0] == 400


def test_notes_prefilled_and_saved(tserver, klass, conn):
    url, _, _ = tserver
    _, studs = klass
    code, j = call(url + "/api/teacher/notes")
    assert code == 200 and j["week"] == "2026-W45" and len(j["students"]) == 3
    assert all(s["note"] == "" and s["suggestion"].endswith("요.") and s["warnings"] == [] for s in j["students"])   # F16.3
    code, r = call(url + "/api/teacher/note", {"slug": studs[0]["slug"], "week": j["week"], "text": "잘했어요"})
    assert code == 200 and r["ok"]
    _, j = call(url + "/api/teacher/notes")
    assert j["students"][0]["note"] == "잘했어요" and j["students"][0]["suggestion"]
    assert call(url + "/api/teacher/note", {"slug": studs[0]["slug"], "week": "W45", "text": "x"})[0] == 400


def test_import_screeners_and_tests_csv(tserver, conn):
    url, _, _ = tserver
    files = [{"name": p.name, "data": b64(p.read_bytes())} for p in sorted((ROOT / "demo" / "screeners").glob("round_*.json"))]
    code, j = call(url + "/api/teacher/import", {"kind": "screeners", "files": files})
    assert code == 200 and j["files"] == 3 and j["imported"] == 9 and any("S999" in u for u in j["unmatched"])
    csv = (ROOT / "mappings" / "tests.example.csv").read_bytes()
    code, j = call(url + "/api/teacher/import", {"kind": "tests", "files": [{"name": "t.csv", "data": b64(csv)}]})
    assert code == 200 and j["imported"] == 1
    assert call(url + "/api/teacher/import", {"kind": "tests", "files": []})[0] == 400
    assert call(url + "/api/teacher/import", {"kind": "zip", "files": [{"name": "a", "data": b64(b"x")}]})[0] == 400


def test_moments_and_cancel(tserver, conn, tmp_path):
    from PIL import Image
    import io
    url, _, _ = tserver
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "#123").save(buf, "JPEG")
    img = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    assert call(url + "/api/moment", {"image": img, "text": "칠판", "confirm": False})[0] == 400
    code, j = call(url + "/api/moment", {"image": img, "text": "칠판", "confirm": True})
    assert code == 200 and j["moments"] == 1
    code, j = call(url + "/api/teacher/moments")
    assert j["moments"][0]["text"] == "칠판" and j["cancelled"] == []
    code, j = call(url + "/api/teacher/cancel", {"date": "2026-11-13", "reason": "휴강"})
    assert code == 200 and j["cancelled"] == [{"date": "2026-11-13", "reason": "휴강"}]
    code, j = call(url + "/api/teacher/cancel", {"date": "2026-11-13", "undo": True})
    assert j["cancelled"] == []
    assert call(url + "/api/teacher/cancel", {"date": "13 Nov"})[0] == 400


def test_publish_runs_pull_publish_lint(tserver, klass, conn, tmp_path, monkeypatch):
    url, _, sent = tserver
    _, studs = klass
    monkeypatch.setattr(pullmod, "http_fetch", lambda cfg: (lambda path: json.dumps({"now": "2026-11-06T13:00:00.000Z", "items": []}).encode()))
    import console.teacher as T
    monkeypatch.setattr(T, "http_fetch", pullmod.http_fetch)
    code, j = call(url + "/api/teacher/publish", {})
    assert code == 200 and j["ok"], j
    assert j["steps"][0].startswith("pull: new 0") and "lint passed" in j["steps"][1]
    assert (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").exists()
    assert [x[0] for x in sent][-2:] == ["/tokmap", "/reviews-sync"]        # F18.3: reviews follow the links
    assert not (tmp_path / "dist" / "static" / "teacher.js").exists()       # the Teacher tab never goes online
    conn.execute("UPDATE students SET display_name = full_name WHERE id = ?", (studs[0]["id"],))
    conn.commit()
    code, j = call(url + "/api/teacher/publish", {})
    assert not j["ok"] and "STOPPED" in j["steps"][-1] and any("full_name" in f for f in j["findings"])


def test_deploy_runs_wrangler_and_shows_output(tserver):
    url, b, _ = tserver
    seen = []
    b.deploy_runner = lambda cmd: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "Deployment complete! https://x.daybook.pages.dev\n", "")
    code, j = call(url + "/api/teacher/deploy", {})
    assert code == 200 and j["ok"] and "Deployment complete" in j["output"]
    assert seen[0][1:] == ["wrangler", "pages", "deploy", str(Path("..") / "dist"), "--project-name", "daybook", "--branch", "main"]
    b.deploy_runner = lambda cmd: subprocess.CompletedProcess(cmd, 1, "", "Not logged in")
    assert call(url + "/api/teacher/deploy", {})[1]["ok"] is False


def test_weekly_messages(tserver, klass):
    url, _, _ = tserver
    code, j = call(url + "/api/teacher/weekly")
    assert code == 200 and len(j["messages"]) == 3 and all(m.startswith("[AMP 주간 기록]") for m in j["messages"])


def test_rounds_add_progress_export_close(tserver, conn, klass, tmp_path):
    """F14: the Teacher tab's 주간 테스트 section, through the HTTP layer (same functions as `daybook round`)."""
    url, b, sent = tserver
    _, studs = klass
    body = {"action": "add", "id": "Adaptive_Screeners_1S_Nov06_G10", "grade": "1학년", "n": 20, "classes": ["고1A"],
            "open_at": "2026-11-06 20:00", "close_at": "2026-11-08 23:59", "label": ""}
    code, j = call(url + "/api/teacher/round", body)
    assert code == 200 and j["ok"] and sent[-1][0] == "/rounds" and sent[-1][1]["rounds"][0]["n"] == 20
    assert "Adaptive" not in json.dumps(sent[-1][1])
    assert call(url + "/api/teacher/round", body)[0] == 400                       # exists
    code, j = call(url + "/api/teacher/rounds")
    assert j["rounds"][0]["label"] == "11/6 주간 테스트" and (j["rounds"][0]["done"], j["rounds"][0]["total"]) == (0, 3)
    from console import rounds as R
    R.apply_reports(conn, [{"rid": R.rid(body["id"]), "token": studs[0]["student_token"], "incorrect": "1", "missed": "",
                            "hardest": "", "reasons": "시간부족", "wishlist": "", "metaphor": "", "ts": "2026-11-06T12:00:00.000Z",
                            "updated": "2026-11-06T12:00:00.000Z"}])
    conn.commit()
    code, p = call(url + "/api/teacher/round?id=" + body["id"])
    assert p["done"] == 1 and [x["done"] for x in p["students"]] == [True, False, False] and "incorrect" not in json.dumps(p)
    code, j = call(url + "/api/teacher/round", {"action": "export", "id": body["id"]})
    assert j["ok"] and (tmp_path / "reports" / (body["id"] + ".csv")).exists()
    code, j = call(url + "/api/teacher/round", {"action": "edit", "id": body["id"], "n": 22})
    assert j["ok"] and sent[-1][1]["rounds"][0]["n"] == 22
    code, j = call(url + "/api/teacher/round", {"action": "close", "id": body["id"]})
    assert j["ok"]
    assert call(url + "/api/teacher/round", {"action": "add", "id": "R2", "n": 99, "classes": ["고1A"],
                                             "open_at": "2026-11-06 20:00", "close_at": "2026-11-08 23:59"})[0] == 400
