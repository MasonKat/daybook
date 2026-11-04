"""F22.4 ⚙ 설정: each student's own avatar and section order (reorder, never hide).

The settings live server-side per token (a small JSON in the Worker's D1, validated against SECTIONS there too) so they
follow the student across devices; `pull` copies them to SQLite (`student_prefs`) and the generator renders every page
in that order. An own picture is pre-moderated: it waits in `avatar_uploads` (status pending) until the teacher approves
it in the Teacher tab, and only then is it published, to the student's own page and their parent's page.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from html import escape

# The Worker's PREF_SECTIONS must stay equal to this (tests/test_prefs.py compares them).
SECTIONS: dict[str, list[str]] = {
    "rings": ["moments", "videos", "popular", "streak", "goal", "review", "teacher"],
    "today": ["routine", "test", "classwork", "vote"],
    "profile": ["watched", "solved", "tiles", "lines", "skills", "shouts", "notes"],
}
MAX_BYTES = 1024
UPLOAD_MAX = 60 * 1024


class PrefsError(ValueError):
    pass


# ---------------------------------------------------------------- avatars: in-house inline SVG (no library, no characters)
COLORS = ["#7c3aed", "#ec4899", "#f59e0b", "#10b981", "#0ea5e9", "#ef4444", "#6366f1", "#64748b"]
_INK = "#1f2937"
_SKIN = "#ffe0b2"


def _face(extra: str) -> str:
    return f'<circle cx="32" cy="34" r="19" fill="{_SKIN}"/>{extra}'


_EYES = f'<circle cx="25" cy="31" r="2.6" fill="{_INK}"/><circle cx="39" cy="31" r="2.6" fill="{_INK}"/>'
_SMILE = f'<path d="M24 39q8 8 16 0" fill="none" stroke="{_INK}" stroke-width="2.6" stroke-linecap="round"/>'
_ARCS = f'<path d="M22 32q3-4 6 0M36 32q3-4 6 0" fill="none" stroke="{_INK}" stroke-width="2.4" stroke-linecap="round"/>'
DESIGNS: dict[str, str] = {
    "smile": _face(_EYES + _SMILE),
    "wink": _face(f'<path d="M22 31q3-3 6 0" fill="none" stroke="{_INK}" stroke-width="2.4" stroke-linecap="round"/>'
                  f'<circle cx="39" cy="31" r="2.6" fill="{_INK}"/>' + _SMILE),
    "cool": _face(f'<rect x="18" y="26" width="12" height="8" rx="3" fill="{_INK}"/><rect x="34" y="26" width="12" height="8" rx="3" fill="{_INK}"/>'
                  f'<path d="M30 29h4" stroke="{_INK}" stroke-width="2"/><path d="M26 41q6 4 12 0" fill="none" stroke="{_INK}" stroke-width="2.4" stroke-linecap="round"/>'),
    "sleepy": _face(f'<path d="M22 31h6M36 31h6" stroke="{_INK}" stroke-width="2.4" stroke-linecap="round"/><circle cx="32" cy="41" r="2.6" fill="{_INK}"/>'
                    '<path d="M44 12h7l-7 8h7" fill="none" stroke="#fff" stroke-width="2.2" stroke-linejoin="round"/>'),
    "laugh": _face(_ARCS + f'<path d="M23 37h18q-2 11-9 11t-9-11z" fill="{_INK}"/><path d="M27 44q5 3 10 0" fill="#f87171"/>'),
    "wow": _face(_EYES + f'<circle cx="32" cy="42" r="4.2" fill="{_INK}"/>'),
    "cat": ('<path d="M14 26l5-16 10 9zM50 26l-5-16-10 9z" fill="#f5f5f4"/><circle cx="32" cy="35" r="18" fill="#f5f5f4"/>' + _EYES +
            f'<path d="M30 37h4l-2 3z" fill="#f472b6"/><path d="M14 36h9M14 41h9M41 36h9M41 41h9" stroke="{_INK}" stroke-width="1.4"/>'),
    "dog": ('<circle cx="32" cy="34" r="18" fill="#e7b98a"/><ellipse cx="15" cy="31" rx="6" ry="12" fill="#8d5b3a"/>'
            '<ellipse cx="49" cy="31" rx="6" ry="12" fill="#8d5b3a"/>' + _EYES +
            f'<ellipse cx="32" cy="39" rx="4" ry="3" fill="{_INK}"/><path d="M32 42v3" stroke="{_INK}" stroke-width="2"/>'),
    "bear": ('<circle cx="18" cy="19" r="7" fill="#a16207"/><circle cx="46" cy="19" r="7" fill="#a16207"/><circle cx="32" cy="35" r="19" fill="#ca8a04"/>'
             '<ellipse cx="32" cy="41" rx="8" ry="6" fill="#fde68a"/>' + _EYES + f'<ellipse cx="32" cy="39" rx="3" ry="2" fill="{_INK}"/>'),
    "rabbit": ('<ellipse cx="24" cy="15" rx="5" ry="13" fill="#fff"/><ellipse cx="40" cy="15" rx="5" ry="13" fill="#fff"/>'
               '<ellipse cx="24" cy="16" rx="2" ry="8" fill="#f9a8d4"/><ellipse cx="40" cy="16" rx="2" ry="8" fill="#f9a8d4"/>'
               '<circle cx="32" cy="39" r="16" fill="#fff"/>' + f'<circle cx="26" cy="37" r="2.4" fill="{_INK}"/><circle cx="38" cy="37" r="2.4" fill="{_INK}"/>'
               '<circle cx="32" cy="43" r="2" fill="#f472b6"/>'),
    "fox": ('<path d="M12 14l10 6h20l10-6-4 20-14 18-14-18z" fill="#f97316"/><path d="M20 34l12 18 12-18-12 4z" fill="#fff7ed"/>'
            + _EYES + f'<circle cx="32" cy="45" r="2.4" fill="{_INK}"/>'),
    "panda": ('<circle cx="18" cy="19" r="7" fill="#111827"/><circle cx="46" cy="19" r="7" fill="#111827"/><circle cx="32" cy="35" r="19" fill="#fff"/>'
              '<ellipse cx="25" cy="32" rx="5" ry="6" fill="#111827" transform="rotate(-20 25 32)"/><ellipse cx="39" cy="32" rx="5" ry="6" fill="#111827" transform="rotate(20 39 32)"/>'
              '<circle cx="25" cy="32" r="1.8" fill="#fff"/><circle cx="39" cy="32" r="1.8" fill="#fff"/><ellipse cx="32" cy="41" rx="3" ry="2" fill="#111827"/>'),
    "owl": ('<ellipse cx="32" cy="36" rx="18" ry="20" fill="#92400e"/><circle cx="24" cy="31" r="8" fill="#fff"/><circle cx="40" cy="31" r="8" fill="#fff"/>'
            f'<circle cx="24" cy="31" r="3.4" fill="{_INK}"/><circle cx="40" cy="31" r="3.4" fill="{_INK}"/><path d="M29 38h6l-3 5z" fill="#f59e0b"/>'),
    "penguin": ('<ellipse cx="32" cy="36" rx="17" ry="21" fill="#111827"/><ellipse cx="32" cy="40" rx="11" ry="15" fill="#fff"/>'
                '<circle cx="27" cy="31" r="2.2" fill="#111827"/><circle cx="37" cy="31" r="2.2" fill="#111827"/><path d="M28 35h8l-4 4z" fill="#f59e0b"/>'),
    "frog": ('<circle cx="21" cy="21" r="8" fill="#22c55e"/><circle cx="43" cy="21" r="8" fill="#22c55e"/><ellipse cx="32" cy="37" rx="21" ry="16" fill="#22c55e"/>'
             f'<circle cx="21" cy="21" r="4" fill="#fff"/><circle cx="43" cy="21" r="4" fill="#fff"/><circle cx="21" cy="21" r="2" fill="{_INK}"/><circle cx="43" cy="21" r="2" fill="{_INK}"/>'
             f'<path d="M20 39q12 9 24 0" fill="none" stroke="{_INK}" stroke-width="2.4" stroke-linecap="round"/>'),
    "chick": ('<path d="M30 14q2-6 4 0" fill="none" stroke="#f59e0b" stroke-width="2.4"/><circle cx="32" cy="35" r="18" fill="#fde047"/>'
              f'<circle cx="26" cy="32" r="2.4" fill="{_INK}"/><circle cx="38" cy="32" r="2.4" fill="{_INK}"/><path d="M28 37h8l-4 5z" fill="#f97316"/>'),
    "star": '<path d="M32 10l6.5 13.6 14.9 1.9-10.9 10.3 2.8 14.8L32 43.4l-13.3 7.2 2.8-14.8-10.9-10.3 14.9-1.9z" fill="#fde047"/>',
    "heart": '<path d="M32 50S12 38 12 25a10 10 0 0 1 20-3 10 10 0 0 1 20 3c0 13-20 25-20 25z" fill="#fff"/>',
    "planet": ('<circle cx="32" cy="32" r="13" fill="#fde68a"/><ellipse cx="32" cy="34" rx="24" ry="7" fill="none" stroke="#fff" stroke-width="3"'
               ' transform="rotate(-18 32 34)"/>'),
    "rocket": ('<path d="M32 8q12 10 10 30H22Q20 18 32 8z" fill="#f8fafc"/><circle cx="32" cy="24" r="4.5" fill="#38bdf8"/>'
               '<path d="M22 32l-7 10h8zM42 32l7 10h-8z" fill="#f87171"/><path d="M26 40h12l-6 14z" fill="#fbbf24"/>'),
    "flower": ('<g fill="#fff"><circle cx="32" cy="18" r="8"/><circle cx="45" cy="28" r="8"/><circle cx="40" cy="44" r="8"/>'
               '<circle cx="24" cy="44" r="8"/><circle cx="19" cy="28" r="8"/></g><circle cx="32" cy="32" r="7" fill="#fbbf24"/>'),
    "bolt": '<path d="M36 8L16 36h13l-4 20 22-30H34z" fill="#fde047"/>',
    "sun": ('<g stroke="#fde047" stroke-width="3" stroke-linecap="round"><path d="M32 6v7M32 51v7M6 32h7M51 32h7M13 13l5 5M46 46l5 5M13 51l5-5M46 18l5-5"/></g>'
            '<circle cx="32" cy="32" r="12" fill="#fde047"/>'),
    "moon": '<path d="M40 12a20 20 0 1 0 12 30A16 16 0 0 1 40 12z" fill="#fef3c7"/>',
}
DESIGN_IDS = list(DESIGNS)
PRESET_RE_PART = "|".join(DESIGN_IDS)


def valid_avatar(aid: str) -> bool:
    if aid in ("", "own", "mascot"):
        return True
    if not aid.startswith("p:") or "-" not in aid:
        return False
    d, _, c = aid[2:].rpartition("-")
    return d in DESIGNS and c.isdigit() and 0 <= int(c) < len(COLORS)


def avatar_svg(aid: str, label: str = "") -> str:
    """A preset id (p:<design>-<colour>) -> one inline <svg> (our own markup, no user input in it)."""
    if not aid.startswith("p:") or not valid_avatar(aid):
        return ""
    d, _, c = aid[2:].rpartition("-")
    aria = f' role="img" aria-label="{escape(label)}"' if label else ' aria-hidden="true"'
    return (f'<svg class="avsvg" viewBox="0 0 64 64"{aria}><rect width="64" height="64" rx="32" fill="{COLORS[int(c)]}"/>'
            f'{DESIGNS[d]}</svg>')


def gallery_svg(design: str, label: str) -> str:
    """The settings gallery's tile: the background follows the chosen colour (CSS --avc), the drawing is the same."""
    return (f'<svg class="avsvg" viewBox="0 0 64 64" role="img" aria-label="{escape(label)}"><rect class="avbg" width="64" height="64" rx="32"'
            f' style="fill: var(--avc, {COLORS[0]})"/>{DESIGNS[design]}</svg>')


# ---------------------------------------------------------------- F22.5: the look (theme, light/dark, text size, background)
def _look():
    from . import themes as TH
    return {"theme": lambda v: v == "" or v in TH.THEMES, "mode": lambda v: v in TH.MODES, "size": lambda v: v in TH.SIZES,
            "bg": lambda v: v == "" or bool(TH.BG_RE.match(v))}


LOOK = _look()


# ---------------------------------------------------------------- validation and order
def validate(obj) -> dict:
    """The Worker's rule: known kinds only; an order names every id of its kind exactly once (no hiding)."""
    if not isinstance(obj, dict):
        raise PrefsError("not an object")
    out: dict = {}
    for k, v in obj.items():
        if k in SECTIONS:
            want = SECTIONS[k]
            if not isinstance(v, list) or len(v) != len(want) or len(set(v)) != len(v) or any(x not in want for x in v):
                raise PrefsError(f"{k}: every section exactly once")
            out[k] = list(v)
        elif k == "avatar":
            if not isinstance(v, str) or not valid_avatar(v):
                raise PrefsError("unknown avatar")
            out[k] = v
        elif k in LOOK:                                   # F22.5: presets only (locks are applied when a page renders)
            if not isinstance(v, str) or not LOOK[k](v):
                raise PrefsError(f"unknown {k}")
            out[k] = v
        else:
            raise PrefsError(f"unknown key {k}")
    if len(json.dumps(out, ensure_ascii=False).encode()) > MAX_BYTES:
        raise PrefsError("too big")
    return out


def ordered(kind: str, saved: list | None, pin_routine: bool = False) -> list[str]:
    """The student's order (unknown ids dropped, missing ones kept at their default place); the class routine first
    during the class window."""
    base = SECTIONS[kind]
    out = [x for x in (saved or []) if x in base]
    out = list(dict.fromkeys(out))
    for i, x in enumerate(base):
        if x not in out:
            out.insert(min(i, len(out)), x)
    if pin_routine and kind == "today":
        out = ["routine"] + [x for x in out if x != "routine"]
    return out


def load(conn: sqlite3.Connection, student_id: int) -> dict:
    r = conn.execute("SELECT prefs FROM student_prefs WHERE student_id = ?", (student_id,)).fetchone()
    if not r:
        return {}
    try:
        return json.loads(r[0]) or {}
    except ValueError:
        return {}


def save(conn: sqlite3.Connection, student_id: int, obj, updated: str = "") -> dict:
    p = validate(obj)
    conn.execute("""INSERT INTO student_prefs (student_id, prefs, updated) VALUES (?, ?, ?)
                    ON CONFLICT (student_id) DO UPDATE SET prefs = excluded.prefs, updated = excluded.updated""",
                 (student_id, json.dumps(p, ensure_ascii=False), updated))
    return p


# ---------------------------------------------------------------- own pictures: pull, the teacher's queue, the published file
def sniff(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def add_upload(conn: sqlite3.Connection, student_id: int, data: bytes, ts: str) -> int | None:
    """A pending own picture (from the Worker's pull). Duplicates (same student and time) are ignored."""
    mime = sniff(data)
    if not mime or len(data) > UPLOAD_MAX:
        return None
    if conn.execute("SELECT 1 FROM avatar_uploads WHERE student_id = ? AND ts = ?", (student_id, ts)).fetchone():
        return None
    cur = conn.execute("INSERT INTO avatar_uploads (student_id, mime, image, status, ts) VALUES (?, ?, ?, 'pending', ?)",
                       (student_id, mime, data, ts))
    return cur.lastrowid


def decide(conn: sqlite3.Connection, upload_id: int, ok: bool, when: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM avatar_uploads WHERE id = ?", (upload_id,)).fetchone()
    if not row or row["status"] != "pending":
        raise PrefsError("no such pending picture")
    if ok:   # one approved picture per student: the older one is retired
        conn.execute("UPDATE avatar_uploads SET status = 'replaced' WHERE student_id = ? AND status = 'approved'", (row["student_id"],))
    conn.execute("UPDATE avatar_uploads SET status = ?, decided = ? WHERE id = ?", ("approved" if ok else "rejected", when, upload_id))
    return row


def approved(conn: sqlite3.Connection, student_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM avatar_uploads WHERE student_id = ? AND status = 'approved' ORDER BY id DESC LIMIT 1",
                        (student_id,)).fetchone()


def pending(conn: sqlite3.Connection) -> list[dict]:
    return [{"id": r["id"], "student_id": r["student_id"], "ts": r["ts"],
             "src": f"data:{r['mime']};base64,{base64.b64encode(r['image']).decode()}"}
            for r in conn.execute("SELECT * FROM avatar_uploads WHERE status = 'pending' ORDER BY id")]


def resolve(conn: sqlite3.Connection, student_id: int, prefs: dict | None = None, mascot: bool = False) -> dict:
    """What this student's avatar is: {"kind": "svg", "id"} | {"kind": "img", "mime", "data"} | {"kind": "mascot"} | {"kind": ""}."""
    p = load(conn, student_id) if prefs is None else prefs
    aid = p.get("avatar", "")
    if aid.startswith("p:") and valid_avatar(aid):
        return {"kind": "svg", "id": aid}
    if aid == "own":
        a = approved(conn, student_id)
        if a:
            return {"kind": "img", "mime": a["mime"], "data": bytes(a["image"])}
    if aid == "mascot" and mascot:
        return {"kind": "mascot"}
    return {"kind": ""}


def ext(mime: str) -> str:
    return "png" if mime == "image/png" else "jpg"


def apply_pull(conn: sqlite3.Connection, body: dict, token_map: dict[str, int]) -> tuple[int, int]:
    """The Worker's /avatars-pull: settings rows (validated again here) and pending pictures. Returns (prefs, pictures)."""
    n_p = n_a = 0
    for it in body.get("prefs", []):
        sid = token_map.get(it.get("token", ""))
        if not sid:
            continue
        try:
            save(conn, sid, json.loads(it.get("prefs") or "{}"), it.get("updated", ""))
            n_p += 1
        except (PrefsError, ValueError):
            continue
    for it in body.get("avatars", []):
        sid = token_map.get(it.get("token", ""))
        if not sid:
            continue
        try:
            data = base64.b64decode(it.get("image") or "", validate=True)
        except ValueError:
            continue
        if add_upload(conn, sid, data, it.get("ts", "")):
            n_a += 1
    return n_p, n_a
