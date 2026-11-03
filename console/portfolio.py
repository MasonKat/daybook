"""F16.5: `daybook export portfolio --synthetic`: a folder the teacher may show outside the academy.

Contents: the class-level metrics CSVs (class names replaced by "Class 1..n"), 3 screenshots of the synthetic demo
(committed in demo/screenshots), and 12 example comments drafted from the data, each under a generated name. Never one
row per real student; the order is shuffled. Before anything is kept, every text file is checked for every real full
name, display name, student ID and phone number (plus the no-PII text scrubber); one hit deletes the folder.
"""
from __future__ import annotations

import random
import re
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from . import metrics as MT
from . import models as M
from .config import ROOT, Config

SCREENSHOTS = ("student-stories.png", "parent-light.png", "weekly-test.png")
SYNTH_GIVEN = ["서준", "하윤", "도윤", "지아", "시우", "하린", "주원", "서아", "예준", "지유", "건우", "나은",
               "우진", "다인", "선우", "채원"]


class PortfolioError(RuntimeError):
    pass


def _leaks(conn: sqlite3.Connection, folder: Path) -> list[str]:
    from .lint import PHONE_RE, RAW_ID_RE
    needles = set()
    for r in conn.execute("SELECT full_name, display_name, ext_id, parent_phone FROM students"):
        for v in (r["full_name"], r["display_name"], r["ext_id"]):
            if v and len(v.strip()) >= 2:
                needles.add(v.strip())
        digits = re.sub(r"\D", "", r["parent_phone"] or "")
        if len(digits) >= 7:
            needles.add(digits)
    hits = []
    for f in folder.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in (".csv", ".txt", ".md", ".json"):
            continue
        text = f.read_text(encoding="utf-8-sig")
        flat = re.sub(r"\D", "", text)
        for n in needles:
            if (n.isdigit() and n in flat) or (not n.isdigit() and n in text):
                hits.append(f"{f.name}: a real name, ID or phone")
        if PHONE_RE.search(text) or RAW_ID_RE.search(text):
            hits.append(f"{f.name}: a phone number or a raw id")
    return hits


def export(conn: sqlite3.Connection, cfg: Config, out: str | Path, today: date | None = None, seed: int = 7) -> Path:
    from .report import suggest_notes
    today = today or M.kst_today()
    folder = Path(out)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    classes = conn.execute("SELECT * FROM classes ORDER BY id").fetchall()
    alias = {c["name"]: f"Class {i + 1}" for i, c in enumerate(classes)}
    try:
        rows = MT.weekly(conn, cfg, None, 8, today)
        for r in rows:
            r["class"] = "All classes"
        MT.write_csv(rows, folder / "metrics_all.csv")
        for c in classes:
            rows = MT.weekly(conn, cfg, c["name"], 8, today)
            for r in rows:
                r["class"] = alias[c["name"]]
            MT.write_csv(rows, folder / f"metrics_{alias[c['name']].replace(' ', '_').lower()}.csv")
        for name in SCREENSHOTS:
            src = ROOT / "demo" / "screenshots" / name
            if src.exists():
                shutil.copy2(src, folder / name)
        # 12 example comments: drafts from the data, shuffled, each under a generated name (never the real one)
        rng = random.Random(seed)
        drafts = []
        for c in classes:
            drafts += [(alias[c["name"]], d) for _, _, d in suggest_notes(conn, cfg, c, today, include_existing=True)]
        rng.shuffle(drafts)
        names = SYNTH_GIVEN[:]
        rng.shuffle(names)
        lines = ["Example weekly comments (synthetic names; drafted by DAYBOOK from class data)", ""]
        for i, (cls, text) in enumerate(drafts[:12]):
            lines.append(f"{i + 1}. {names[i % len(names)]} ({cls}): {text}")
        (folder / "comments.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
        (folder / "README.txt").write_text(
            "DAYBOOK portfolio (synthetic). Metrics are class level only; class names are replaced; the screenshots show\n"
            "the synthetic demo; the comment names are generated. No real student appears in this folder.\n",
            encoding="utf-8", newline="")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    hits = _leaks(conn, folder)
    if hits:
        shutil.rmtree(folder, ignore_errors=True)
        raise PortfolioError("stopped, nothing kept: " + "; ".join(sorted(set(hits))[:5]))
    return folder
