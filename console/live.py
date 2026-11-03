"""F7: AMP Live on the laptop side. Question sets are local JSON files; their text is shown on the projector only.
The Worker receives, per question, only the kind (choice ①–⑤ / num) and the answer key."""
from __future__ import annotations

import io
import json
import math
import sqlite3
from datetime import date
from pathlib import Path

from . import ops
from .config import ROOT, Config
from .db import log_event, now_utc_iso

BONUS_SHARE = 0.8        # >= 80 % of the class correct -> class +N


class LiveError(ValueError):
    pass


def set_dirs(cfg: Config) -> list[Path]:
    d = Path(str(cfg.extra.get("live_dir") or "live"))
    return [d if d.is_absolute() else Path.cwd() / d, ROOT / "demo" / "live"]


def list_sets(cfg: Config) -> list[dict]:
    out, seen = [], set()
    for d in set_dirs(cfg):
        for p in sorted(d.glob("*.json")) if d.exists() else []:
            if p.name in seen:
                continue
            seen.add(p.name)
            try:
                data = load_set(p)
            except LiveError:
                continue
            out.append({"name": p.name, "title": data["title"], "n": len(data["questions"])})
    return out


def load_set(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LiveError(f"cannot read {path.name}") from e
    qs = []
    for i, q in enumerate(data.get("questions") or [], 1):
        kind = "num" if q.get("kind") == "num" else "choice"
        ans = str(q.get("answer", "")).strip()
        if (kind == "choice" and ans not in "12345") or (kind == "num" and not ans.lstrip("-").isdigit()) or not ans:
            raise LiveError(f"{path.name}: question {i} has no valid answer")
        qs.append({"q": str(q.get("q", "")), "kind": kind, "choices": [str(c) for c in (q.get("choices") or [])][:5],
                   "answer": ans})
    if not qs or len(qs) > 30:
        raise LiveError(f"{path.name}: 1-30 questions")
    return {"title": str(data.get("title") or path.stem), "questions": qs}


def find_set(cfg: Config, name: str) -> dict:
    name = Path(name).name
    for d in set_dirs(cfg):
        if (d / name).exists():
            return load_set(d / name)
    raise LiveError("no such question set")


def worker_payload(class_id: int, qs: list[dict]) -> dict:
    """What leaves the laptop: kind + answer per question. Never the question text or the choices."""
    return {"class": ops.class_key(class_id), "questions": [{"kind": q["kind"], "answer": q["answer"]} for q in qs]}


def needed(size: int) -> int:
    return max(1, math.ceil(BONUS_SHARE * size))


def maybe_bonus(conn: sqlite3.Connection, cfg: Config, class_id: int, session_id: int | None, day: date, room: str,
                state: dict) -> int:
    """After a reveal: if >= 80 % of the class answered correctly, the class total gets +N once per question."""
    if not state.get("revealed") or not state.get("q") or state.get("correct") is None:
        return 0
    if state["correct"] < needed(int(state.get("size") or 0)):
        return 0
    points = int(cfg.extra.get("live_bonus", 5))
    ref = f"live:{day.isoformat()}:{room}:{state['q']}"
    cur = conn.execute("INSERT OR IGNORE INTO class_bonus (class_id, session_id, points, source, ref, ts) VALUES (?, ?, ?, 'live', ?, ?)",
                       (class_id, session_id, points, ref, now_utc_iso()))
    if cur.rowcount:
        log_event(conn, "class_bonus", {"class_id": class_id, "points": points, "ref": ref})
        conn.commit()
        return points
    return 0


def moment_card(title: str, q: int, rate: int) -> bytes:
    """A generated 'board photo' for a Live highlight: numbers only, no question text, no names, no faces."""
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (1200, 900), "#0b1f3a")
    dr = ImageDraw.Draw(im)
    for i in range(12):
        dr.ellipse([600 - 60 * i, 450 - 60 * i, 600 + 60 * i, 450 + 60 * i], outline=("#1e3a8a", "#312e81")[i % 2], width=3)
    dr.rounded_rectangle([200, 520, 1000, 600], radius=40, fill="#1f2937")
    dr.rounded_rectangle([200, 520, 200 + int(8 * rate), 600], radius=40, fill="#14b8a6")
    try:
        font = ImageFont.load_default(size=120)
        small = ImageFont.load_default(size=48)
    except TypeError:  # Pillow < 10.1
        font = small = ImageFont.load_default()
    dr.text((600, 330), f"{rate}%", fill="#ffffff", font=font, anchor="mm")
    dr.text((600, 700), f"LIVE  Q{q}", fill="#c7d2fe", font=small, anchor="mm")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()
