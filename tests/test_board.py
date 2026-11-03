"""Board (P2): attitude buttons write attitude + events at once and move the class bar."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from console import board as boardmod
from console import models as M
from console.config import Config

from conftest import TODAY


@pytest.fixture
def server(conn, klass, tmp_path):
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    b = boardmod.Board(db_path, Config(), "고1A", TODAY, poll=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), boardmod.make_handler(b))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", b
    httpd.shutdown()


def post(url, body, origin=None):
    headers = {"Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_board_page_and_attitude(server, conn, klass):
    url, b = server
    cls, studs = klass
    html = urllib.request.urlopen(url + "/").read().decode()
    assert "가람" in html and "대신 제출" in html and "오늘 3점" in html
    assert "누적" not in html  # no individual cumulative on the board
    code, st = post(url + "/api/attitude", {"student_id": studs[0]["id"], "points": 3})
    assert code == 200 and st["students"][str(studs[0]["id"])]["points"] == 3
    assert st["three"] == "가람"
    assert M.class_total(conn, cls["id"]) == 3
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='attitude'").fetchone()[0] == 1
    pct_before = st["pct"]
    code, st = post(url + "/api/attitude", {"student_id": studs[1]["id"], "points": 2})
    assert st["pct"] > pct_before


def test_board_submit_for_and_guards(server, conn, klass):
    url, _ = server
    _, studs = klass
    code, st = post(url + "/api/submit-for", {"student_id": studs[1]["id"]})
    assert code == 200 and st["students"][str(studs[1]["id"])]["done"]
    assert conn.execute("SELECT source FROM submissions").fetchone()[0] == "teacher"
    assert post(url + "/api/attitude", {"student_id": studs[0]["id"], "points": 2}, origin="https://evil.example")[0] == 403
    assert post(url + "/api/attitude", {"student_id": 999, "points": 2})[0] == 404
    assert post(url + "/api/attitude", {"student_id": studs[0]["id"], "points": 9})[0] == 400
    assert urllib.request.urlopen(url + "/static/app.css").status == 200


def test_board_code_and_checkin_for(server, conn, klass):
    url, b = server
    _, studs = klass
    html = urllib.request.urlopen(url + "/").read().decode("utf-8")
    code = conn.execute("SELECT code FROM sessions WHERE id = ?", (b.session_id,)).fetchone()[0]
    assert code and f'id="b-code">{code}<' in html and "출석 처리" in html
    code_, st = post(url + "/api/checkin-for", {"student_id": studs[2]["id"]})
    assert code_ == 200 and st["students"][str(studs[2]["id"])]["ck"]
    assert st["arrivals"][0]["initial"] == studs[2]["display_name"][:1]
    row = conn.execute("SELECT source FROM checkins WHERE student_id = ?", (studs[2]["id"],)).fetchone()
    assert row["source"] == "teacher"
    post(url + "/api/checkin-for", {"student_id": studs[2]["id"]})          # idempotent
    assert conn.execute("SELECT COUNT(*) FROM checkins").fetchone()[0] == 1


def _raw(url, head, body):
    """Send one raw request and read the whole reply (a reset would raise ConnectionResetError here)."""
    import socket
    host, port = url.split("//")[1].split(":")
    with socket.create_connection((host, int(port)), timeout=10) as sk:
        sk.sendall(head.encode() + body)
        sk.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := sk.recv(65536):
            data += chunk
    return int(data.split(b" ", 2)[1]), data


@pytest.mark.parametrize("case", ["origin", "host", "too_big", "bad_length"])
def test_every_refusal_reads_the_body_first(server, case):
    """Windows resets a connection answered with unread request bytes (WinError 10054): refusals drain first."""
    url, _ = server
    port = url.rsplit(":", 1)[1]
    body = json.dumps({"student_id": 1, "points": 2, "pad": "x" * 4_000_000}).encode()
    host = "evil.example" if case == "host" else f"127.0.0.1:{port}"
    origin = "Origin: https://evil.example\r\n" if case == "origin" else ""
    length = {"too_big": str(26 * 1024 * 1024), "bad_length": "12abc"}.get(case, str(len(body)))
    if case == "too_big":
        body = b"x" * (26 * 1024 * 1024)
    if case == "bad_length":           # no usable length = nothing can be drained safely; real clients never do this
        body = b""
    head = f"POST /api/attitude HTTP/1.1\r\nHost: {host}\r\n{origin}Content-Type: application/json\r\nContent-Length: {length}\r\n\r\n"
    code, data = _raw(url, head, body)
    assert code == {"origin": 403, "host": 403, "too_big": 413, "bad_length": 400}[case]
    assert data.rstrip().endswith(b"}")                    # a complete JSON answer, not a cut connection
