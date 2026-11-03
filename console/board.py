"""Classroom board: a tiny stdlib HTTP server on 127.0.0.1 (BUILD_BRIEF §5, §7).

GET  /              board page      GET  /api/state   live state (JSON)
POST /api/attitude  {student_id, points}  -> attitude + events at once
POST /api/submit-for {student_id}         -> teacher submission for today
A background thread pulls the Worker every `board_poll_seconds` when it is configured.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import db as dbmod
from . import generate, models as M, ops
from .config import Config, strings
from .pull import PullError, http_fetch, pull


class Board:
    def __init__(self, db_path: str, cfg: Config, class_name: str, day: date, poll: bool):
        self.db_path, self.cfg, self.class_name, self.day = db_path, cfg, class_name, day
        self.poll = poll and bool(cfg.worker and cfg.teacher_token)
        self.lock = threading.Lock()
        self.S = strings()
        with self.conn() as conn:
            dbmod.migrate(conn)
            cls = ops.get_class(conn, class_name)
            self.class_id = cls["id"]
            sess = ops.start_session(conn, cls, day)          # keeps an existing code / start time
            self.session_id = sess["id"]
            conn.commit()
            self.sent = False
            if cfg.worker and cfg.teacher_token:
                from .pull import http_put_json
                try:
                    http_put_json(cfg, "/session", ops.session_payload(conn, sess))
                    self.sent = True
                except PullError as e:
                    print(f"board: 출석 code not sent to the Worker ({e})")

    def conn(self) -> sqlite3.Connection:
        return dbmod.connect(self.db_path)

    def state(self, conn: sqlite3.Connection) -> dict:
        cls = conn.execute("SELECT * FROM classes WHERE id = ?", (self.class_id,)).fetchone()
        sess = conn.execute("SELECT * FROM sessions WHERE id = ?", (self.session_id,)).fetchone()
        rows = conn.execute(
            """SELECT s.id, s.display_name, sub.ts, a.points, c.ts AS ck FROM students_public s
               LEFT JOIN submissions sub ON sub.student_id = s.id AND sub.day = ? AND sub.status != 'deleted'
               LEFT JOIN attitude a ON a.student_id = s.id AND a.session_id = ?
               LEFT JOIN checkins c ON c.student_id = s.id AND c.session_id = ?
               WHERE s.class_id = ? AND s.active = 1 ORDER BY s.id""",
            (self.day.isoformat(), self.session_id, self.session_id, self.class_id)).fetchall()
        reward = M.reward_for(conn, cls, self.day)
        three = [r["display_name"] for r in rows if r["points"] == 3]
        hm = lambda ts: M.utc_iso_to_kst(ts).strftime("%H:%M") if ts else ""   # noqa: E731
        students = [{"id": r["id"], "display_name": r["display_name"], "done": r["ts"] is not None, "time": hm(r["ts"]),
                     "points": r["points"], "ck": hm(r["ck"])} for r in rows]
        arrivals = [{"initial": r["display_name"][:1], "time": hm(r["ck"])}
                    for r in sorted((r for r in rows if r["ck"]), key=lambda r: r["ck"])]
        return {"cls": cls, "students": students, "reward": reward, "three": three, "arrivals": arrivals,
                "code": sess["code"] or "", "start": sess["start_time"] or ""}

    def state_json(self, conn) -> dict:
        st = self.state(conn)
        r = st["reward"]
        return {
            "students": {str(s["id"]): {"done": s["done"], "time": s["time"], "points": s["points"], "ck": s["ck"]}
                         for s in st["students"]},
            "pct": r.pct,
            "togo": self.S["reward_to_go"].format(label=r.label, n=r.to_go),
            "three": ", ".join(st["three"]) if st["three"] else self.S["board_none"],
            "arrivals": st["arrivals"],
        }

    def page(self) -> str:
        with self.conn() as conn:
            st = self.state(conn)
        tpl = generate.env().get_template("board.html")
        return tpl.render(S=self.S, page="board", cls=st["cls"], students=st["students"], reward=st["reward"],
                          three=st["three"], poll=self.poll, code=st["code"], start=st["start"], arrivals=st["arrivals"], poll_seconds=self.cfg.board_poll_seconds,
                          js=generate.json_script({"refresh": max(2, min(self.cfg.board_poll_seconds, 10))}))

    def poll_loop(self, stop: threading.Event) -> None:
        fetch = http_fetch(self.cfg)
        while not stop.wait(self.cfg.board_poll_seconds):
            try:
                with self.lock, self.conn() as conn:
                    pull(conn, fetch)
            except PullError as e:
                print(f"board: pull failed: {e}")


def make_handler(board: Board):
    static = generate.STATIC

    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                self._send(200, board.page().encode(), "text/html; charset=utf-8")
            elif path == "/api/state":
                with board.conn() as conn:
                    self._json(board.state_json(conn))
            elif path.startswith("/static/") and "/" not in path[len("/static/"):]:
                f = static / path[len("/static/"):]
                if f.is_file():
                    ctype = {".css": "text/css", ".js": "text/javascript", ".png": "image/png"}.get(f.suffix, "application/octet-stream")
                    self._send(200, f.read_bytes(), ctype)
                else:
                    self._send(404, b"not found", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            # only same-origin requests from the board page (a page elsewhere must not write scores)
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self._json({"ok": False, "error": "origin"}, 403)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                sid = int(body["student_id"])
            except (ValueError, KeyError, json.JSONDecodeError):
                return self._json({"ok": False, "error": "bad_request"}, 400)
            with board.lock, board.conn() as conn:
                ok = conn.execute("SELECT 1 FROM students WHERE id = ? AND class_id = ? AND active = 1",
                                  (sid, board.class_id)).fetchone()
                if not ok:
                    return self._json({"ok": False, "error": "unknown_student"}, 404)
                try:
                    if self.path == "/api/attitude":
                        ops.set_attitude(conn, sid, board.session_id, int(body.get("points")), said_aloud=True)
                    elif self.path == "/api/submit-for":
                        ops.teacher_submit(conn, sid, board.day, "teacher")
                    elif self.path == "/api/checkin-for":
                        ops.record_checkin(conn, sid, board.session_id, dbmod.now_utc_iso(), "teacher")
                    else:
                        return self._json({"ok": False, "error": "not_found"}, 404)
                except (ops.OpError, TypeError, ValueError) as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                conn.commit()
                self._json(board.state_json(conn))

    return H


def serve(db_path: str, cfg: Config, class_name: str, port: int = 8787, poll: bool = True,
          day: date | None = None, host: str = "127.0.0.1") -> None:
    board = Board(db_path, cfg, class_name, day or M.kst_today(), poll)
    httpd = ThreadingHTTPServer((host, port), make_handler(board))
    stop = threading.Event()
    if board.poll:
        threading.Thread(target=board.poll_loop, args=(stop,), daemon=True).start()
    print(f"board for {class_name} on http://{host}:{port}/  (poll {'on' if board.poll else 'off'}; Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()


