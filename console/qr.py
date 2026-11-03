"""F5: handout QR codes. A QR only ever encodes {pages_url}/go/?set=<set_id>: never a token, never a name."""
from __future__ import annotations

import io
import re
import sqlite3
from pathlib import Path

from .config import Config

SET_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
TOKEN_LIKE = re.compile(r"[a-z2-7]{26}")


class QRError(ValueError):
    pass


def go_url(cfg: Config, set_id: str) -> str:
    if not cfg.pages:
        raise QRError("pages_url is not set in daybook.json (the QR needs the site address)")
    if not SET_RE.match(set_id or ""):
        raise QRError("set id: letters, digits, _ and - only")
    url = f"{cfg.pages}/go/?set={set_id}"
    if TOKEN_LIKE.search(url.split("?", 1)[0]) or "/s/" in url or "/p/" in url:
        raise QRError("refusing: a QR must never point at a student or parent page")
    return url


def svg(url: str, scale: int = 6) -> str:
    import segno
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=scale, border=2, xmldecl=False, svgns=True)
    return buf.getvalue().decode("utf-8")


def save(url: str, out: Path) -> Path:
    import segno
    out.parent.mkdir(parents=True, exist_ok=True)
    kind = out.suffix.lower().lstrip(".")
    if kind not in ("svg", "png"):
        raise QRError("--out must end in .svg or .png")
    segno.make(url, error="m").save(str(out), scale=10 if kind == "png" else 6, border=2)
    return out


def sheet(conn: sqlite3.Connection, cfg: Config, cls: sqlite3.Row, labels: dict[str, str]) -> str:
    """A print-ready A4 HTML page: one QR card per set id used by this class (newest first)."""
    from html import escape
    rows = conn.execute("""SELECT set_id, MAX(date) AS d FROM sessions WHERE class_id = ? AND set_id IS NOT NULL
                           GROUP BY set_id ORDER BY d DESC""", (cls["id"],)).fetchall()
    cards = []
    for r in rows:
        url = go_url(cfg, r["set_id"])
        label = labels.get(r["set_id"]) or f"{int(r['d'][5:7])}/{int(r['d'][8:10])} 과제"
        cards.append(f'<figure class="card">{svg(url, 5)}<figcaption><b>{escape(label)}</b>'
                     f'<span>휴대폰 카메라로 찍으면 내 오늘 과제 화면이 열려요</span></figcaption></figure>')
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="robots" content="noindex,nofollow"><title>AMP 과제 QR</title>
<style>
@page {{ size: A4; margin: 12mm; }}
body {{ font: 12pt -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif; margin: 0; color: #111; }}
h1 {{ font-size: 14pt; margin: 0 0 6mm; }}
.grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 6mm; }}
.card {{ margin: 0; border: 1px dashed #999; border-radius: 4mm; padding: 4mm; text-align: center; break-inside: avoid; }}
.card svg {{ width: 42mm; height: 42mm; }}
figcaption b {{ display: block; font-size: 12pt; margin-top: 2mm; }}
figcaption span {{ display: block; font-size: 8pt; color: #555; }}
</style></head><body>
<h1>{escape(cls["name"])} · 과제 QR (토큰 없음: 학생은 자기 링크를 한 번 열어 두면 돼요)</h1>
<div class="grid">{"".join(cards) or "<p>세션에 set id가 없어요: daybook session start --set …</p>"}</div>
</body></html>
"""
