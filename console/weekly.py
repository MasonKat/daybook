"""`daybook weekly`: paste-ready Korean weekly messages (from templates/weekly.txt). Console output only."""
from __future__ import annotations

import sqlite3
from datetime import date

from jinja2 import Environment, FileSystemLoader

from . import generate
from .config import Config


def messages(conn: sqlite3.Connection, cfg: Config, cls: sqlite3.Row, day: date) -> list[str]:
    ctx = generate.build_context(conn, cfg, day, generate.M.kst_now())
    conn.rollback()  # build_context may freeze a reward goal; weekly must not write
    S = ctx["S"]
    env = Environment(loader=FileSystemLoader(str(generate.TEMPLATES)), autoescape=False,
                      trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=False)
    tpl = env.get_template("weekly.txt")
    ids = {r["id"] for r in conn.execute("SELECT id FROM students_public WHERE class_id = ? AND active = 1", (cls["id"],))}
    out = []
    for s in ctx["students"]:
        if s["id"] not in ids:
            continue
        week = s["week"]
        t = s["trend"] if s["share_tests"] else None     # F21.1: no test results unless the student and the parent agreed
        out.append(tpl.render(
            S=S, name=s["display_name"], week_label=ctx["week_label"],
            n_sub=sum(1 for d in week if d["done"]), n_class=sum(1 for d in week if d["class_day"] and not d["future"]),
            streak=s["streak"], week_sum=s["week_sum"], cumulative=s["cumulative"], trend=t is not None,
            trend_eff=S["dir_" + t["lines"]["eff"].dir] if t else "",
            trend_reach=S["dir_" + t["lines"]["reach"].dir] if t else "",
            note=s["note"], link=f"{cfg.pages}/p/{s['parent_token']}/" if cfg.pages else "",
        ).strip())
    return out
