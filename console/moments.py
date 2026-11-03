"""오늘의 장면 (F4): the teacher's class highlight. Images are cleaned on the laptop before anything leaves it:
EXIF (GPS!) stripped, long edge <= 1080 px, JPEG <= 200 KB. Every post needs an explicit yes to the faces/names gate."""
from __future__ import annotations

import base64
import io
import sqlite3
from datetime import date
from pathlib import Path

from . import ops
from .config import ROOT, Config
from .db import log_event, now_utc_iso

MAX_EDGE = 1080
MAX_BYTES = 200 * 1024
PAGE_EDGE = 720            # the copy baked into dist/ (<= 100 KB, the page-file limit)
PAGE_BYTES = 100 * 1024
MAX_PER_SESSION = 3
MAX_TEXT = 60
GATE = "학생 얼굴·이름·문제집 원문 전체가 보이지 않나요? (y/N)"


class MomentError(ValueError):
    pass


def _jpeg(im, max_edge: int, max_bytes: int) -> bytes:
    from PIL import Image
    im = im.copy()
    im.thumbnail((max_edge, max_edge), Image.LANCZOS)
    for q in (82, 74, 66, 58, 50, 42, 35):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True)        # no exif= argument: nothing of the original travels
        if buf.tell() <= max_bytes:
            return buf.getvalue()
    im.thumbnail((max_edge * 2 // 3, max_edge * 2 // 3), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=40, optimize=True)
    if buf.tell() > max_bytes:
        raise MomentError("the photo cannot be made small enough")
    return buf.getvalue()


def prepare_image(data: bytes, max_edge: int = MAX_EDGE, max_bytes: int = MAX_BYTES) -> bytes:
    """Any image -> upright RGB JPEG without EXIF/ICC/comments, long edge <= max_edge, size <= max_bytes."""
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)          # keep the visible orientation, then drop all metadata
            im = im.convert("RGB")
            clean = Image.new("RGB", im.size)
            clean.paste(im)
    except Exception as e:  # noqa: BLE001 - any decode failure is the same answer for the teacher
        raise MomentError(f"not a readable image ({e.__class__.__name__})") from e
    return _jpeg(clean, max_edge, max_bytes)


def store_dir(cfg: Config) -> Path:
    p = Path(cfg.moments_dir)
    return p if p.is_absolute() else (Path.cwd() / p)


def image_path(cfg: Config, key: str) -> Path:
    p = store_dir(cfg) / key
    if not p.exists():
        alt = ROOT / cfg.moments_dir / key
        if alt.exists():
            return alt
    return p


def add_moment(conn: sqlite3.Connection, cfg: Config, cls: sqlite3.Row, day: date, data: bytes, text: str,
               confirmed: bool, push=None) -> sqlite3.Row:
    """Store a moment (after the y/N gate) and, when a Worker is configured, PUT it for the same-day view."""
    if not confirmed:
        raise MomentError("not posted: the faces/names/book-page check was not answered with y")
    text = " ".join((text or "").split())
    if len(text) > MAX_TEXT:
        raise MomentError(f"the line is longer than {MAX_TEXT} characters")
    sid = ops.ensure_session(conn, cls["id"], day)
    n = conn.execute("SELECT COUNT(*) FROM moments WHERE session_id = ?", (sid,)).fetchone()[0] + 1
    if n > MAX_PER_SESSION:
        raise MomentError(f"at most {MAX_PER_SESSION} moments per class session")
    jpeg = prepare_image(data)
    key = f"{cls['id']}/{day.isoformat()}_{n}.jpg"
    path = store_dir(cfg) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg)
    ts = now_utc_iso()
    cur = conn.execute("INSERT INTO moments (class_id, session_id, ts, text, image_key) VALUES (?, ?, ?, ?, ?)",
                       (cls["id"], sid, ts, text, key))
    log_event(conn, "moment_add", {"moment_id": cur.lastrowid, "class_id": cls["id"], "date": day.isoformat(), "n": n}, ts)
    conn.commit()
    if push is not None:
        push({"class": ops.class_key(cls["id"]), "date": day.isoformat(), "n": n, "text": text,
              "image": base64.b64encode(jpeg).decode("ascii")})
    return conn.execute("SELECT * FROM moments WHERE id = ?", (cur.lastrowid,)).fetchone()


def page_copy(cfg: Config, key: str) -> bytes | None:
    p = image_path(cfg, key)
    if not p.exists():
        return None
    return prepare_image(p.read_bytes(), PAGE_EDGE, PAGE_BYTES)


def make_demo_moment_images(out: Path, n: int = 4) -> list[Path]:
    """Synthetic 'board photos' for the demo: chalk-like strokes on a green board, no people, no text."""
    import math
    from PIL import Image, ImageDraw
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        im = Image.new("RGB", (1400, 1050), ("#1f3b2c", "#22313f", "#2b2440", "#1d3540")[i % 4])
        dr = ImageDraw.Draw(im)
        for k in range(5):
            dr.line([(120 + 60 * k, 860 - 30 * k), (1280 - 40 * k, 220 + 70 * k)], fill="#3d5a4a", width=2)
        pts = [(x, 520 - 240 * math.sin((x - 200) / (130 + 40 * i)) * math.exp(-(x - 700) ** 2 / 4e5)) for x in range(200, 1250, 6)]
        dr.line(pts, fill=("#f8fafc", "#fde68a", "#a5f3fc", "#fbcfe8")[i % 4], width=9, joint="curve")
        dr.ellipse([640 - 26, 500 - 26, 640 + 26, 500 + 26], outline="#fca5a5", width=7)
        dr.rectangle([0, 960, 1400, 1050], fill="#6b4f3a")
        p = out / f"m{i + 1}.jpg"
        exif = Image.Exif()
        exif[0x010F] = "DemoCamera"                     # synthetic EXIF so the demo also exercises the stripping
        im.save(p, "JPEG", quality=85, exif=exif)
        paths.append(p)
    return paths
