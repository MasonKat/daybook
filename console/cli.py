"""`daybook` command line (click). See README for the teacher's weekly routine."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import click

from . import config as configmod
from . import db as dbmod
from . import generate, importers, models as M, ops
from .pull import PullError, http_fetch, pull as do_pull


class Ctx:
    def __init__(self, db_path: str, config_path: str | None):
        self.db_path = db_path
        self.cfg = configmod.load(config_path)
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = dbmod.connect(self.db_path)
            if not dbmod.is_initialized(self._conn):
                raise click.ClickException(f"{self.db_path} has no schema yet: run `daybook init` first")
            dbmod.migrate(self._conn)
        return self._conn


def fail(msg: str):
    raise click.ClickException(msg)


def parse_date(s: str | None) -> date:
    if not s:
        return M.kst_today()
    try:
        return date.fromisoformat(s)
    except ValueError:
        fail(f"bad date {s!r}; use YYYY-MM-DD")


@click.group()
@click.option("--db", "db_path", default=lambda: os.environ.get("DAYBOOK_DB", "daybook.sqlite"), show_default="daybook.sqlite or $DAYBOOK_DB",
              help="SQLite file.")
@click.option("--config", "config_path", default=None, help="Config JSON (default daybook.json or $DAYBOOK_CONFIG).")
@click.pass_context
def cli(ctx, db_path, config_path):
    """DAYBOOK console: student ledger + static parent pages."""
    ctx.obj = Ctx(db_path, config_path)


def utf8_console() -> None:
    """Korean output on any console (Windows cp1252/cp949, a C locale): always write UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def utf8_argv() -> None:
    """POSIX with a non-UTF-8 locale hands Korean arguments over as undecoded bytes; recover them as UTF-8.
    (Windows passes arguments as Unicode already.)"""
    sys.argv = [a.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
                if any("\udc80" <= c <= "\udcff" for c in a) else a for a in sys.argv]


def main():
    utf8_console()
    utf8_argv()
    try:
        cli(standalone_mode=True)
    except ops.OpError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------- init / demo

@cli.command()
@click.pass_obj
def init(obj: Ctx):
    """Create the schema and views (safe to re-run)."""
    conn = dbmod.connect(obj.db_path)
    dbmod.init(conn)
    click.echo(f"schema ready in {obj.db_path}")


@cli.command()
@click.option("--out", default="dist", show_default=True)
@click.option("--force", is_flag=True, help="Re-seed even if the database already has demo data.")
@click.option("--no-publish", is_flag=True, help="Seed only (e.g. before `publish --taste`).")
@click.pass_obj
def demo(obj: Ctx, out, force, no_publish):
    """Seed the synthetic demo tenant (2 classes x 6 students) and publish it."""
    from . import demo as demomod
    conn = dbmod.connect(obj.db_path)
    dbmod.init(conn)
    try:
        demomod.seed(conn, force=force)
    except demomod.DemoError as e:
        fail(str(e))
    demomod.write_demo_videos()
    click.echo(f"moments: {demomod.seed_moments(conn, obj.cfg)} synthetic class scenes")
    rep = importers.import_screeners(conn, configmod.ROOT / "demo" / "screeners")
    click.echo(f"screeners: {rep.imported} rows imported from {rep.files} rounds; unmatched: {len(rep.unmatched)}")
    for u in rep.unmatched:
        click.echo(f"  unmatched: {u}")
    if no_publish:
        return
    res = generate.publish(conn, obj.cfg, Path(out))
    _report_publish(res)
    if not res.ok:
        sys.exit(1)


# ---------------------------------------------------------------- classes

@cli.group("class")
def class_():
    """Create and list classes."""


@class_.command("add")
@click.argument("name")
@click.option("--term", required=True, help="START..END, e.g. 2026-11-02..2027-02-27")
@click.option("--days", required=True, help="Class weekdays, e.g. 화,금 or tue,fri")
@click.option("--reward", "reward_label", default="", help="Reward label shown on the class bar")
@click.option("--goal", type=int, default=None, help="Fixed reward goal (default: computed and frozen at publish)")
@click.option("--coef", type=float, default=2.4, show_default=True)
@click.pass_obj
def class_add(obj: Ctx, name, term, days, reward_label, goal, coef):
    if M.parse_term(term) == (None, None):
        fail("--term must be START..END with ISO dates")
    try:
        mask = M.mask_from_days(days)
    except ValueError as e:
        fail(str(e))
    conn = obj.conn
    if conn.execute("SELECT 1 FROM classes WHERE name = ?", (name,)).fetchone():
        fail(f"class {name!r} exists")
    cur = conn.execute(
        "INSERT INTO classes (name, term, weekday_mask, reward_label, reward_goal_points, goal_coef) VALUES (?,?,?,?,?,?)",
        (name, term, mask, reward_label, goal, coef))
    dbmod.log_event(conn, "class_add", {"class_id": cur.lastrowid, "name": name})
    conn.commit()
    click.echo(f"class {name} added")


@class_.command("edit")
@click.argument("name")
@click.option("--term", default=None)
@click.option("--days", default=None)
@click.option("--reward", "reward_label", default=None)
@click.option("--goal", type=int, default=None, help="Set the goal; 0 = recompute at the next publish")
@click.option("--coef", type=float, default=None)
@click.pass_obj
def class_edit(obj: Ctx, name, term, days, reward_label, goal, coef):
    conn = obj.conn
    cls = ops.get_class(conn, name)
    fields = {}
    if term is not None:
        if M.parse_term(term) == (None, None):
            fail("--term must be START..END")
        fields["term"] = term
    if days is not None:
        fields["weekday_mask"] = M.mask_from_days(days)
    if reward_label is not None:
        fields["reward_label"] = reward_label
    if goal is not None:
        fields["reward_goal_points"] = goal or None
    if coef is not None:
        fields["goal_coef"] = coef
    if not fields:
        fail("nothing to change")
    conn.execute(f"UPDATE classes SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), cls["id"]))
    dbmod.log_event(conn, "class_edit", {"class_id": cls["id"], "fields": sorted(fields)})
    conn.commit()
    click.echo("ok")


@class_.command("list")
@click.pass_obj
def class_list(obj: Ctx):
    conn = obj.conn
    today = M.kst_today()
    for c in conn.execute("SELECT * FROM classes ORDER BY id"):
        days = "".join(M.WEEKDAYS_KO[i] for i in M.mask_days(c["weekday_mask"]))
        r = M.reward_for(conn, c, today)
        click.echo(f"{c['name']}\tterm {c['term']}\tdays {days}\tstudents {M.active_count(conn, c['id'])}\t"
                   f"reward {c['reward_label']!r} {r.total}/{r.goal}")


# ---------------------------------------------------------------- roster

@cli.group()
def roster():
    """Students: add, list, import, edit, rotate tokens."""


@roster.command("add")
@click.argument("class_name")
@click.argument("full_name")
@click.option("--display", default=None, help="Display name (default: the given name)")
@click.option("--ext-id", default=None)
@click.option("--phone", default=None, help="Parent phone (console only, never published)")
@click.option("--track", default=None)
@click.pass_obj
def roster_add(obj: Ctx, class_name, full_name, display, ext_id, phone, track):
    conn = obj.conn
    cls = ops.get_class(conn, class_name)
    s = importers.add_student(conn, cls["id"], full_name, display, ext_id, phone, track)
    conn.commit()
    click.echo(f"added {s['slug']} (display {s['display_name']})")
    if s["display_name"] == s["full_name"]:
        click.echo(f"warning: display name equals the full name; publish will refuse it. "
                   f"Run: daybook roster edit {s['slug']} --display <given name>", err=True)


@roster.command("list")
@click.argument("class_name", required=False)
@click.option("--links", is_flag=True, help="Print each student's and parent's link (pages_url from daybook.json)")
@click.option("--all", "show_all", is_flag=True, help="Include inactive students")
@click.pass_obj
def roster_list(obj: Ctx, class_name, links, show_all):
    conn = obj.conn
    q = "SELECT s.*, c.name AS cname FROM students s LEFT JOIN classes c ON c.id = s.class_id WHERE 1=1"
    args: list = []
    if class_name:
        q += " AND s.class_id = ?"
        args.append(ops.get_class(conn, class_name)["id"])
    if not show_all:
        q += " AND s.active = 1"
    base = obj.cfg.pages or "<pages_url>"
    for s in conn.execute(q + " ORDER BY c.id, s.id", args):
        line = f"{s['slug']}\t{s['display_name']}\t{s['cname']}\t{'active' if s['active'] else 'inactive'}"
        click.echo(line)
        if links:
            click.echo(f"  student: {base}/s/{s['student_token']}/")
            click.echo(f"  parent:  {base}/p/{s['parent_token']}/")


@roster.command("import")
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--map", "map_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--class", "class_name", default=None, help="Class for new students")
@click.pass_obj
def roster_import(obj: Ctx, csv_path, map_path, class_name):
    conn = obj.conn
    cls = ops.get_class(conn, class_name) if class_name else None
    try:
        rep = importers.import_roster(conn, csv_path, importers.load_mapping(map_path), cls["id"] if cls else None)
    except ValueError as e:
        fail(str(e))
    click.echo(f"added {len(rep.added)}, updated {len(rep.updated)}, unchanged {len(rep.unchanged)}, skipped {len(rep.skipped)}")
    for s in rep.skipped:
        click.echo(f"  skipped: {s}")
    for w in rep.warnings:
        click.echo(f"  warning: {w}", err=True)


@roster.command("edit")
@click.argument("slug")
@click.option("--display", default=None)
@click.option("--slug", "new_slug", default=None)
@click.option("--track", default=None)
@click.option("--class", "class_name", default=None)
@click.option("--active/--inactive", default=None)
@click.pass_obj
def roster_edit(obj: Ctx, slug, display, new_slug, track, class_name, active):
    conn = obj.conn
    s = ops.get_student(conn, slug)
    fields = {}
    if display is not None:
        fields["display_name"] = display.strip()
    if new_slug is not None:
        fields["slug"] = M.unique_slug(conn, new_slug)
    if track is not None:
        fields["track"] = track
    if class_name is not None:
        fields["class_id"] = ops.get_class(conn, class_name)["id"]
    if active is not None:
        fields["active"] = 1 if active else 0
    if not fields:
        fail("nothing to change")
    conn.execute(f"UPDATE students SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), s["id"]))
    dbmod.log_event(conn, "student_update", {"student_id": s["id"], "fields": sorted(fields)})
    conn.commit()
    click.echo("ok")


@roster.command("rotate-tokens")
@click.argument("class_name")
@click.option("--student", "slug", default=None, help="Only this student")
@click.option("--which", type=click.Choice(["both", "student", "parent"]), default="both", show_default=True)
@click.pass_obj
def roster_rotate(obj: Ctx, class_name, slug, which):
    """New link(s); the old pages disappear at the next publish (old student uploads still map)."""
    conn = obj.conn
    cls = ops.get_class(conn, class_name)
    rows = [ops.get_student(conn, slug)] if slug else conn.execute(
        "SELECT * FROM students WHERE class_id = ? AND active = 1", (cls["id"],)).fetchall()
    for s in rows:
        ops.rotate_tokens(conn, s, which)
    conn.commit()
    click.echo(f"rotated {which} tokens for {len(rows)} student(s); run `daybook publish`, then deploy and send the "
               f"new links (`daybook roster list {class_name} --links`)")


# ---------------------------------------------------------------- sessions / board / score / submit

@cli.group()
def session():
    """Class sessions."""


@session.command("start")
@click.argument("class_name")
@click.option("--date", "d", default=None)
@click.option("--at", "at", default=None, help="Start time HH:MM (default: now). 출석 opens 30 min before, closes 60 min after.")
@click.option("--set", "set_id", default=None)
@click.option("--topic", default=None, help="Label students see for this set (else \"M/D 과제\")")
@click.option("--new-code", is_flag=True, help="Draw a new 4-digit code even if the session has one")
@click.pass_obj
def session_start(obj: Ctx, class_name, d, at, set_id, topic, new_code):
    """Start a class session: 4-digit 출석 code, sent to the Worker when it is configured."""
    from .pull import http_put_json
    conn = obj.conn
    cls = ops.get_class(conn, class_name)
    day = parse_date(d)
    sess = ops.start_session(conn, cls, day, at, set_id, topic, new_code)
    conn.commit()
    t_from, t_until = ops.session_window(day, sess["start_time"])
    click.echo(f"session {cls['name']} {day.isoformat()} {sess['start_time']}" + (f" set {set_id}" if set_id else ""))
    click.echo(f"오늘의 코드: {sess['code']}   (출석 {t_from:%H:%M}–{t_until:%H:%M})")
    if obj.cfg.worker and obj.cfg.teacher_token:
        try:
            http_put_json(obj.cfg, "/session", ops.session_payload(conn, sess))
            click.echo("sent to the Worker: students can check in")
        except PullError as e:
            click.echo(f"warning: not sent to the Worker ({e}); run the command again when online", err=True)
    else:
        click.echo("no Worker configured: 출석 works only through the board's 출석 처리")
    if set_id:
        base = obj.cfg.pages or "https://<pages>"
        click.echo(f"handout QR URL (no token): {base}/go/?set={set_id}")


@cli.group()
def moment():
    """오늘의 장면: the class highlight (photo + one line), up to 3 per session."""


@moment.command("add")
@click.argument("class_name")
@click.option("--photo", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--text", default="", help="One line, up to 60 characters")
@click.option("--date", "d", default=None)
@click.pass_obj
def moment_add(obj: Ctx, class_name, photo, text, d):
    """EXIF stripped, <= 1080 px, <= 200 KB; asks the faces/names check before anything is stored or sent."""
    from . import moments as mm
    from .pull import http_put_json
    conn = obj.conn
    cls = ops.get_class(conn, class_name)
    answer = click.prompt(mm.GATE, default="N", show_default=False)
    push = None
    if obj.cfg.worker and obj.cfg.teacher_token:
        push = lambda payload: http_put_json(obj.cfg, "/moment-put", payload)   # noqa: E731
    try:
        row = mm.add_moment(conn, obj.cfg, cls, parse_date(d), Path(photo).read_bytes(), text,
                            confirmed=answer.strip().lower() == "y", push=push)
    except mm.MomentError as e:
        fail(str(e))
    except PullError as e:
        click.echo(f"stored locally; not sent to the Worker ({e}): it will show after Saturday's publish", err=True)
        return
    size = mm.image_path(obj.cfg, row["image_key"]).stat().st_size
    click.echo(f"moment {row['image_key']} ({size // 1024} KB, EXIF removed)" + (" sent to the Worker" if push else ""))


@moment.command("list")
@click.argument("class_name")
@click.pass_obj
def moment_list(obj: Ctx, class_name):
    conn = obj.conn
    cls = ops.get_class(conn, class_name)
    for r in conn.execute("""SELECT m.*, se.date FROM moments m JOIN sessions se ON se.id = m.session_id
                             WHERE m.class_id = ? ORDER BY se.date DESC, m.id""", (cls["id"],)):
        click.echo(f"{r['date']}  {r['image_key']}\t{r['text'] or ''}")


@cli.command("lines")
@click.argument("class_name")
@click.option("--date", "d", default=None, help="Day (default today); --days for more")
@click.option("--days", default=1, show_default=True, help="How many days back")
@click.pass_obj
def lines_cmd(obj: Ctx, class_name, d, days):
    """The students' 오늘 한 줄 and chips (teacher only; parents do not see them by default)."""
    from datetime import timedelta
    conn = obj.conn
    cls = ops.get_class(conn, class_name)
    end = parse_date(d)
    S = configmod.strings()
    rows = conn.execute(
        """SELECT s.display_name, sub.day, sub.reflection, sub.chip FROM submissions sub JOIN students s ON s.id = sub.student_id
           WHERE s.class_id = ? AND sub.day BETWEEN ? AND ? AND (sub.reflection IS NOT NULL OR sub.chip IS NOT NULL)
           ORDER BY sub.day DESC, s.id""", (cls["id"], (end - timedelta(days=days - 1)).isoformat(), end.isoformat())).fetchall()
    for r in rows:
        chip = S.get("chip_" + (r["chip"] or ""), "") if r["chip"] else ""
        click.echo(f"{r['day']}  {r['display_name']}\t{chip}\t{r['reflection'] or ''}")
    if not rows:
        click.echo("no lines")


@cli.command("checkin")
@click.argument("slug")
@click.option("--date", "d", default=None)
@click.pass_obj
def checkin_cmd(obj: Ctx, slug, d):
    """출석 처리 on the student's behalf (source teacher)."""
    conn = obj.conn
    s = ops.get_student(conn, slug)
    sid = ops.ensure_session(conn, s["class_id"], parse_date(d))
    made = ops.record_checkin(conn, s["id"], sid, dbmod.now_utc_iso(), "teacher")
    conn.commit()
    click.echo("ok" if made else "already checked in")


@cli.command()
@click.argument("class_name")
@click.option("--port", default=8787, show_default=True)
@click.option("--no-poll", is_flag=True, help="Do not poll the Worker")
@click.option("--date", "d", default=None, help="Board date (default today)")
@click.pass_obj
def board(obj: Ctx, class_name, port, no_poll, d):
    """Serve the classroom board on 127.0.0.1."""
    from . import board as boardmod
    cls = ops.get_class(obj.conn, class_name)
    boardmod.serve(obj.db_path, obj.cfg, cls["name"], port, poll=not no_poll, day=parse_date(d))


@cli.command()
@click.argument("class_name")
@click.argument("d", metavar="DATE")
@click.argument("slug")
@click.argument("points", type=click.IntRange(0, 3))
@click.pass_obj
def score(obj: Ctx, class_name, d, slug, points):
    """Record an attitude score 0-3 (the non-interactive board)."""
    conn = obj.conn
    cls = ops.get_class(conn, class_name)
    s = ops.get_student(conn, slug)
    if s["class_id"] != cls["id"]:
        fail(f"{slug} is not in {class_name}")
    sid = ops.ensure_session(conn, cls["id"], parse_date(d))
    ops.set_attitude(conn, s["id"], sid, points)
    conn.commit()
    click.echo("ok")


@cli.command()
@click.argument("slug")
@click.option("--date", "d", default=None)
@click.option("--source", type=click.Choice(["teacher", "paper"]), default="teacher", show_default=True)
@click.pass_obj
def submit(obj: Ctx, slug, d, source):
    """Mark today's task as submitted on the student's behalf (paper copy, phone trouble)."""
    conn = obj.conn
    s = ops.get_student(conn, slug)
    made = ops.teacher_submit(conn, s["id"], parse_date(d), source)
    conn.commit()
    click.echo("ok" if made else "already submitted that day")


# ---------------------------------------------------------------- pull / import / note

@cli.command()
@click.option("--photos", "photos_dir", default=None, type=click.Path(file_okay=False),
              help="Also download the photos (kept 7 days in the Worker) into this folder")
@click.pass_obj
def pull(obj: Ctx, photos_dir):
    """Fetch new submissions from the Worker."""
    try:
        rep = do_pull(obj.conn, http_fetch(obj.cfg), photos_dir)
    except PullError as e:
        fail(str(e))
    click.echo(f"fetched {rep.fetched}: new {rep.new}, updated {rep.updated}, check-ins {rep.checkins}, "
               f"unknown tokens {rep.unknown_tokens}")
    if rep.photos:
        click.echo(f"photos saved: {len(rep.photos)}")


@cli.group("import")
def import_():
    """Import screener rounds or a generic tests CSV."""


@import_.command("screeners")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.pass_obj
def import_screeners(obj: Ctx, directory):
    rep = importers.import_screeners(obj.conn, directory)
    click.echo(f"{rep.files} rounds, {rep.rows} rows: imported {rep.imported}, unmatched {len(rep.unmatched)}")
    for u in rep.unmatched:
        click.echo(f"  unmatched (not imported): {u}")
    for e in rep.errors:
        click.echo(f"  error: {e}", err=True)


@import_.command("tests")
@click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def import_tests(obj: Ctx, csv_path):
    try:
        rep = importers.import_tests_csv(obj.conn, csv_path)
    except ValueError as e:
        fail(str(e))
    click.echo(f"{rep.rows} rows: imported {rep.imported}, unmatched {len(rep.unmatched)}")
    for u in rep.unmatched + rep.errors:
        click.echo(f"  {u}")


@cli.command()
@click.argument("slug")
@click.argument("week")
@click.argument("text")
@click.pass_obj
def note(obj: Ctx, slug, week, text):
    """The teacher's one-line note for a week (YYYY-Www). The parent page shows the latest."""
    conn = obj.conn
    s = ops.get_student(conn, slug)
    ops.add_note(conn, s["id"], week, text)
    conn.commit()
    click.echo("ok")


# ---------------------------------------------------------------- weekly / publish / export

@cli.command()
@click.argument("class_name")
@click.option("--date", "d", default=None, help="Any day of the week (default today)")
@click.pass_obj
def weekly(obj: Ctx, class_name, d):
    """Print a paste-ready Korean weekly message per student."""
    from .weekly import messages
    conn = obj.conn
    cls = ops.get_class(conn, class_name)
    for msg in messages(conn, obj.cfg, cls, parse_date(d)):
        click.echo(msg)
        click.echo("-" * 24)


def _report_publish(res):
    for f in res.findings:
        click.echo(str(f), err=True)
    if res.ok:
        click.echo(f"published {res.n_students} students into {res.out}/ (lint passed)")
    else:
        click.echo("publish STOPPED: the no-PII lint failed; dist/ was not changed", err=True)


@cli.command()
@click.option("--out", default=None, help="Output folder (default dist/, or dist-taste/ with --taste)")
@click.option("--date", "d", default=None, help="Publish as of this date (default today)")
@click.option("--taste", is_flag=True, help="Taste build: only the demo student 콩 at /s/kong/ and /p/kong/, no Worker")
@click.pass_obj
def publish(obj: Ctx, out, d, taste):
    """Render dist/ (student, parent, go pages) and run the no-PII lint; exits non-zero if it fails."""
    today = parse_date(d) if d else None
    out = out or ("dist-taste" if taste else "dist")
    try:
        res = generate.publish(obj.conn, obj.cfg, Path(out), today=today, taste=taste)
    except ValueError as e:
        fail(str(e))
    _report_publish(res)
    if not res.ok:
        sys.exit(1)


@cli.group()
def export():
    """Exports."""


@export.command("pdf")
@click.argument("slug")
def export_pdf(slug):
    """(stub) PDF report: not implemented in P0."""
    click.echo("export pdf is not implemented yet (deferred, see DECISIONS.md). "
               "Use the parent page link or print it from the browser.", err=True)
    sys.exit(2)


if __name__ == "__main__":
    main()
