"""F19.4: the teacher remote ("tap who, speak what"): a phone page on the Worker (/t/remote) behind the teacher token.

Names never reach the Worker in the clear: the console encrypts the class lists (class name, weekdays, start time, and per
student the link token, the label = call sign or display name, and a seat number when the call sign is one, e.g. 7번) with
`remote_key`, a random key kept in daybook.json. The key reaches the teacher's phone once, inside the setup link's
#fragment (never sent to a server): the Teacher tab shows it as an on-screen QR (never printed: it carries the teacher
token) and `remote link` prints it. The Worker stores only the ciphertext and token-keyed shout-outs.

Encryption (no new dependency; WebCrypto on the phone): k_enc = HMAC(key, "enc"), k_mac = HMAC(key, "mac");
keystream block i = HMAC(k_enc, nonce | i32be); tag = HMAC(k_mac, nonce | ciphertext); blob = b64url(nonce16 | ct | tag).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from pathlib import Path

from .config import Config

DEFAULT_CHIPS = ["잘했어", "풀이 깔끔", "질문 좋았어", "발표 최고", "끝까지 해냄"]


class RemoteError(RuntimeError):
    pass


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _keys(key: bytes) -> tuple[bytes, bytes]:
    return hmac.new(key, b"enc", hashlib.sha256).digest(), hmac.new(key, b"mac", hashlib.sha256).digest()


def encrypt(key: bytes, plain: bytes, nonce: bytes | None = None) -> str:
    k_enc, k_mac = _keys(key)
    nonce = nonce or secrets.token_bytes(16)
    stream = b"".join(hmac.new(k_enc, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest() for i in range((len(plain) + 31) // 32))
    ct = bytes(a ^ b for a, b in zip(plain, stream))
    return _b64u(nonce + ct + hmac.new(k_mac, nonce + ct, hashlib.sha256).digest())


def decrypt(key: bytes, blob: str) -> bytes:
    raw = _unb64u(blob)
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    k_enc, k_mac = _keys(key)
    if not hmac.compare_digest(tag, hmac.new(k_mac, nonce + ct, hashlib.sha256).digest()):
        raise RemoteError("the roster does not match this key")
    stream = b"".join(hmac.new(k_enc, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest() for i in range((len(ct) + 31) // 32))
    return bytes(a ^ b for a, b in zip(ct, stream))


def key(cfg: Config) -> bytes | None:
    k = str(cfg.extra.get("remote_key") or "")
    try:
        b = _unb64u(k) if k else b""
    except ValueError:
        return None
    return b if len(b) == 32 else None


def ensure_key(cfg: Config, rotate: bool = False) -> bytes:
    """A random remote key in daybook.json (never the repo). rotate: a new key (the old phone setup stops working)."""
    k = key(cfg)
    if k and not rotate:
        return k
    path = Path(cfg.path or "daybook.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["remote_key"] = _b64u(secrets.token_bytes(32))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
    cfg.extra["remote_key"] = data["remote_key"]
    return _unb64u(data["remote_key"])


def chips(cfg: Config) -> list[str]:
    c = cfg.extra.get("remote_chips")
    return [str(x)[:20] for x in c if str(x).strip()][:8] if isinstance(c, list) and c else DEFAULT_CHIPS


def _seat(callsign: str | None) -> int | None:
    m = re.fullmatch(r"(\d{1,2})번", (callsign or "").replace(" ", ""))
    return int(m.group(1)) if m else None


def roster(conn: sqlite3.Connection, cfg: Config) -> dict:
    """What the phone shows: per class its students' labels (call sign, else display name) and link tokens."""
    from . import models as M
    from .ops import class_key
    out = []
    for c in conn.execute("SELECT * FROM classes ORDER BY id"):
        studs = [{"t": r["student_token"], "label": (r["callsign"] or r["display_name"]), "seat": _seat(r["callsign"])}
                 for r in conn.execute("""SELECT student_token, display_name, callsign FROM students
                                          WHERE class_id = ? AND active = 1 ORDER BY id""", (c["id"],))]
        days = [i for i in range(7) if c["weekday_mask"] and c["weekday_mask"] & (1 << i)]
        out.append({"key": class_key(c["id"]), "name": c["name"], "days": days, "start": c["start_time"] or "", "students": studs})
    from . import classwork as CW              # F21.2: the set names (and their quiz counts) for 수업 문제; never a tag
    tags = CW.load_tags(cfg)
    sets = [{"label": k, "n": max(tags[k]) if tags[k] else 0} for k in CW.set_names(cfg)]
    return {"classes": out, "chips": chips(cfg), "sets": sets, "v": 1}


def link(cfg: Config) -> str:
    """The one-time setup link: the Worker's /t/remote/ with the teacher token and the key in the #fragment."""
    if not cfg.worker or not cfg.teacher_token:
        raise RemoteError("worker_url and teacher_token must be set in daybook.json")
    k = ensure_key(cfg)
    return f"{cfg.worker}/t/remote/#t={cfg.teacher_token}&k={_b64u(k)}"


def push(conn: sqlite3.Connection, cfg: Config) -> str:
    """Send the encrypted class lists (after every 발행 and roster change). Nothing to do before the remote is set up."""
    from .pull import PullError, http_put_json
    k = key(cfg)
    if not k or not cfg.worker or not cfg.teacher_token:
        return ""
    blob = encrypt(k, json.dumps(roster(conn, cfg), ensure_ascii=False, separators=(",", ":")).encode())
    try:
        http_put_json(cfg, "/remote-roster", {"blob": blob})
        return "Worker: remote class lists updated (encrypted)"
    except PullError as e:
        return f"warning: remote class lists not sent ({e})"


def qr_svg(text: str) -> str:
    import io
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=4, border=2, dark="#000", light="#fff", xmldecl=False, svgns=True)
    return buf.getvalue().decode()
