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
from . import models as M
from .config import ROOT, Config, strings
from .db import log_event, now_utc_iso

TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"

SVG_W, SVG_H = 320, 150
JS_STRINGS = ("done_title", "streak_label", "class_plus", "reward_to_go", "uploading", "upload_error",
              "upload_rate", "upload_offline", "replaced", "no_worker", "today_set", "set_default", "weekdays", "story_streak")
YT_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
WEEKDAYS_FULL = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
PARENT_WEEKS = 8      # weeks kept in the parent room
PARENT_OPEN = 2       # most recent weeks shown open; older ones sit in "이전 기록"
TILE_DAYS_MAX = 180   # profile grid: at most this many day tiles


def json_script(obj) -> str:
    """JSON safe to embed inside <script type="application/json">."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
PAD_L, PAD_R, PAD_T, PAD_B = 34, 12, 12, 26


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


def load_videos(cfg: Config) -> list[dict]:
    """videos.json: [{title, url, group, added, yt_id?, thumb?}] -> flat list, newest first.

    `thumb` is a local image path (relative to videos.json); it is shrunk into dist/t/<id>.jpg at publish.
    """
    p = videos_file(cfg)
    if p is None:
        return []
    out = []
    for v in json.loads(p.read_text(encoding="utf-8")):
        url = str(v.get("url", ""))
        if not url.startswith(("https://", "http://")):
            continue
        yt = str(v.get("yt_id", "") or "")
        yt = yt if YT_RE.match(yt) else ""
        vid = yt or hashlib.sha1(url.encode()).hexdigest()[:12]
        thumb_src = None
        if v.get("thumb"):
            cand = (p.parent / str(v["thumb"])).resolve()
            thumb_src = cand if cand.is_file() else None
        out.append({"id": vid, "title": str(v.get("title", "")), "url": url, "group": str(v.get("group", "")),
                    "added": str(v.get("added", "")), "yt_id": yt, "thumb_src": thumb_src,
                    "thumb": f"/t/{vid}.jpg" if thumb_src else ""})
    out.sort(key=lambda v: v["added"], reverse=True)
    return out


@dataclass
class TrendLine:
    values: list[float]
    dir: str
    points: str = ""
    dots: list[tuple[float, float]] = field(default_factory=list)


def _trend(rows: list[sqlite3.Row]) -> dict | None:
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
    labels = [(round(xs[i], 1), parse_short(r["date"])) for i, r in enumerate(rows)]
    grid = [(PAD_T + (SVG_H - PAD_T - PAD_B) * (1 - g), f"{int(g * 100)}") for g in (0, 0.5, 1)]
    return {"lines": lines, "labels": labels, "grid": grid, "w": SVG_W, "h": SVG_H, "x0": PAD_L,
            "x1": SVG_W - PAD_R, "ylab": SVG_H - 8}


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


def _week_days(mon: date, subs: dict, cd: set, today: date, wk: list[str]) -> list[dict]:
    out = []
    for i in range(7):
        d = mon + timedelta(days=i)
        sub = subs.get(d.isoformat()) if d <= today else None
        out.append({"date": d.isoformat(), "wd": wk[i], "label": f"{d.month}/{d.day}",
                    "done": sub is not None, "time": _fmt_time(sub["ts"]) if sub else "",
                    "class_day": d in cd, "future": d > today})
    return out


def build_context(conn: sqlite3.Connection, cfg: Config, today: date, now: datetime) -> dict:
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
    new_videos = [v for v in videos if v["added"] and v["added"] >= (today - timedelta(days=6)).isoformat()]

    students = []
    for s in conn.execute("SELECT * FROM students_public WHERE active = 1 ORDER BY id"):
        cls = classes.get(s["class_id"])
        subs = {r["day"]: r for r in conn.execute(
            "SELECT day, ts, source FROM submissions WHERE student_id = ? AND status != 'deleted'", (s["id"],))}
        sub_days = {date.fromisoformat(d) for d in subs if date.fromisoformat(d) <= today}
        cd = cdays.get(s["class_id"], set())
        days_upto_today = {d for d in cd if d <= today}
        week = _week_days(mon, subs, cd, today, wk)
        att = [(date.fromisoformat(r["date"]), r["points"]) for r in conn.execute(
            "SELECT se.date, a.points FROM attitude a JOIN sessions se ON se.id = a.session_id WHERE a.student_id = ?",
            (s["id"],))]
        three_days = {d for d, p in att if p == 3}
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

        # profile grid: one tile per day of the term so far, newest first (never a photo)
        first = t_start or min(sub_days | {date.fromisoformat(s["created_at"][:10])})
        first = max(first, today - timedelta(days=TILE_DAYS_MAX - 1))
        tiles = []
        d = today
        while d >= first:
            st = ("sub" if d in sub_days else "open" if d == today else "miss" if d in cd else "off")
            tiles.append({"date": d.isoformat(), "label": f"{d.month}/{d.day}", "wd": wk[d.weekday()],
                          "st": st, "heart": d in three_days})
            d -= timedelta(days=1)

        # stories (own data only)
        stories = []
        if new_videos:
            stories.append({"key": "videos", "label": S["story_new_videos"], "items": new_videos})
        stories.append({"key": "streak", "label": S["story_streak"].format(n=streak), "streak": streak})
        if rw and rw.goal > 0:
            nxt = next((m for m in rw.milestones + [rw.goal] if m > rw.total), None)
            stories.append({"key": "goal", "label": S["story_goal"], "next": (nxt - rw.total) if nxt else 0})
        if note:
            stories.append({"key": "teacher", "label": S["story_teacher"], "note": note,
                            "week": _date_ko(M.week_monday(notes[-1]["week"]))})
        for st_ in stories:
            content = {k: v for k, v in st_.items() if k not in ("label", "streak")}
            if st_["key"] == "videos":
                content = [v["id"] for v in st_["items"]]
            st_["hash"] = _hash([st_["key"], content, rw.total if st_["key"] == "goal" and rw else 0])

        # parent room: one group of report cards per week, last PARENT_WEEKS weeks
        room = []
        wm = M.week_bounds(t_start)[0] if t_start else M.week_bounds(first)[0]
        wm = max(wm, mon - timedelta(weeks=PARENT_WEEKS - 1))
        while wm <= mon:
            cut = min(wm + timedelta(days=6), today)
            current = wm == mon
            wdays = _week_days(wm, subs, cd, today, wk)
            rows = [r for r in tests if r["date"] <= cut.isoformat()]
            wk_notes = [n for n in notes if n["week"] == M.iso_week(wm)]
            total_then = _sum_upto(class_att.get(s["class_id"], []), None, cut)
            stamp = today if current else wm + timedelta(days=5)
            room.append({
                "sep": _date_ko_full(stamp), "current": current, "time": now.strftime("%H:%M") if current else "",
                "label": f"{_date_ko(wm)} – {_date_ko(wm + timedelta(days=6))}",
                "days": wdays, "k": sum(1 for x in wdays if x["done"]), "n": (cut - wm).days + 1,
                "trend": _trend(rows), "week_sum": _sum_upto(att, wm, cut), "cumulative": _sum_upto(att, None, cut),
                "bar_pct": (min(100.0, 100.0 * total_then / rw.goal) if rw and rw.goal > 0 else None),
                "to_go": max(0, rw.goal - total_then) if rw else 0,
                "note": wk_notes[-1]["text"] if wk_notes else "",
            })
            wm += timedelta(weeks=1)

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
            "sets": set_labels(conn, cfg, s["class_id"]),
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
            "room_open": room[-PARENT_OPEN:],
            "room_earlier": room[:-PARENT_OPEN],
            "js": json_script(js),
        })
    return {
        "S": S,
        "students": students,
        "videos": videos,
        "today": today,
        "week_label": f"{_date_ko(mon)} – {_date_ko(sun)}",
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "go_js": json.dumps({"s": "/s/"}, separators=(",", ":")),
        "worker": cfg.worker,
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


def render_site(conn: sqlite3.Connection, cfg: Config, out: Path, today: date, now: datetime) -> dict:
    ctx = build_context(conn, cfg, today, now)
    e = env()
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    for f in STATIC.iterdir():
        if f.is_file():
            shutil.copy2(f, out / "static" / f.name)
    if any(v["thumb_src"] for v in ctx["videos"]):
        from .thumbs import make_thumb
        (out / "t").mkdir()
        for v in ctx["videos"]:
            if v["thumb_src"]:
                make_thumb(v["thumb_src"], out / "t" / f"{v['id']}.jpg")
    st, pt, gt, it = (e.get_template(n) for n in ("student.html", "parent.html", "go.html", "index.html"))
    for s in ctx["students"]:
        sd = out / "s" / s["student_token"]
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
        pd = out / "p" / s["parent_token"]
        pd.mkdir(parents=True)
        (pd / "index.html").write_text(pt.render(ctx, me=s, page="parent"), encoding="utf-8")
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
            now: datetime | None = None) -> PublishResult:
    now = now or M.kst_now()
    today = today or now.date()
    out = Path(out)
    staging = out.parent / ".dist-staging"
    ctx = render_site(conn, cfg, staging, today, now)
    findings = lintmod.lint_dir(staging, lintmod.private_rows(conn), ctx["S"]["page_title"])
    errs = lintmod.errors(findings)
    if errs:
        shutil.rmtree(staging, ignore_errors=True)
        conn.rollback()  # do not keep a goal frozen by a failed publish
        log_event(conn, "publish_failed", {"errors": len(errs), "kinds": sorted({f.kind for f in errs})})
        conn.commit()
        return PublishResult(False, len(ctx["students"]), findings, out)
    if out.exists():
        shutil.rmtree(out)
    staging.rename(out)
    log_event(conn, "publish", {"students": len(ctx["students"]), "date": today.isoformat(), "at": now_utc_iso()})
    conn.commit()
    return PublishResult(True, len(ctx["students"]), findings, out)
