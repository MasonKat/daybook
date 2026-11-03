"""F15.4: the mascot slot. `"brand": {"mascot": "brand/mascot.png", "mascot_public": false}` in daybook.json.

The image is the academy's own file in `brand/` (gitignored), never in the repo. It is shown small next to the AMP
wordmark on student and parent pages and the board; without it nothing is shown. The taste build shows it only with
`mascot_public: true`. The demo uses a neutral placeholder shape (never a real character).
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from .config import ROOT, Config

SIZE = 96          # px (shown at 28 px; 96 keeps it sharp on 3x screens)
MAX_BYTES = 30_000


def source(cfg: Config) -> Path | None:
    b = cfg.extra.get("brand") or {}
    rel = b.get("mascot") if isinstance(b, dict) else None
    if not rel:
        return None
    p = Path(str(rel))
    for cand in ((p,) if p.is_absolute() else (Path.cwd() / p, ROOT / p)):
        if cand.is_file():
            return cand
    return None


def public(cfg: Config) -> bool:
    b = cfg.extra.get("brand") or {}
    return isinstance(b, dict) and b.get("mascot_public") is True


def png_bytes(src: Path) -> bytes:
    """A small square PNG (transparency kept), at most SIZE px."""
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGBA")
        im.thumbnail((SIZE, SIZE), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
    data = buf.getvalue()
    if len(data) > MAX_BYTES:
        raise ValueError(f"the mascot is still {len(data)} bytes at {SIZE} px; use a simpler image")
    return data


def data_uri(cfg: Config) -> str:
    """For the local board (no file route needed)."""
    src = source(cfg)
    return "data:image/png;base64," + base64.b64encode(png_bytes(src)).decode() if src else ""


def write_placeholder(path: Path) -> Path:
    """The demo's neutral shape: a soft rounded square with a circle. Not a character, not a logo."""
    from PIL import Image, ImageDraw
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((4, 4, SIZE - 4, SIZE - 4), radius=24, fill=(124, 58, 237, 255))
    d.ellipse((30, 30, SIZE - 30, SIZE - 30), fill=(255, 255, 255, 230))
    im.save(path, "PNG", optimize=True)
    return path
