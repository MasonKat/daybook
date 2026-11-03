"""Static site generator. Reads ONLY `students_public` (never the private student columns).

publish(): compute derived values -> render into a staging dir -> run the no-PII lint ->
swap into dist/ only if the lint passes. A failing lint leaves the previous dist/ untouched.
"""
from __future__ import annotations

import json
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
              "upload_rate", "upload_offline", "replaced", "no_worker", "today_set", "weekdays")


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


def load_videos(cfg: Config) -> list[dict]:
    p = Path(cfg.videos_path)
    if not p.is_absolute():
        p = Path.cwd() / p
        if not p.exists():
            p = ROOT / cfg.videos_path
    if not p.exists():
        return []
    items = json.loads(p.read_text(encoding="utf-8"))
    out = [
        {"title": str(v.get("title", "")), "url": str(v.get("url", "")), "group": str(v.get("group", "")),
         "added": str(v.get("added", ""))}
        for v in items if str(v.get("url", "")).startswith(("https://", "http://"))
    ]
    out.sort(key=lambda v: v["added"], reverse=True)
    groups: dict[str, list] = {}
    for v in out:
        groups.setdefault(v["group"] or "", []).append(v)
    return [{"group": g, "items": vs} for g, vs in groups.items()]


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

    students = []
    for s in conn.execute("SELECT * FROM students_public WHERE active = 1 ORDER BY id"):
        cls = classes.get(s["class_id"])
        subs = {r["day"]: r for r in conn.execute(
            "SELECT day, ts, source FROM submissions WHERE student_id = ? AND status != 'deleted'", (s["id"],))}
        sub_days = {date.fromisoformat(d) for d in subs}
        cd = cdays.get(s["class_id"], set())
        days_upto_today = {d for d in cd if d <= today}
        week = []
        for i in range(7):
            d = mon + timedelta(days=i)
            sub = subs.get(d.isoformat())
            week.append({"date": d.isoformat(), "wd": wk[i], "label": f"{d.month}/{d.day}",
                         "done": sub is not None, "time": _fmt_time(sub["ts"]) if sub else "",
                         "class_day": d in cd, "future": d > today})
        pts = conn.execute(
            """SELECT COALESCE(SUM(a.points),0) AS cum,
                      COALESCE(SUM(CASE WHEN se.date BETWEEN ? AND ? THEN a.points END),0) AS wk
               FROM attitude a JOIN sessions se ON se.id = a.session_id WHERE a.student_id = ?""",
            (mon.isoformat(), sun.isoformat(), s["id"]),
        ).fetchone()
        tests = conn.execute(
            "SELECT date, test_name, reach, efficiency FROM tests WHERE student_id = ? ORDER BY date, id",
            (s["id"],)).fetchall()
        note = conn.execute(
            "SELECT text, week FROM notes WHERE student_id = ? ORDER BY week DESC, ts DESC, id DESC LIMIT 1",
            (s["id"],)).fetchone()
        yesterday = today - timedelta(days=1)
        t_start, t_end = M.parse_term(cls["term"]) if cls else (None, None)
        rw = rewards.get(s["class_id"])
        js = {
            "worker": cfg.worker,
            "pub": today.isoformat(),
            # streak through yesterday (closed days); the page adds today + days after publish
            "streakPrev": M.streak(sub_days, days_upto_today, yesterday, today_open=False),
            "subDays": sorted(d.isoformat() for d in sub_days if d >= today - timedelta(days=21)),
            "mask": cls["weekday_mask"] if cls else 0,
            "termStart": t_start.isoformat() if t_start else "",
            "termEnd": t_end.isoformat() if t_end else "",
            "classTotal": rw.total if rw else 0,
            "rewardLabel": rw.label if rw else "",
            "toGo": rw.to_go if rw else 0,
            "weekStart": mon.isoformat(),
            "S": {k: S[k] for k in JS_STRINGS},
        }
        students.append({
            "id": s["id"],
            "display_name": s["display_name"],
            "class_name": cls["name"] if cls else "",
            "student_token": s["student_token"],
            "parent_token": s["parent_token"],
            "week": week,
            "streak": M.streak(sub_days, days_upto_today, today),
            "week_sum": int(pts["wk"]),
            "cumulative": int(pts["cum"]),
            "trend": _trend(tests),
            "note": note["text"] if note else "",
            "reward": rw,
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
           f"img-src 'self' blob: data:; connect-src {connect}; base-uri 'none'; form-action 'none'; "
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
    st, pt, gt, it = (e.get_template(n) for n in ("student.html", "parent.html", "go.html", "index.html"))
    for s in ctx["students"]:
        sd = out / "s" / s["student_token"]
        sd.mkdir(parents=True)
        (sd / "index.html").write_text(st.render(ctx, me=s, page="student"), encoding="utf-8")
        manifest = {
            "name": ctx["S"]["page_title"], "short_name": ctx["S"]["app_name"], "lang": "ko",
            "start_url": "./#today", "scope": "./", "display": "standalone",
            "background_color": "#ffffff", "theme_color": "#2563eb",
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
