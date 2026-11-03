"""Classroom board: a tiny stdlib HTTP server on 127.0.0.1 (BUILD_BRIEF §5, §7).

GET  /              board page      GET  /api/state   live state (JSON)
POST /api/attitude  {student_id, points}  -> attitude + events at once
POST /api/submit-for {student_id}         -> teacher submission for today
GET  /teacher       the Teacher tab (F6): roster, notes, imports, moments, 휴강, publish, deploy, weekly messages
GET|POST /api/teacher/...  (see console/teacher.py; the same functions as the CLI)
A background thread pulls the Worker every `board_poll_seconds` when it is configured.
Only requests whose Host is 127.0.0.1/localhost on this port are served (no DNS-rebinding reads of the console).
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

    reactions_text = ""
    polling = False

    def refresh_reactions(self, fetch) -> None:
        """Totals of this class's moments (anonymous counters) for the board, e.g. "11/3 🔥 9 👏 3"."""
        try:
            items = json.loads(fetch(f"/moments?class={ops.class_key(self.class_id)}")).get("items", [])
        except (PullError, ValueError):
            return
        parts = []
        for m in items[-3:]:
            c = m.get("counts") or {}
            d = m.get("date", "")
            tot = " ".join(f"{self.S['react_' + r]} {c.get(r, 0)}" for r in ("fire", "clap", "mind") if c.get(r))
            if tot:
                parts.append(f"{int(d[5:7])}/{int(d[8:10])} {tot}")
        self.reactions_text = " · ".join(parts)

    def add_moment(self, conn, body: dict) -> str:
        import base64
        from . import moments as mm
        from .pull import http_put_json
        push = None
        if self.cfg.worker and self.cfg.teacher_token:
            push = lambda payload: http_put_json(self.cfg, "/moment-put", payload)   # noqa: E731
        raw = str(body.get("image") or "")
        raw = raw.split(",", 1)[1] if raw.startswith("data:") else raw
        try:
            data = base64.b64decode(raw, validate=True)
        except ValueError as e:
            raise mm.MomentError("not an image") from e
        cls = conn.execute("SELECT * FROM classes WHERE id = ?", (self.class_id,)).fetchone()
        try:
            mm.add_moment(conn, self.cfg, cls, self.day, data, str(body.get("text") or ""),
                          confirmed=body.get("confirm") is True, push=push)
        except PullError:
            return self.S["board_moment_ok"] + " (Worker 미연결: 토요일 발행 후 보여요)"
        return self.S["board_moment_ok"]

    def state(self, conn: sqlite3.Connection) -> dict:
        cls = conn.execute("SELECT * FROM classes WHERE id = ?", (self.class_id,)).fetchone()
        sess = conn.execute("SELECT * FROM sessions WHERE id = ?", (self.session_id,)).fetchone()
        rows = conn.execute(
            """SELECT s.id, s.display_name, sub.ts, sub.reflection, sub.chip, a.points, c.ts AS ck FROM students_public s
               LEFT JOIN submissions sub ON sub.student_id = s.id AND sub.day = ? AND sub.status != 'deleted'
               LEFT JOIN attitude a ON a.student_id = s.id AND a.session_id = ?
               LEFT JOIN checkins c ON c.student_id = s.id AND c.session_id = ?
               WHERE s.class_id = ? AND s.active = 1 ORDER BY s.id""",
            (self.day.isoformat(), self.session_id, self.session_id, self.class_id)).fetchall()
        reward = M.reward_for(conn, cls, self.day)
        three = [r["display_name"] for r in rows if r["points"] == 3]
        hm = lambda ts: M.utc_iso_to_kst(ts).strftime("%H:%M") if ts else ""   # noqa: E731
        students = [{"id": r["id"], "display_name": r["display_name"], "done": r["ts"] is not None, "time": hm(r["ts"]),
                     "points": r["points"], "ck": hm(r["ck"]),
                     "line": " · ".join(x for x in ((self.S.get("chip_" + (r["chip"] or ""), "") if r["chip"] else ""),
                                                    r["reflection"] or "") if x)} for r in rows]
        arrivals = [{"initial": r["display_name"][:1], "time": hm(r["ck"])}
                    for r in sorted((r for r in rows if r["ck"]), key=lambda r: r["ck"])]
        return {"cls": cls, "students": students, "reward": reward, "three": three, "arrivals": arrivals,
                "code": sess["code"] or "", "start": sess["start_time"] or ""}

    def state_json(self, conn) -> dict:
        st = self.state(conn)
        r = st["reward"]
        return {
            "students": {str(s["id"]): {"done": s["done"], "time": s["time"], "points": s["points"], "ck": s["ck"],
                                        "line": s["line"]}
                         for s in st["students"]},
            "pct": r.pct,
            "togo": self.S["reward_to_go"].format(label=r.label, n=r.to_go),
            "three": ", ".join(st["three"]) if st["three"] else self.S["board_none"],
            "arrivals": st["arrivals"],
            "reactions": self.reactions_text,
            "moments": conn.execute("SELECT COUNT(*) FROM moments WHERE session_id = ?", (self.session_id,)).fetchone()[0],
        }

    deploy_runner = None   # tests replace the wrangler call
    live: dict | None = None   # F7: the open AMP Live room (code, set, local question text)

    def live_page(self) -> str:
        with self.conn() as conn:
            cls = conn.execute("SELECT * FROM classes WHERE id = ?", (self.class_id,)).fetchone()
        return generate.env().get_template("live.html").render(S=self.S, page="live", cls=cls, worker=bool(self.cfg.worker))

    def live_view(self, conn, st: dict, bonus: int = 0) -> dict:
        """Worker counts + the local question (text and choices never leave the laptop)."""
        from . import live as L
        lv = self.live or {}
        q = int(st.get("q") or 0)
        qs = lv.get("questions") or []
        size = int(st.get("size") or lv.get("size") or 0)
        out = {"ok": st.get("ok", True), "open": bool(st.get("open", bool(lv))), "room": lv.get("room"), "title": lv.get("title"),
               "q": q, "n": len(qs), "accepting": st.get("accepting"), "revealed": st.get("revealed"),
               "joined": st.get("joined", 0), "size": size, "answered": st.get("answered", 0), "correct": st.get("correct"),
               "needed": L.needed(size), "bonus": bonus, "class_total": M.class_total(conn, self.class_id)}
        if q and q <= len(qs):
            cur = qs[q - 1]
            out["question"] = {"text": cur["q"], "kind": cur["kind"], "choices": cur["choices"],
                               "answer": cur["answer"] if st.get("revealed") else None}
        return out

    def live_call(self, op: str, body: dict | None = None) -> dict:
        from . import live as L
        from .pull import http_post_json
        if not (self.cfg.worker and self.cfg.teacher_token):
            raise L.LiveError("AMP Live needs the Worker (worker_url and teacher_token in daybook.json)")
        with self.conn() as conn:
            if op == "open":
                data = L.find_set(self.cfg, str((body or {}).get("set", "")))
                r = http_post_json(self.cfg, "/live/open", L.worker_payload(self.class_id, data["questions"]))
                if not r.get("ok"):
                    raise L.LiveError(r.get("error") or "could not open a room")
                self.live = {"room": r["room"], "title": data["title"], "questions": data["questions"], "size": r.get("size", 0)}
                return self.live_view(conn, {"open": True, "q": 0, "size": r.get("size", 0)})
            if not self.live:
                raise L.LiveError("no open room")
            room = self.live["room"]
            if op == "state":
                st = json.loads(http_fetch(self.cfg)(f"/live/state?room={room}"))
            elif op in ("next", "reveal"):
                st = http_post_json(self.cfg, f"/live/{op}", {"room": room, **({"q": body.get("q")} if body and body.get("q") else {})})
            elif op == "close":
                http_post_json(self.cfg, "/live/close", {"room": room})
                self.live = None
                return {"ok": True, "open": False, "class_total": M.class_total(conn, self.class_id)}
            elif op == "moment":
                st = json.loads(http_fetch(self.cfg)(f"/live/state?room={room}"))
                if not st.get("revealed") or st.get("correct") is None:
                    raise L.LiveError("reveal the answer first")
                rate = round(100 * st["correct"] / max(1, int(st.get("size") or 1)))
                from . import moments as mm
                from .pull import http_put_json
                cls = conn.execute("SELECT * FROM classes WHERE id = ?", (self.class_id,)).fetchone()
                # a generated card (numbers only): no face, name or book text, so the gate is answered by construction
                mm.add_moment(conn, self.cfg, cls, self.day, L.moment_card(self.live["title"], st["q"], rate),
                              f"AMP Live · {st['q']}번 · 반 정답률 {rate}%", confirmed=True,
                              push=lambda payload: http_put_json(self.cfg, "/moment-put", payload))
            else:
                raise L.LiveError("unknown action")
            bonus = L.maybe_bonus(conn, self.cfg, self.class_id, self.session_id, self.day, room, st)
            return self.live_view(conn, st, bonus)

    def teacher_page(self) -> str:
        with self.conn() as conn:
            cls = conn.execute("SELECT * FROM classes WHERE id = ?", (self.class_id,)).fetchone()
        tpl = generate.env().get_template("teacher.html")
        return tpl.render(S=self.S, page="teacher", cls=cls, day=self.day.isoformat(), week=M.iso_week(self.day),
                          pages=bool(self.cfg.pages), worker=bool(self.cfg.worker))

    def page(self) -> str:
        with self.conn() as conn:
            st = self.state(conn)
        tpl = generate.env().get_template("board.html")
        return tpl.render(S=self.S, page="board", cls=st["cls"], students=st["students"], reward=st["reward"],
                          three=st["three"], poll=self.poll, code=st["code"], start=st["start"], arrivals=st["arrivals"],
                          reactions=self.reactions_text, poll_seconds=self.cfg.board_poll_seconds,
                          js=generate.json_script({"refresh": max(2, min(self.cfg.board_poll_seconds, 10)),
                                                   "need": self.S["board_moment_need"], "rxLabel": self.S["board_reactions"]}))

    def poll_loop(self, stop: threading.Event) -> None:
        fetch = http_fetch(self.cfg)
        with self.conn() as conn:
            start = conn.execute("SELECT start_time FROM sessions WHERE id = ?", (self.session_id,)).fetchone()[0]
            until = conn.execute("SELECT COALESCE(self_study_until, end_time) FROM classes WHERE id = ?",
                                 (self.class_id,)).fetchone()[0]
        window = ops.board_window(self.day, start, until)
        first = True
        while first or not stop.wait(self.cfg.board_poll_seconds):
            first = False
            now = M.kst_now()
            if window and not (window[0] <= now <= window[1]):
                self.polling = False          # H1: outside class time the board does not touch the Worker
                continue
            self.polling = True
            try:
                with self.lock, self.conn() as conn:
                    pull(conn, fetch)
                self.refresh_reactions(fetch)
            except PullError as e:
                print(f"board: pull failed: {e}")


MAX_POST = 25 * 1024 * 1024        # a moment photo as a data URL fits easily
DRAIN_CAP = 64 * 1024 * 1024       # beyond this a client is not a browser on this laptop: stop reading, close


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

        def _host_ok(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _teacher_get(self, path: str, query: str):
            from urllib.parse import parse_qs
            from . import teacher as T
            q = parse_qs(query)
            with board.conn() as conn:
                cls = conn.execute("SELECT * FROM classes WHERE id = ?", (board.class_id,)).fetchone()
                day = date.fromisoformat(q["date"][0]) if q.get("date") else board.day
                if path == "/api/teacher/roster":
                    return self._json({"students": T.roster(conn, board.cfg, board.class_id)})
                if path == "/api/teacher/notes":
                    return self._json(T.notes(conn, board.cfg, cls, day))
                if path == "/api/teacher/moments":
                    return self._json({"moments": T.moments(conn, board.class_id),
                                       "cancelled": [{"date": d.isoformat(), "reason": r}
                                                     for d, r in sorted(M.cancelled_days(conn, board.class_id).items())]})
                if path == "/api/teacher/weekly":
                    return self._json({"messages": T.weekly(conn, board.cfg, cls, day)})
                if path == "/api/teacher/contacts":           # F13 (all classes: the teacher's whole desk)
                    return self._json(T.contacts(conn, board.cfg, cls, day))
                if path == "/api/teacher/flags":              # F13: the board's "선생님 보기" toggle (this class)
                    return self._json(T.class_flags(conn, board.cfg, board.class_id, day))
                if path == "/api/teacher/solved":
                    return self._json({"rows": T.solved_rows(conn, board.class_id, day)})
            return self._send(404, b"not found", "text/plain")

        def _teacher_post(self, body: dict):
            from . import teacher as T
            from .moments import MomentError
            with board.lock, board.conn() as conn:
                cls = conn.execute("SELECT * FROM classes WHERE id = ?", (board.class_id,)).fetchone()
                try:
                    if self.path == "/api/teacher/roster":
                        st = conn.execute("SELECT class_id FROM students WHERE slug = ?", (str(body.get("slug", "")),)).fetchone()
                        if not st or st["class_id"] != board.class_id:
                            return self._json({"ok": False, "error": "unknown_student"}, 404)
                        return self._json(T.roster_edit(conn, board.cfg, body))
                    if self.path == "/api/teacher/note":
                        return self._json(T.note_save(conn, body))
                    if self.path == "/api/teacher/import":
                        return self._json(T.import_files(conn, body))
                    if self.path == "/api/teacher/cancel":
                        return self._json(T.cancel(conn, cls, body))
                    if self.path == "/api/teacher/publish":
                        return self._json(T.publish(conn, board.cfg))
                    if self.path == "/api/teacher/deploy":
                        return self._json(T.deploy(board.cfg, runner=board.deploy_runner))
                    if self.path == "/api/teacher/contact":
                        return self._json(T.contact_log(conn, body))
                    if self.path == "/api/teacher/snooze":
                        return self._json(T.contact_snooze(conn, body, board.day))
                    if self.path == "/api/teacher/profile":
                        return self._json(T.contact_profile(conn, body))
                    if self.path == "/api/teacher/policy":
                        return self._json(T.policy_save(conn, cls, body))
                    if self.path == "/api/teacher/solved":
                        return self._json(T.solved_fix(conn, body))
                except (ops.OpError, T.TeacherError, MomentError, ValueError) as e:
                    conn.rollback()
                    return self._json({"ok": False, "error": str(e)}, 400)
            return self._json({"ok": False, "error": "not_found"}, 404)

        def do_GET(self):
            if self.headers.get("Content-Length"):
                self._read_body()             # a GET with a body: drain it too before answering
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path, _, query = self.path.partition("?")
            if path.startswith("/api/teacher/"):
                return self._teacher_get(path, query)
            if path == "/live":
                return self._send(200, board.live_page().encode(), "text/html; charset=utf-8")
            if path == "/api/live/sets":
                from . import live as L
                return self._json({"sets": L.list_sets(board.cfg), "open": board.live and {k: board.live[k] for k in ("room", "title")}})
            if path == "/api/live/state":
                from . import live as L
                if not board.live:          # a poll that lands just after 방 닫기: an answer, not an error
                    return self._json({"ok": True, "open": False})
                try:
                    return self._json(board.live_call("state"))
                except (L.LiveError, PullError) as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
            if path == "/teacher":
                return self._send(200, board.teacher_page().encode(), "text/html; charset=utf-8")
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

        def _read_body(self) -> bytes | None:
            """Read the whole request body before any reply, refusals included: answering first and closing with
            unread bytes makes Windows reset the connection (WinError 10054 on the client). Returns None when the
            body is over the limit (it is still drained, in chunks, up to a hard cap); a length that is not a number gives
            an unparseable body (400) and closes the connection."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0:                          # no usable length: nothing can be read safely; answer 400 and close
                self.close_connection = True
                return b"\x00"
            if n > MAX_POST:
                left = min(n, DRAIN_CAP)
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                return None
            return self.rfile.read(n) if n else b""

        def do_POST(self):
            raw = self._read_body()           # first, on every path
            if not self._host_ok():
                return self._json({"ok": False, "error": "host"}, 403)
            # only same-origin requests from the board page (a page elsewhere must not write scores)
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self._json({"ok": False, "error": "origin"}, 403)
            if raw is None:
                return self._json({"ok": False, "error": "too_big"}, 413)
            try:
                body = json.loads(raw or b"{}")
                if self.path.startswith("/api/teacher/"):
                    return self._teacher_post(body)
                if self.path.startswith("/api/live/"):
                    from . import live as L
                    from .moments import MomentError
                    try:
                        with board.lock:
                            return self._json(board.live_call(self.path[len("/api/live/"):], body))
                    except (L.LiveError, PullError, MomentError) as e:
                        return self._json({"ok": False, "error": str(e)}, 400)
                if self.path == "/api/moment":
                    from .moments import MomentError
                    with board.lock, board.conn() as conn:
                        try:
                            msg = board.add_moment(conn, body)
                        except MomentError as e:
                            return self._json({"ok": False, "error": str(e)}, 400)
                        st = board.state_json(conn)
                        st["message"] = msg
                        return self._json(st)
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


