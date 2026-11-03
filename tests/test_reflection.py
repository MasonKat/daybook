"""F3: 과제 끝 with an optional one-line review (오늘 한 줄) and chips."""
import json
import re
from datetime import datetime

from click.testing import CliRunner

from console import generate, ops
from console.cli import cli
from console.config import Config
from console.db import KST
from console.pull import apply_items

from conftest import TODAY

NOW = datetime(2026, 11, 6, 22, 0, tzinfo=KST)


def item(tok, line=None, chip=None, day="2026-11-06"):
    it = {"token": tok, "day": day, "ts": f"{day}T11:55:00.000Z", "updated": f"{day}T11:55:00.000Z",
          "key": f"photo:{day}:{tok}", "n": 1, "set": ""}
    if line is not None:
        it["line"] = line
    if chip is not None:
        it["chip"] = chip
    return it


def test_pull_stores_line_and_chip_and_profile_shows_own_line(conn, cfg, klass, tmp_path):
    _, studs = klass
    apply_items(conn, [item(studs[0]["student_token"], "통분이 이제 이해됐어요", "ok"),
                       item(studs[1]["student_token"], "", "hacker")])
    rows = {r["student_id"]: r for r in conn.execute("SELECT * FROM submissions")}
    assert rows[studs[0]["id"]]["reflection"] == "통분이 이제 이해됐어요" and rows[studs[0]["id"]]["chip"] == "ok"
    assert rows[studs[1]["id"]]["chip"] is None                       # unknown chips are dropped
    apply_items(conn, [item(studs[0]["student_token"], "두번째 한 줄입니다", "rewatch")])     # a later upload updates the line
    assert conn.execute("SELECT reflection, chip FROM submissions WHERE student_id = ?", (studs[0]["id"],)).fetchone()[:] == ("두번째 한 줄입니다", "rewatch")
    assert generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW).ok
    s_html = (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<li data-date="2026-11-06">.*?다시 볼 영상 있어요.*?두번째 한 줄입니다', s_html)
    other = (tmp_path / "dist" / "s" / studs[1]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert "두번째 한 줄입니다" not in other
    p_html = (tmp_path / "dist" / "p" / studs[0]["parent_token"] / "index.html").read_text(encoding="utf-8")
    assert "두번째 한 줄입니다" not in p_html and "다시 볼 영상 있어요" not in p_html     # parents do not see it by default


def test_parent_sees_lines_only_when_enabled(conn, klass, tmp_path):
    cls, studs = klass
    ops.ensure_session(conn, cls["id"], TODAY)
    apply_items(conn, [item(studs[0]["student_token"], "오늘은 쉬웠어요", "ok")])
    cfg = Config(worker_url="https://w.example", extra={"parent_shows_reflection": True})
    assert generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW).ok
    p_html = (tmp_path / "dist" / "p" / studs[0]["parent_token"] / "index.html").read_text(encoding="utf-8")
    assert "“오늘은 쉬웠어요”" in p_html


def test_risky_lines_are_withheld_not_blocking_publish(conn, cfg, klass, tmp_path):
    _, studs = klass
    apply_items(conn, [item(studs[0]["student_token"], "윤도담 번호 010-1234-5678", "ok"),
                       item(studs[1]["student_token"], "한가람이랑 같이 풀었어요"),
                       item(studs[2]["student_token"], "Adaptive_Screeners_3S 어려움")])
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings]                      # the teacher's publish is never blocked
    s0 = (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert "010-1234" not in s0 and "이해됐어요" in s0                    # the chip stays, the text is left out
    for st in studs[1:]:
        html = (tmp_path / "dist" / "s" / st["student_token"] / "index.html").read_text(encoding="utf-8")
        assert "한가람이랑" not in html and "Adaptive_" not in html


def test_board_and_console_show_lines(conn, klass, tmp_path, monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from console import board as boardmod
    _, studs = klass
    apply_items(conn, [item(studs[0]["student_token"], "다음엔 검산할게요", "confused")])
    conn.commit()
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    b = boardmod.Board(db_path, Config(), "고1A", TODAY, poll=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), boardmod.make_handler(b))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        state = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/state").read())
        assert state["students"][str(studs[0]["id"])]["line"] == "아직 헷갈려요 · 다음엔 검산할게요"
    finally:
        httpd.shutdown()
    monkeypatch.setenv("DAYBOOK_CONFIG", str(tmp_path / "none.json"))
    r = CliRunner().invoke(cli, ["--db", db_path, "lines", "고1A", "--date", "2026-11-06"])
    assert r.exit_code == 0 and "가람" in r.output and "다음엔 검산할게요" in r.output
