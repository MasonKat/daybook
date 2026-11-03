"""Static site generator. Reads ONLY `students_public` (never the private student columns).

publish(): compute derived values -> render into a staging dir -> run the no-PII lint ->
swap into dist/ only if the lint passes. A failing lint leaves the previous dist/ untouched.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import lint as lintmod
from . import watch as W
from . import models as M
from .config import ROOT, Config, strings
from .db import log_event, now_utc_iso

TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"

SVG_W, SVG_H = 320, 150
JS_STRINGS = ("wt_todo", "wt_done", "wt_queued", "wt_err", "wt_closed", "wt_hard_max", "wt_edit", "wt_open", "wt_title", "done_title", "streak_label", "class_plus", "reward_to_go", "uploading", "upload_error",
              "upload_rate", "upload_offline", "replaced", "no_worker", "today_set", "set_default", "weekdays", "story_streak",
              "recorded_at", "recorded", "ritual_class", "ritual_study", "study_cta", "end_cta", "ck_done", "ck_bad",
              "ck_closed", "ck_none", "ck_rate", "ck_error", "chip_ok", "chip_confused", "chip_rewatch", "day_class",
              "day_study", "day_class_m", "bio", "pad_title", "pad_taste", "live_pad", "live_noroom", "live_wait",
              "live_sent", "live_right", "live_wrong", "live_closed", "live_class", "live_class_c", "live_taste",
              "queued_title", "queued_body", "story_moments", "react_fire", "react_clap", "react_mind")
YT_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
WEEKDAYS_FULL = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
PARENT_WEEKS = 8      # weeks kept in the parent room
TILE_DAYS_MAX = 180   # profile grid: at most this many day tiles


def json_script(obj) -> str:
    """JSON safe to embed inside <script type="application/json">."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
PAD_L, PAD_R, PAD_T, PAD_B = 34, 14, 12, 26


def env() -> Environment:
    e = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]),
                    trim_blocks=True, lstrip_blocks=True)
    e.filters["fmt"] = lambda s, **kw: s.format(**kw)
    return e


def set_hash(set_id: str) -> str:
    """FNV-1a 32-bit, base 36 (same as fnv() in app.js): the page maps ?set= to a label without containing the raw id."""
    h = 0x811C9DC5
    for ch in set_id:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return _b36(h)


def _b36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if n == 0:
            return out


def _resolve(cfg_path: str) -> Path | None:
    p = Path(cfg_path)
    if not p.is_absolute():
        p = Path.cwd() / p
        if not p.exists():
            p = ROOT / cfg_path
    return p if p.exists() else None


def set_labels(conn: sqlite3.Connection, cfg: Config, class_id: int | None) -> dict[str, str]:
    """{hash(set_id): label} for the student page. Label = assignments.json, else the session topic,
    else "{M/D} 과제" from the session date. Unknown sets get "{today} 과제" in the browser."""
    S = strings()
    out: dict[str, str] = {}
    if class_id is not None:
        for r in conn.execute("SELECT date, set_id, topic FROM sessions WHERE class_id = ? AND set_id IS NOT NULL "
                              "ORDER BY date", (class_id,)):
            d = date.fromisoformat(r["date"])
            out[set_hash(r["set_id"])] = (r["topic"] or "").strip() or S["set_default"].format(md=f"{d.month}/{d.day}")
    p = _resolve(cfg.assignments_path)
    if p is not None:
        for sid, label in json.loads(p.read_text(encoding="utf-8")).items():
            if isinstance(label, str) and label.strip() and not sid.startswith("_"):
                out[set_hash(sid)] = label.strip()
    return out


def videos_file(cfg: Config) -> Path | None:
    p = Path(cfg.videos_path)
    if not p.is_absolute():
        p = Path.cwd() / p
        if not p.exists():
            p = ROOT / cfg.videos_path
    return p if p.exists() else None


def _tags(v) -> list[str]:
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in (v or []) if str(x).strip()] if isinstance(v, list) else []


def video_files(cfg: Config) -> list[Path]:
    """F10: `videos_path` is one JSON file or a folder whose *.json files are merged (one per source, e.g.
    solutions.json, shorts.json, concepts.json), in name order."""
    p = videos_file(cfg)
    if p is None:
        return []
    return sorted(f for f in p.glob("*.json") if f.is_file()) if p.is_dir() else [p]


def load_videos(cfg: Config) -> list[dict]:
    """videos.json: [{title, url, group, added, kind?, yt_id?, thumb?, duration?, classes?, grades?, pin?}]
    -> flat list, newest first.

    `kind` is "short" (vertical, shown as reels) or "long" (default; shown as 16:9 cards).
    `thumb` is a local image path (relative to its JSON file); it is shrunk into dist/t/<id>.jpg at publish.
    F10: `classes` (e.g. ["고1A"]) and/or `grades` (e.g. ["고1"]) limit an item to matching students; an item with
    neither is for everyone. `pin: true` keeps it on top of the 강의 list for its students. The same URL in two files
    is kept once (the first file wins).
    """
    out, seen = [], set()
    for p in video_files(cfg):
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for v in items if isinstance(items, list) else []:
            if not isinstance(v, dict):
                continue
            item = _video_item(p, v)
            if item and item["url"] not in seen:
                seen.add(item["url"])
                out.append(item)
    out.sort(key=lambda v: v["added"], reverse=True)
    return out


def _video_item(p: Path, v: dict) -> dict | None:
    url = str(v.get("url", ""))
    if not url.startswith(("https://", "http://")):
        return None
    yt = str(v.get("yt_id", "") or "")
    yt = yt if YT_RE.match(yt) else ""
    vid = yt or hashlib.sha1(url.encode()).hexdigest()[:12]
    thumb_src = None
    if v.get("thumb"):
        cand = (p.parent / str(v["thumb"])).resolve()
        thumb_src = cand if cand.is_file() else None
    kind = "short" if str(v.get("kind", "")).lower() == "short" else "long"
    dur = str(v.get("duration", "") or "")
    return {"id": vid, "title": str(v.get("title", "")), "url": url, "group": str(v.get("group", "")),
            "added": str(v.get("added", "")), "yt_id": yt, "thumb_src": thumb_src, "kind": kind,
            "duration": dur if re.fullmatch(r"\d{1,2}(:\d{2}){1,2}", dur) else "",
            "thumb": f"/t/{vid}.jpg" if thumb_src else "",
            "classes": _tags(v.get("classes")), "grades": _tags(v.get("grades")), "pin": v.get("pin") is True,
            # F15.2: YouTube's own counters, filled by the local agent; the Teacher tab shows them, pages never do
            "views": v.get("views") if isinstance(v.get("views"), int) else None,
            "views_7d": v.get("views_7d") if isinstance(v.get("views_7d"), int) else None}


def video_for(v: dict, cls) -> bool:
    """F10: an untagged item is for everyone; a tagged one only for a matching class or grade."""
    if not v["classes"] and not v["grades"]:
        return True
    if cls is None:
        return False
    return cls["name"] in v["classes"] or bool(cls["grade"] and cls["grade"] in v["grades"])


def video_tag_warnings(conn: sqlite3.Connection, videos: list[dict]) -> list:
    """F10: an unknown class or grade in `classes`/`grades` is a publish warning (the item shows to nobody)."""
    names = {r["name"] for r in conn.execute("SELECT name FROM classes")}
    grades = {r["grade"] for r in conn.execute("SELECT grade FROM classes WHERE grade IS NOT NULL AND grade != ''")}
    out = []
    for v in videos:
        bad = [c for c in v["classes"] if c not in names] + [g for g in v["grades"] if g not in grades]
        if bad:
            out.append(lintmod.Finding("warning", "videos", "video_tag", f"{v['title']!r}: unknown {', '.join(bad)}"))
    return out


@dataclass
class TrendLine:
    values: list[float]
    dir: str
    points: str = ""
    dots: list[tuple[float, float]] = field(default_factory=list)


BAND_MIN_STUDENTS = 5   # the class band is drawn only when a round has at least this many students


def _quartiles(vals: list[float]) -> tuple[float, float]:
    v = sorted(vals)

    def q(p):
        k = (len(v) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(v) - 1)
        return v[lo] + (v[hi] - v[lo]) * (k - lo)
    return q(0.25), q(0.75)


def _trend(rows: list[sqlite3.Row], class_rounds: dict | None = None) -> dict | None:
    """The student's last rounds; `class_rounds[(test_name, date)] = {"eff": [...], "reach": [...]}` adds the
    class's middle-50 % band (never another student's point) when every shown round has >= BAND_MIN_STUDENTS."""
    rows = rows[-M.TREND_ROUNDS:]
    if not rows:
        return None
    eff = [r["efficiency"] for r in rows]
    reach = [r["reach"] for r in rows]
    n = len(rows)
    xs = [PAD_L + (SVG_W - PAD_L - PAD_R) * (0.5 if n == 1 else i / (n - 1)) for i in range(n)]

    def y(v: float) -> float:
        v = max(0.0, min(1.0, v))
        return PAD_T + (SVG_H - PAD_T - PAD_B) * (1 - v)

    lines = {}
    for key, vals, thr in (("eff", eff, M.EFFICIENCY_THRESHOLD), ("reach", reach, M.REACH_THRESHOLD)):
        pts = [(round(xs[i], 1), round(y(v), 1)) for i, v in enumerate(vals) if v is not None]
        lines[key] = TrendLine(vals, M.direction(vals, thr), " ".join(f"{a},{b}" for a, b in pts), pts)
    labels = [(round(xs[i], 1), parse_short(r["date"]),
               "middle" if n == 1 else "start" if i == 0 else "end" if i == n - 1 else "middle")
              for i, r in enumerate(rows)]
    grid = [(PAD_T + (SVG_H - PAD_T - PAD_B) * (1 - g), f"{int(g * 100)}") for g in (0, 0.5, 1)]
    bands = []
    if class_rounds is not None and n >= 2:
        stats = [class_rounds.get((r["test_name"], r["date"])) for r in rows]
        if all(st and len(st["eff"]) >= BAND_MIN_STUDENTS for st in stats):
            for key in ("eff", "reach"):
                qs = [_quartiles([x for x in st[key] if x is not None]) if any(x is not None for x in st[key]) else None
                      for st in stats]
                if all(qs):
                    top = [f"{round(xs[i], 1)},{round(y(qs[i][1]), 1)}" for i in range(n)]
                    bot = [f"{round(xs[i], 1)},{round(y(qs[i][0]), 1)}" for i in reversed(range(n))]
                    bands.append({"key": key, "points": " ".join(top + bot)})
    return {"lines": lines, "labels": labels, "grid": grid, "w": SVG_W, "h": SVG_H, "x0": PAD_L,
            "x1": SVG_W - PAD_R, "ylab": SVG_H - 8, "bands": bands}


def parse_short(d: str) -> str:
    dd = date.fromisoformat(d)
    return f"{dd.month}/{dd.day}"


def _fmt_time(ts: str) -> str:
    return M.utc_iso_to_kst(ts).strftime("%H:%M")


def _date_ko(d: date) -> str:
    return f"{d.month}월 {d.day}일"


def _date_ko_full(d: date) -> str:
    return f"{d.month}월 {d.day}일 {WEEKDAYS_FULL[d.weekday()]}"


def _hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _sum_upto(rows: list[tuple[date, int]], start: date | None, end: date) -> int:
    return sum(p for d, p in rows if d <= end and (start is None or d >= start))


def _week_of_month(mon: date) -> tuple[int, int]:
    """Korean "M월 N주": the month that holds the week's Thursday, N = which Thursday of that month."""
    thu = mon + timedelta(days=3)
    return thu.month, (thu.day - 1) // 7 + 1


def _md(d: date) -> str:
    return f"{d.month}/{d.day}"


def _publish_times(conn: sqlite3.Connection) -> dict[date, datetime]:
    """The last successful publish of each day (from the append-only event log), in KST."""
    out: dict[date, datetime] = {}
    for r in conn.execute("SELECT payload_json FROM events WHERE type = 'publish' ORDER BY id"):
        p = json.loads(r["payload_json"])
        try:
            out[date.fromisoformat(p["date"])] = M.utc_iso_to_kst(p["at"])
        except (KeyError, ValueError):
            continue
    return out


def _week_days(mon: date, subs: dict, cd: set, today: date, wk: list[str]) -> list[dict]:
    out = []
    for i in range(7):
        d = mon + timedelta(days=i)
        sub = subs.get(d.isoformat()) if d <= today else None
        out.append({"date": d.isoformat(), "wd": wk[i], "label": f"{d.month}/{d.day}",
                    "done": sub is not None, "time": _fmt_time(sub["ts"]) if sub else "",
                    "class_day": d in cd, "future": d > today})
    return out


LOCAL_ONLY_STATIC = {"teacher.js", "live.js", "sw.js", "flags.js"}
TASTE_SLUG = "콩"      # the demo student shown in the taste build (F1)
TASTE_PATH = "kong"   # fixed paths /s/kong/ and /p/kong/


CHIP_KEYS = ("ok", "confused", "rewatch")


def _solved(subs: dict, start: date, end: date) -> int | None:
    """F13.5: the week's self-reported 푼 문제 수 (None when the student never entered one that week)."""
    vals = [r["solved"] for d, r in subs.items() if start.isoformat() <= d <= end.isoformat() and r["solved"] is not None]
    return sum(vals) if vals else None


def _review(insights, mon: date, sun: date, scrub, videos: list[dict], S: dict) -> dict | None:
    """F12 "이번 주 복습" story: this week's latest insight -> <= 3 focus chips, <= 3 videos (matched to videos.json,
    else plain links) and the student's line. None (no ring) when there is nothing for this week."""
    week = [r for r in insights if mon.isoformat() <= r["date"] <= sun.isoformat()]
    if not week:
        return None
    r = week[-1]
    focus = [f for f in (scrub(x) for x in json.loads(r["focus"] or "[]")) if f][:3]
    by_url = {v["url"]: v for v in videos}
    items = []
    for u in json.loads(r["videos"] or "[]")[:3]:
        if lintmod.RAW_ID_RE.search(u):
            continue
        items.append(by_url.get(u) or {"title": S["review_link"], "url": u, "thumb": "", "kind": "long", "group": ""})
    line = scrub(r["student_line"])
    if not (focus or items or line):
        return None
    return {"key": "review", "label": S["story_review"], "focus": focus, "items": items, "line": line}


def class_hours(cls, S: dict) -> str:
    """F13.5: "수업 17:30–20:00 · 자습 ~21:00" for the parent card (empty when the class has no hours set)."""
    if not cls or not cls["start_time"]:
        return ""
    out = S["hours_class"].format(s=cls["start_time"], e=cls["end_time"] or "")
    return out + (" · " + S["hours_self"].format(t=cls["self_study_until"]) if cls["self_study_until"] else "")


def build_context(conn: sqlite3.Connection, cfg: Config, today: date, now: datetime, taste: bool = False,
                  scrub=None) -> dict:
    """`scrub` (from lint.text_scrubber) filters text students wrote; without it student lines are left out."""
    scrub = scrub or (lambda _t: "")
    S = strings()
    wk = S["weekdays"].split(",")
    mon, sun = M.week_bounds(today)
    classes = {r["id"]: r for r in conn.execute("SELECT * FROM classes")}
    rewards, cdays = {}, {}
    for cid, cls in classes.items():
        rewards[cid] = M.reward_for(conn, cls, today, freeze=True)
        cdays[cid] = M.class_days(conn, cls, upto=sun)
    # Re-read classes: reward_for may have frozen a goal.
    classes = {r["id"]: r for r in conn.execute("SELECT * FROM classes")}
    videos = load_videos(cfg)

    class_att: dict[int, list[tuple[date, int]]] = {}
    for cid in classes:
        class_att[cid] = [(date.fromisoformat(r["date"]), r["points"]) for r in conn.execute(
            """SELECT se.date, a.points FROM attitude a JOIN sessions se ON se.id = a.session_id
               JOIN students_public s ON s.id = a.student_id WHERE s.class_id = ? AND s.active = 1""", (cid,))]
    from .ops import class_key
    class_moments: dict[int, list[dict]] = {}
    for r in conn.execute("""SELECT m.*, se.date FROM moments m JOIN sessions se ON se.id = m.session_id
                             WHERE se.date <= ? ORDER BY se.date, m.id""", (today.isoformat(),)):
        lst = class_moments.setdefault(r["class_id"], [])
        n = sum(1 for x in lst if x["date"] == r["date"]) + 1
        ck = class_key(r["class_id"])
        d0 = date.fromisoformat(r["date"])
        lst.append({"date": r["date"], "n": n, "k": f"{ck}:{r['date']}:{n}", "text": r["text"] or "",
                    "src": f"/m/{ck}/{r['date']}_{n}.jpg", "image_key": r["image_key"],
                    "label": f"{d0.month}/{d0.day} ({wk[d0.weekday()]})"})
    class_rounds: dict[int, dict] = {}
    for r in conn.execute("""SELECT s.class_id, t.test_name, t.date, t.efficiency, t.reach FROM tests t
                             JOIN students_public s ON s.id = t.student_id WHERE s.active = 1"""):
        st = class_rounds.setdefault(r["class_id"], {}).setdefault((r["test_name"], r["date"]), {"eff": [], "reach": []})
        st["eff"].append(r["efficiency"])
        st["reach"].append(r["reach"])
    week_ago = (today - timedelta(days=6)).isoformat()
    shorts = [v for v in videos if v["kind"] == "short"]
    longs = [v for v in videos if v["kind"] == "long"]
    published_at = _publish_times(conn)
    show_late = bool(cfg.extra.get("show_late", False))
    show_lines = bool(cfg.extra.get("parent_shows_reflection", False))   # off by default: students write honestly
    show_videos = bool(cfg.extra.get("parent_shows_videos", True))       # F15.1: the parent card's 영상 count
    # F15.2: each class's 인기 video (>= 3 different students over popular_days); never a name or a number on a page
    pop_days = 14 if int(cfg.extra.get("popular_days", 7) or 7) == 14 else 7
    popular = {cid: W.popular(conn, cid, today, pop_days) for cid in classes}
    pop_pin = cfg.extra.get("popular_pin", True) is not False

    students = []
    only = " AND slug = ?" if taste else ""
    for s in conn.execute("SELECT * FROM students_public WHERE active = 1" + only + " ORDER BY id",
                          (TASTE_SLUG,) if taste else ()):
        cls = classes.get(s["class_id"])
        subs = {r["day"]: r for r in conn.execute(
            "SELECT day, ts, source, reflection, chip, solved FROM submissions WHERE student_id = ? AND status != 'deleted'",
            (s["id"],))}
        lines = []
        for dd in sorted(subs, reverse=True):
            r = subs[dd]
            text = scrub(r["reflection"])
            if (text or r["chip"] in CHIP_KEYS) and date.fromisoformat(dd) <= today:
                d0 = date.fromisoformat(dd)
                lines.append({"date": dd, "label": f"{d0.month}/{d0.day} ({wk[d0.weekday()]})", "text": text,
                              "chip": S["chip_" + r["chip"]] if r["chip"] in CHIP_KEYS else ""})
        sub_days = {date.fromisoformat(d) for d in subs if date.fromisoformat(d) <= today}
        cd = cdays.get(s["class_id"], set())
        cancelled = M.cancelled_days(conn, s["class_id"]) if s["class_id"] is not None else {}
        days_upto_today = {d for d in cd if d <= today}
        week = _week_days(mon, subs, cd, today, wk)
        att = [(date.fromisoformat(r["date"]), r["points"]) for r in conn.execute(
            "SELECT se.date, a.points FROM attitude a JOIN sessions se ON se.id = a.session_id WHERE a.student_id = ?",
            (s["id"],))]
        three_days = {d for d, p in att if p == 3}
        checkins = {r["date"]: r for r in conn.execute(
            """SELECT se.date, se.start_time, c.ts FROM checkins c JOIN sessions se ON se.id = c.session_id
               WHERE c.student_id = ?""", (s["id"],))}
        tests = conn.execute(
            "SELECT date, test_name, reach, efficiency FROM tests WHERE student_id = ? ORDER BY date, id",
            (s["id"],)).fetchall()
        notes = conn.execute(
            "SELECT text, week FROM notes WHERE student_id = ? ORDER BY week, ts, id", (s["id"],)).fetchall()
        note = notes[-1]["text"] if notes else ""
        yesterday = today - timedelta(days=1)
        t_start, t_end = M.parse_term(cls["term"]) if cls else (None, None)
        rw = rewards.get(s["class_id"])
        streak = M.streak(sub_days, days_upto_today, today)
        cumulative = _sum_upto(att, None, today)

        # profile grid: one tile per day of the term so far, newest first, a month header where a month starts
        first = t_start or min(sub_days | {date.fromisoformat(s["created_at"][:10])})
        first = max(first, today - timedelta(days=TILE_DAYS_MAX - 1))
        tiles, months = [], []
        d = today
        while d >= first:
            if not months or months[-1]["key"] != f"{d.year}-{d.month:02d}":
                months.append({"key": f"{d.year}-{d.month:02d}", "label": S["month"].format(m=d.month),
                               "n": 0})
                tiles.append({"month": months[-1]["label"], "key": months[-1]["key"]})
            st = ("hl" if d in three_days else "sub" if d in sub_days else "open" if d == today
                  else "miss" if d in cd else "cancel" if d in cancelled else "off")
            if d in sub_days:
                months[-1]["n"] += 1
            tiles.append({"date": d.isoformat(), "label": f"{d.month}/{d.day}", "wd": wk[d.weekday()],
                          "st": st, "sub": d in sub_days, "heart": d in three_days,
                          "note": (cancelled.get(d) or "") if st == "cancel" else ""})
            d -= timedelta(days=1)
        week_k = sum(1 for x in week if x["done"])

        # F12: the teacher's own analysis, student-safe slice (scrubbed; the round id never reaches a page)
        insights = conn.execute("SELECT * FROM insights WHERE student_id = ? ORDER BY date, ts", (s["id"],)).fetchall()
        # F10: this student's videos (untagged + their class / grade), pinned 강의 first
        hot = popular.get(s["class_id"])
        my_videos = [dict(v, hot=True) if v["id"] == hot else v for v in videos if video_for(v, cls)]
        my_shorts = [v for v in my_videos if v["kind"] == "short"]
        my_longs = sorted((v for v in my_videos if v["kind"] == "long"), key=lambda v: not v["pin"])
        if pop_pin:                                     # the 인기 video first in its own list (sorts are stable)
            my_shorts.sort(key=lambda v: not v.get("hot"))
            my_longs.sort(key=lambda v: not v.get("hot"))
        hot_v = next((v for v in my_videos if v.get("hot")), None)
        new_videos = [v for v in my_videos if v["added"] and v["added"] >= week_ago]
        newest_thumb = next((v["thumb"] for v in (new_videos or my_videos) if v["thumb"]), "")
        # stories (own data only; 오늘의 장면 is class-wide, never another student's)
        stories = []
        week_moments = [m for m in class_moments.get(s["class_id"], [])
                        if (today - timedelta(days=6)).isoformat() <= m["date"] <= today.isoformat()]
        stories.append({"key": "moments", "label": S["story_moments"], "items": week_moments,
                        "thumb": week_moments[-1]["src"] if week_moments else "", "hidden": not week_moments})
        if new_videos:
            stories.append({"key": "videos", "label": S["story_new_videos"], "items": new_videos,
                            "thumb": newest_thumb})
        if hot_v:
            stories.append({"key": "popular", "label": S["story_popular"], "items": [hot_v], "thumb": hot_v["thumb"]})
        stories.append({"key": "streak", "label": S["story_streak"].format(n=streak), "streak": streak,
                        "pct": round(100 * week_k / 7)})
        if rw and rw.goal > 0:
            nxt = next((m for m in rw.milestones + [rw.goal] if m > rw.total), None)
            stories.append({"key": "goal", "label": S["story_goal"], "next": (nxt - rw.total) if nxt else 0,
                            "pct": round(rw.pct)})
        review = _review(insights, mon, sun, scrub, videos, S)
        if review:
            stories.append(review)
        if note:
            stories.append({"key": "teacher", "label": S["story_teacher"], "note": note,
                            "week": _date_ko(M.week_monday(notes[-1]["week"]))})
        for st_ in stories:
            content = {k: v for k, v in st_.items() if k not in ("label", "streak", "pct", "thumb")}
            if st_["key"] in ("videos", "popular"):
                content = [v["id"] for v in st_["items"]]
            if st_["key"] == "moments":
                content = [m["k"] for m in st_["items"]]
            if st_["key"] == "review":
                content = [st_["focus"], [v["url"] for v in st_["items"]], st_["line"]]
            st_["hash"] = _hash([st_["key"], content, rw.total if st_["key"] == "goal" and rw else 0])

        # parent room: one report message per week (last PARENT_WEEKS weeks), newest open, earlier ones one line
        room = []
        wm = M.week_bounds(t_start)[0] if t_start else M.week_bounds(first)[0]
        wm = max(wm, mon - timedelta(weeks=PARENT_WEEKS - 1))
        while wm <= mon:
            we = wm + timedelta(days=6)
            cut = min(we, today)
            current = wm == mon
            wdays = _week_days(wm, subs, cd, today, wk)
            rows = [r for r in tests if r["date"] <= cut.isoformat()]
            trend = _trend(rows, class_rounds.get(s["class_id"], {}))
            last = rows[-1] if rows else None
            wk_notes = [n for n in notes if n["week"] == M.iso_week(wm)]
            total_then = _sum_upto(class_att.get(s["class_id"], []), None, cut)
            class_days_so_far = [x for x in wdays if x["class_day"] and not x["future"]]
            if current:
                stamp, at = today, now
            else:
                pubs = [dd for dd in published_at if wm <= dd <= we]
                stamp = max(pubs) if pubs else wm + timedelta(days=5)
                at = published_at[stamp] if pubs else None
            mth, nth = _week_of_month(wm)
            k = sum(1 for x in wdays if x["done"])
            ritual = []
            for x in wdays:
                ck = checkins.get(x["date"])
                if x["future"] or not (x["class_day"] or ck):
                    continue
                t_in = _fmt_time(ck["ts"]) if ck else ""
                late = bool(show_late and ck and ck["start_time"] and t_in > ck["start_time"])
                if t_in or x["time"]:
                    sub_row = subs.get(x["date"])
                    line = ""
                    if show_lines and sub_row:
                        line = scrub(sub_row["reflection"]) or (S["chip_" + sub_row["chip"]] if sub_row["chip"] in CHIP_KEYS else "")
                    ritual.append({"wd": x["wd"], "in": t_in, "out": x["time"], "late": late, "line": line})
            wsum = _sum_upto(att, wm, cut)
            solved = _solved(subs, wm, cut)
            wk_videos = len(W.watched_ids(conn, s["id"], wm, cut)) if show_videos else 0      # F15.1: a count only
            wk_ins = [r for r in insights if wm.isoformat() <= r["date"] <= we.isoformat()]
            ins_line = scrub(wk_ins[-1]["parent_line"]) if wk_ins else ""
            ins_focus = ([f for f in (scrub(x) for x in json.loads(wk_ins[-1]["focus"] or "[]")) if f]
                         if wk_ins and cfg.extra.get("parent_shows_focus") else [])
            dir_eff = S["dir_" + trend["lines"]["eff"].dir] if trend else ""
            rng = f"{_md(wm)}–{_md(we)}"
            room.append({
                "sep": _date_ko_full(stamp), "current": current, "time": at.strftime("%H:%M") if at else "",
                "title": S["report_title"].format(m=mth, n=nth, range=rng),
                "summary": (S["summary"] if trend else S["summary_no_test"]).format(range=rng, k=k, dir=dir_eff, w=wsum),
                "days": wdays, "k": k, "c": len(class_days_so_far),
                "d": sum(1 for x in class_days_so_far if x["done"]),
                "trend": trend if current else None, "has_tests": trend is not None,
                "eff": round(100 * last["efficiency"]) if last and last["efficiency"] is not None else None,
                "reach": round(100 * last["reach"]) if last and last["reach"] is not None else None,
                "dir_eff": trend["lines"]["eff"].dir if trend else "",
                "dir_reach": trend["lines"]["reach"].dir if trend else "",
                "week_sum": wsum, "cumulative": _sum_upto(att, None, cut), "solved": solved,
                "insight": ins_line, "focus": ins_focus, "videos": wk_videos,
                "bar_pct": (min(100.0, 100.0 * total_then / rw.goal) if rw and rw.goal > 0 and current else None),
                "to_go": max(0, rw.goal - total_then) if rw else 0,
                "note": wk_notes[-1]["text"] if wk_notes else "",
                "ritual": ritual,
                "moments": [m for m in class_moments.get(s["class_id"], [])
                            if wm.isoformat() <= m["date"] <= we.isoformat()][:3],
            })
            wm += timedelta(weeks=1)

        today_sub = subs.get(today.isoformat())
        js = {
            "worker": cfg.worker,
            "pub": today.isoformat(),
            # streak through yesterday (closed days); the page adds today + days after publish
            "streakPrev": M.streak(sub_days, days_upto_today, yesterday, today_open=False),
            "subDays": sorted(x.isoformat() for x in sub_days if x >= today - timedelta(days=21)),
            "nSub": len(sub_days),
            "mask": cls["weekday_mask"] if cls else 0,
            "termStart": t_start.isoformat() if t_start else "",
            "termEnd": t_end.isoformat() if t_end else "",
            "classTotal": rw.total if rw else 0,
            "rewardLabel": rw.label if rw else "",
            "toGo": rw.to_go if rw else 0,
            "weekStart": mon.isoformat(),
            "stories": {st_["key"]: st_["hash"] for st_ in stories},
            "moments": [m["k"] for m in week_moments],
            "momentsFrom": (today - timedelta(days=6)).isoformat(),
            "sets": set_labels(conn, cfg, s["class_id"]),
            "todayTime": _fmt_time(today_sub["ts"]) if today_sub else "",
            "className": cls["name"] if cls else "",
            "classDays": sorted(x.isoformat() for x in cd if x >= today),
            "cancelled": sorted(x.isoformat() for x in cancelled if x >= today - timedelta(days=30)),
            "checkinTime": _fmt_time(checkins[today.isoformat()]["ts"]) if today.isoformat() in checkins else "",
            "taste": taste,
            "S": {k: S[k] for k in JS_STRINGS},
        }
        students.append({
            "id": s["id"],
            "display_name": s["display_name"],
            "initial": s["display_name"][:1],
            "class_name": cls["name"] if cls else "",
            "student_token": s["student_token"],
            "parent_token": s["parent_token"],
            "week": week,
            "streak": streak,
            "n_sub": len(sub_days),
            "week_sum": _sum_upto(att, mon, sun),
            "cumulative": cumulative,
            "trend": _trend(tests),
            "note": note,
            "notes": [{"week": _date_ko(M.week_monday(n["week"])), "text": n["text"]} for n in notes],
            "reward": rw,
            "stories": stories,
            "tiles": tiles,
            "room": room,
            "months": months,
            "lines": lines[:30],
            "bio": S["bio"].format(cls=cls["name"] if cls else "", n=len(sub_days)),
            "solved_week": _solved(subs, mon, today),
            "videos_week": len(W.watched_ids(conn, s["id"], mon, today)),
            "shorts": my_shorts,
            "longs": my_longs,
            "hours": class_hours(cls, S),
            "js": json_script(js),
        })
    return {
        "S": S,
        "students": students,
        "videos": videos,
        "shorts": shorts,
        "longs": longs,
        "today": today,
        "week_label": f"{_date_ko(mon)} – {_date_ko(sun)}",
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "go_js": json.dumps({"s": "/s/"}, separators=(",", ":")),
        "taste": taste,
        "worker": cfg.worker,
        # F15.3: the VAPID public key (not secret) turns on the opt-in box; never in the taste build
        "vapid": "" if taste or not cfg.worker else str(cfg.extra.get("vapid_public") or ""),
    }


def headers_file(cfg: Config) -> str:
    connect = "'self'" + (f" {cfg.worker}" if cfg.worker else "")
    csp = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
           f"img-src 'self' blob: data:; connect-src {connect}; frame-src https://www.youtube-nocookie.com; "
           "base-uri 'none'; form-action 'none'; "
           "frame-ancestors 'none'")
    return (
        "/*\n"
        "  X-Robots-Tag: noindex, nofollow\n"
        "  Referrer-Policy: no-referrer\n"
        "  X-Content-Type-Options: nosniff\n"
        f"  Content-Security-Policy: {csp}\n"
        "/s/*\n  Cache-Control: no-cache\n"
        "/p/*\n  Cache-Control: no-cache\n"
    )


def render_site(conn: sqlite3.Connection, cfg: Config, out: Path, today: date, now: datetime,
                taste: bool = False) -> dict:
    """Render the site into `out`. taste=True: only the demo student 콩 at /s/kong/ and /p/kong/, a landing page,
    no Worker anywhere (uploads and check-ins run in the browser only)."""
    if taste:
        cfg = Config(worker_url="", pages_url="", teacher_token="", videos_path=cfg.videos_path,
                     assignments_path=cfg.assignments_path, moments_dir=cfg.moments_dir,
                     board_poll_seconds=cfg.board_poll_seconds)
    ctx = build_context(conn, cfg, today, now, taste=taste, scrub=lintmod.text_scrubber(conn))
    e = env()
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    for f in STATIC.iterdir():
        if f.is_file() and f.name not in LOCAL_ONLY_STATIC:      # the Teacher tab's script never goes online
            shutil.copy2(f, out / "static" / f.name)
    # F9: served from the root so it may control /s/ (and /p/ for push). The cache name carries a hash of the static
    # files, so every publish that changes app.js / app.css also changes sw.js: phones then fetch the new files instead
    # of keeping a cache-first copy forever.
    shell = hashlib.sha256(b"".join((STATIC / n).read_bytes() for n in ("app.js", "app.css", "sw.js"))).hexdigest()[:10]
    (out / "sw.js").write_text((STATIC / "sw.js").read_text(encoding="utf-8").replace("daybook-shell-v1", f"daybook-shell-{shell}"),
                               encoding="utf-8", newline="")
    if any(v["thumb_src"] for v in ctx["videos"]):
        from .thumbs import make_thumb
        (out / "t").mkdir()
        for v in ctx["videos"]:
            if v["thumb_src"]:
                make_thumb(v["thumb_src"], out / "t" / f"{v['id']}.jpg", max_w=360 if v["kind"] == "short" else 480)
    from .moments import page_copy
    wanted = {m["src"]: m["image_key"] for s_ in ctx["students"] for st_ in s_["stories"] if st_["key"] == "moments"
              for m in st_["items"]}
    wanted.update({m["src"]: m["image_key"] for s_ in ctx["students"] for w in s_["room"] for m in w["moments"]})
    for src, key in wanted.items():
        data = page_copy(cfg, key)
        if data is not None:
            dst = out / src.lstrip("/")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
    st, pt, gt, it = (e.get_template(n) for n in ("student.html", "parent.html", "go.html", "index.html"))
    for s in ctx["students"]:
        sd = out / "s" / (TASTE_PATH if taste else s["student_token"])
        sd.mkdir(parents=True)
        (sd / "index.html").write_text(st.render(ctx, me=s, page="student"), encoding="utf-8")
        manifest = {
            "name": ctx["S"]["page_title"], "short_name": ctx["S"]["app_name"], "lang": "ko",
            "start_url": "./#today", "scope": "./", "display": "standalone",
            "background_color": "#0b0f17", "theme_color": "#2563eb",
            "icons": [{"src": f"/static/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png",
                       "purpose": "any"} for n in (192, 512)],
        }
        (sd / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        pd = out / "p" / (TASTE_PATH if taste else s["parent_token"])
        pd.mkdir(parents=True)
        (pd / "index.html").write_text(pt.render(ctx, me=s, page="parent"), encoding="utf-8")
    if taste:
        (out / "index.html").write_text(e.get_template("taste.html").render(ctx, page="taste"), encoding="utf-8")
    else:
        (out / "go").mkdir()
        (out / "go" / "index.html").write_text(gt.render(ctx, page="go"), encoding="utf-8")
        (out / "index.html").write_text(it.render(ctx, page="index"), encoding="utf-8")
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (out / "_headers").write_text(headers_file(cfg), encoding="utf-8")
    return ctx


@dataclass
class PublishResult:
    ok: bool
    n_students: int
    findings: list
    out: Path


def publish(conn: sqlite3.Connection, cfg: Config, out: Path, today: date | None = None,
            now: datetime | None = None, taste: bool = False) -> PublishResult:
    now = now or M.kst_now()
    today = today or now.date()
    out = Path(out)
    if taste and not conn.execute("SELECT 1 FROM events WHERE type = 'demo_seed' LIMIT 1").fetchone():
        raise ValueError("the taste build only runs on the demo database (daybook --db demo.sqlite demo)")
    staging = out.parent / (".taste-staging" if taste else ".dist-staging")
    ctx = render_site(conn, cfg, staging, today, now, taste=taste)
    findings = lintmod.lint_dir(staging, lintmod.private_rows(conn), ctx["S"]["page_title"])
    findings += video_tag_warnings(conn, ctx["videos"])
    if taste:
        findings += lintmod.lint_taste(staging, lintmod.private_rows(conn), TASTE_SLUG, [cfg.worker] if cfg.worker else [])
        if len(ctx["students"]) != 1:
            findings.append(lintmod.Finding("error", "-", "taste", f"expected 1 demo student, found {len(ctx['students'])}"))
    errs = lintmod.errors(findings)
    if errs:
        shutil.rmtree(staging, ignore_errors=True)
        conn.rollback()  # do not keep a goal frozen by a failed publish
        log_event(conn, "publish_failed", {"errors": len(errs), "kinds": sorted({f.kind for f in errs}), "taste": taste})
        conn.commit()
        return PublishResult(False, len(ctx["students"]), findings, out)
    if out.exists():
        shutil.rmtree(out)
    staging.rename(out)
    log_event(conn, "publish_taste" if taste else "publish",
              {"students": len(ctx["students"]), "date": today.isoformat(), "at": now_utc_iso()})
    conn.commit()
    return PublishResult(True, len(ctx["students"]), findings, out)
