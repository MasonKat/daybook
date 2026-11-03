"""`daybook` command line (click). See README for the teacher's weekly routine."""
from __future__ import annotations

import os
import json
import sys
from datetime import date
from pathlib import Path

import click

from . import config as configmod
from . import db as dbmod
from . import generate, importers, models as M, ops
from .pull import PullError, http_fetch, pull as do_pull, push_tokmap


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
    """Seed the synthetic demo tenant (4 classes, 22 students) and publish it."""
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
    ins = importers.import_insights(conn, demomod.write_demo_insights(conn))
    click.echo(f"insights (F12): {ins.imported} students")
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
@click.option("--grade", default=None, help="F10: e.g. 고1 (videos.json `grades` match it)")
@click.option("--start", "start_time", default=None, help="Class start HH:MM (the default for `session start`)")
@click.pass_obj
def class_add(obj: Ctx, name, term, days, reward_label, goal, coef, grade, start_time):
    if M.parse_term(term) == (None, None):
        fail("--term must be START..END with ISO dates")
    try:
        mask = M.mask_from_days(days)
    except ValueError as e:
        fail(str(e))
    conn = obj.conn
    if conn.execute("SELECT 1 FROM classes WHERE name = ?", (name,)).fetchone():
        fail(f"class {name!r} exists")
    if start_time and not ops._HHMM.match(start_time):
        fail("--start must be HH:MM")
    cur = conn.execute(
        """INSERT INTO classes (name, term, weekday_mask, reward_label, reward_goal_points, goal_coef, grade, start_time)
           VALUES (?,?,?,?,?,?,?,?)""",
        (name, term, mask, reward_label, goal, coef, (grade or "").strip() or None, start_time or None))
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
@click.option("--grade", default=None, help="F10: e.g. 고1 ('' clears it)")
@click.option("--start", "start_time", default=None, help="Class start HH:MM (F13: lateness is measured from it)")
@click.option("--end", "end_time", default=None, help="Class end HH:MM")
@click.option("--self-study", "self_study_until", default=None, help="Self-study until HH:MM ('' clears it)")
@click.option("--late-min", type=int, default=None, help="F13 연락 필요: late = 출석 later than start + N min (default 10)")
@click.option("--late-count", type=int, default=None, help="... on >= N of the last 6 class days (default 3)")
@click.option("--late-avg-min", type=int, default=None, help="... or an average lateness >= N min (default 15)")
@click.pass_obj
def class_edit(obj: Ctx, name, term, days, reward_label, goal, coef, grade, **policy):
    conn = obj.conn
    cls = ops.get_class(conn, name)
    try:
        pol = ops.class_policy(conn, cls, policy)
    except ops.OpError as e:
        fail(str(e))
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
    if grade is not None:
        fields["grade"] = grade.strip() or None
    if not fields and not pol:
        fail("nothing to change")
    if fields:
        conn.execute(f"UPDATE classes SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), cls["id"]))
        dbmod.log_event(conn, "class_edit", {"class_id": cls["id"], "fields": sorted(fields)})
    conn.commit()
    click.echo("ok")


@class_.command("cancel")
@click.argument("class_name")
@click.argument("d", metavar="DATE")
@click.option("--reason", default="휴강", show_default=True)
@click.option("--undo", is_flag=True, help="Make it a class day again")
@click.pass_obj
def class_cancel(obj: Ctx, class_name, d, reason, undo):
    """A cancelled day no longer counts as a class day (streaks, goal); it shows as a grey "휴강" tile."""
    conn = obj.conn
    cls = ops.get_class(conn, class_name)
    day = parse_date(d)
    ops.cancel_class(conn, cls, day, None if undo else reason)
    conn.commit()
    click.echo(f"{cls['name']} {day.isoformat()}: " + ("class day again" if undo else f"cancelled ({reason})"))


@class_.command("list")
@click.pass_obj
def class_list(obj: Ctx):
    conn = obj.conn
    today = M.kst_today()
    for c in conn.execute("SELECT * FROM classes ORDER BY id"):
        days = "".join(M.WEEKDAYS_KO[i] for i in M.mask_days(c["weekday_mask"]))
        r = M.reward_for(conn, c, today)
        click.echo(f"{c['name']}\t{c['grade'] or '-'}\tterm {c['term']}\tdays {days}\tstudents {M.active_count(conn, c['id'])}\t"
                   f"reward {c['reward_label']!r} {r.total}/{r.goal}\t"
                   f"hours {c['start_time'] or '-'}-{c['end_time'] or '-'}"
                   f"{(' self-study ~' + c['self_study_until']) if c['self_study_until'] else ''}\t"
                   f"late {c['late_min']}m x{c['late_count']} / avg {c['late_avg_min']}m")


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
    _send_links(obj, conn)
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
    _send_links(obj, conn)
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
    changed = ops.edit_student(conn, slug, display, new_slug, track, class_name, active)
    conn.commit()
    click.echo("ok")
    if {"active", "class_id"} & changed:
        _send_links(obj, conn)


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
    _send_links(obj, conn)


# ---------------------------------------------------------------- sessions / board / score / submit

@cli.group()
def session():
    """Class sessions."""


@session.command("start")
@click.argument("class_name")
@click.option("--date", "d", default=None)
@click.option("--at", "at", default=None, help="Start time HH:MM (default: the class start, else now). 출석 opens 30 min before, closes 60 min after.")
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


@cli.command()
@click.argument("first")
@click.argument("second", required=False)
@click.option("--set", "set_id", default=None, help="The set id the handout is for")
@click.option("--out", default=None, help="qr/<set>.svg (or qr/<class>-sheet.html) by default; .png also works")
@click.pass_obj
def qr(obj: Ctx, first, second, set_id, out):
    """Handout QR codes: {pages_url}/go/?set=<id>, never a token.

    \b
    daybook qr <class> --set <set_id> [--out f.svg|f.png]
    daybook qr sheet <class> [--out sheet.html]      (print-ready A4, one card per set id)
    """
    import json as _json
    from . import qr as qrmod
    conn = obj.conn
    try:
        if first == "sheet":
            if not second:
                fail("usage: daybook qr sheet <class>")
            cls = ops.get_class(conn, second)
            labels = {}
            p = Path(obj.cfg.assignments_path)
            if p.exists():
                labels = {k: v for k, v in _json.loads(p.read_text(encoding="utf-8")).items() if not k.startswith("_")}
            path = Path(out or f"qr/{cls['name']}-sheet.html")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(qrmod.sheet(conn, obj.cfg, cls, labels), encoding="utf-8")
            click.echo(f"{path}  (open it in a browser and print)")
            return
        ops.get_class(conn, first)
        if not set_id:
            fail("usage: daybook qr <class> --set <set_id>")
        url = qrmod.go_url(obj.cfg, set_id)
        path = qrmod.save(url, Path(out or f"qr/{set_id}.svg"))
        click.echo(f"{path}  →  {url}")
    except qrmod.QRError as e:
        fail(str(e))


@cli.command()
@click.option("--spike", is_flag=True, help="Required: answer-card reading is an experiment (F8)")
@click.option("--eval", "n_eval", type=int, default=None, help="Accuracy on N synthetic phone photos")
@click.option("--sample", default=None, type=click.Path(dir_okay=False), help="Write one synthetic card photo here")
@click.option("--image", default=None, type=click.Path(exists=True, dir_okay=False), help="A photographed card to read")
@click.option("--student", "slug", default=None, help="With --image: write the read into effort for this student")
@click.option("--set", "set_id", default=None, help="With --student: the set id the card belongs to")
@click.option("--key", default=None, help="Optional answer key, e.g. 3,1,4,2,5,…")
@click.pass_obj
def omr(obj: Ctx, spike, n_eval, sample, image, slug, set_id, key):
    """Answer-card reading spike: 20 items × ①–⑤ with four corner squares. Not wired to publish."""
    import json as _json
    import random as _random
    from . import omr as om
    if not spike:
        fail("answer-card reading is a spike: run it with --spike (its results never reach the pages)")
    if n_eval:
        click.echo(_json.dumps(om.evaluate(n_eval), ensure_ascii=False, indent=1))
    if sample:
        img, truth = om.synthetic_card(_random.Random())
        img.save(sample)
        click.echo(f"{sample}  (truth: {[sorted(t) for t in truth]})")
    if image:
        from PIL import Image
        try:
            read = om.read_card(Image.open(image))
        except ValueError as e:
            fail(f"could not read the card: {e}")
        click.echo(" ".join(f"{i}:{''.join(map(str, sorted(p))) or '-'}" for i, p in enumerate(read, 1)))
        if slug:
            if not set_id:
                fail("--set is needed with --student")
            k = [int(x) for x in key.split(",")] if key else None
            n = om.to_effort(obj.conn, ops.get_student(obj.conn, slug)["id"], set_id, read, k)
            click.echo(f"effort: {n} items (source card)")
    if not (n_eval or sample or image):
        fail("nothing to do: --eval N, --sample out.jpg or --image card.jpg")


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
               f"weekly-test reports {rep.reports}, unknown tokens {rep.unknown_tokens}")
    if rep.photos:
        click.echo(f"photos saved: {len(rep.photos)}")


@cli.group("import")
def import_():
    """Import screener rounds, a generic tests CSV, or insights (F12)."""


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


@import_.command("insights")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def import_insights_cmd(obj: Ctx, path):
    """F12: a student-safe slice of your own analysis (focus, videos, one line each) - see mappings/insights.example.json."""
    rep = importers.import_insights(obj.conn, path)
    for e in rep.errors:
        click.echo(f"error: {e}", err=True)
    click.echo(f"insights: {rep.imported}/{rep.rows} imported; unknown ext_id: {len(rep.unknown)}")
    for u in rep.unknown:
        click.echo(f"  unknown: {u}")
    if rep.errors:
        sys.exit(1)


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


@cli.command(context_settings={"ignore_unknown_options": True})
@click.argument("slug")
@click.argument("week", required=False)
@click.argument("text", required=False)
@click.option("--date", "d", default=None, help="note suggest: any day of the week (default today)")
@click.pass_obj
def note(obj: Ctx, slug, week, text, d):
    """The teacher's one-line note for a week (YYYY-Www). The parent page shows the latest.

    \b
    daybook note <slug> <YYYY-Www> "<text>"
    daybook note suggest <class>     data-based drafts to edit (skips students who already have this week's note)
    """
    conn = obj.conn
    if slug == "suggest":
        from .report import suggest_notes
        if not week:
            fail("usage: daybook note suggest <class>")
        cls = ops.get_class(conn, week)
        drafts = suggest_notes(conn, obj.cfg, cls, parse_date(d))
        for sl, wk, draft in drafts:
            click.echo(f'daybook note {sl} {wk} "{draft}"')
        if not drafts:
            click.echo("every student already has a note this week")
        return
    if not week or text is None:
        fail('usage: daybook note <slug> <YYYY-Www> "<text>"')
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


def _send_links(obj, conn) -> None:
    msg = push_tokmap(conn, obj.cfg)
    if msg:
        click.echo(msg, err=msg.startswith("warning"))


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
    if not taste:
        _send_links(obj, obj.conn)
        click.echo(_auto_backup(obj.db_path, obj.cfg))


def _auto_backup(db_path, cfg) -> str:
    """G2: every 발행 ends with a backup (never stops the publish)."""
    from . import backup as B
    try:
        r = B.run(db_path, cfg, keep=int(cfg.extra.get("backup_keep", 30)))
        return f"backup: {r.path}"
    except (B.BackupError, OSError) as e:
        return f"warning: backup failed ({e}); run `daybook backup`"


@cli.group()
def export():
    """Exports."""


@export.command("report")
@click.argument("slug")
@click.option("--out", default=None, help="reports/<slug>.html by default (gitignored)")
@click.option("--date", "d", default=None)
@click.pass_obj
def export_report(obj: Ctx, slug, out, d):
    """End-of-term 1-page 상담 자료 as print-ready HTML (open it and print to PDF in the browser)."""
    from .report import term_report
    conn = obj.conn
    s = ops.get_student(conn, slug)
    try:
        html = term_report(conn, obj.cfg, s["id"], parse_date(d))
    except ValueError as e:
        fail(str(e))
    path = Path(out or f"reports/{slug}.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    click.echo(f"{path}  (open it in a browser → print → save as PDF)")


@export.command("reports")
@click.option("--round", "round_id", required=True, help="The round id, e.g. Adaptive_Screeners_1S_Nov06_G10")
@click.option("--format", "fmt", type=click.Choice(["csv", "notion-json"]), default="csv", show_default=True)
@click.option("--out", "out_dir", default="reports", show_default=True, help="Folder (gitignored)")
@click.pass_obj
def export_reports(obj: Ctx, round_id, fmt, out_dir):
    """F14: the round's 주간 테스트 reports with the Notion columns (Student ID, Name, Grade, …). Local only."""
    from . import rounds as R
    conn = obj.conn
    try:
        path = R.export(conn, round_id, fmt, out_dir)
        prog = R.progress(conn, round_id)
    except R.RoundError as e:
        fail(str(e))
    conn.commit()
    click.echo(f"{path}  ({prog['done']}/{prog['total']} students reported)")
    missing = [f"{x['class_name']} {x['display_name']}" for x in prog["students"] if not x["done"]]
    if missing:
        click.echo("not reported yet: " + ", ".join(missing))


@export.command("pdf")
@click.argument("slug")
def export_pdf(slug):
    """(stub) PDF report: not implemented in P0."""
    click.echo("export pdf is not implemented yet (deferred, see DECISIONS.md). "
               "Use the parent page link or print it from the browser.", err=True)
    sys.exit(2)


# ---------------------------------------------------------------- F13: parent contact (laptop only, never sent)

@cli.group()
def contact():
    """Parent contact desk: 연락 필요, 연락 현황, the weekly planner, logging. Nothing is ever sent."""


@contact.command("needed")
@click.option("--class", "class_name", default=None)
@click.option("--date", "d", default=None)
@click.option("--script", is_flag=True, help="Also print the 3-line call script and the KakaoTalk draft")
@click.pass_obj
def contact_needed(obj: Ctx, class_name, d, script):
    """연락 필요: rule-based exceptions with reasons, dates and times."""
    from . import contact as C
    conn = obj.conn
    cid = ops.get_class(conn, class_name)["id"] if class_name else None
    fl = C.flags(conn, obj.cfg, parse_date(d), cid)
    if not fl:
        click.echo("연락 필요 없음")
    for f in fl:
        click.echo(f"[{'연락 필요' if f['level'] == 'need' else '참고'}] {f['class_name']} {f['display_name']} ({f['slug']})")
        for r in f["rules"]:
            click.echo(f"    - {r['reason']}")
        if script:
            for i, line in enumerate(f["script"], 1):
                click.echo(f"    {i}. {line}")
            click.echo("    카톡 초안:\n      " + f["kakao"].replace("\n", "\n      "))


@contact.command("status")
@click.option("--date", "d", default=None)
@click.pass_obj
def contact_status(obj: Ctx, d):
    """연락 현황: every student, longest without contact first."""
    from . import contact as C
    mark = {"red": "!!", "amber": "! ", "ok": "  "}
    for x in C.status(obj.conn, obj.cfg, parse_date(d)):
        click.echo(f"{mark[x['level']]} {x['days']:>4}d  {x['class_name']}\t{x['display_name']} ({x['slug']})\t"
                   f"last {x['last'] or '-'}\t{x['channel']}/{x['style']}")


@contact.command("log")
@click.argument("slug")
@click.option("--kind", type=click.Choice(["call", "message", "meeting"]), default="call", show_default=True)
@click.option("--in", "inbound", is_flag=True, help="The parent reached out (direction in)")
@click.option("--summary", default="", help="One line, <= 120 characters")
@click.option("--follow-up", default=None, help="YYYY-MM-DD")
@click.pass_obj
def contact_log_cmd(obj: Ctx, slug, kind, inbound, summary, follow_up):
    """Record a call/message/meeting (clears 연락 필요 until new data)."""
    from . import contact as C
    conn = obj.conn
    try:
        C.log_contact(conn, ops.get_student(conn, slug)["id"], kind, summary, "in" if inbound else "out", follow_up)
    except (C.ContactError, ops.OpError) as e:
        fail(str(e))
    conn.commit()
    click.echo("logged")


@contact.command("snooze")
@click.argument("slug")
@click.option("--rule", default="all", show_default=True, help="late|absent|homework|test|stale|all")
@click.option("--days", type=int, default=7, show_default=True)
@click.pass_obj
def contact_snooze_cmd(obj: Ctx, slug, rule, days):
    """이번 주는 보류: hold a flag for 7 days (logged)."""
    from . import contact as C
    conn = obj.conn
    try:
        until = C.snooze(conn, ops.get_student(conn, slug)["id"], rule, M.kst_today(), days)
    except (C.ContactError, ops.OpError) as e:
        fail(str(e))
    conn.commit()
    click.echo(f"held until {until}")


@contact.command("profile")
@click.argument("slug")
@click.option("--channel", type=click.Choice(["call", "kakao", "both"]), default=None)
@click.option("--style", type=click.Choice(["weekly", "needed"]), default=None, help="weekly = 매주, needed = 필요할 때만")
@click.option("--greeting", type=click.Choice(["어머님", "아버님", "보호자님"]), default=None)
@click.pass_obj
def contact_profile_cmd(obj: Ctx, slug, channel, style, greeting):
    from . import contact as C
    conn = obj.conn
    new = C.set_profile(conn, ops.get_student(conn, slug)["id"], channel, style, greeting)
    conn.commit()
    click.echo(f"{new['channel']} / {new['style']} / {new['greeting']}")


@contact.command("plan")
@click.option("--date", "d", default=None)
@click.pass_obj
def contact_plan(obj: Ctx, d):
    """이번 주 할 일: the 100-minute planner."""
    from . import contact as C
    pw = C.plan_week(obj.conn, obj.cfg, parse_date(d))
    label = {"call": "통화", "message": "메시지", "line": "한 줄"}
    for it in pw["items"]:
        click.echo(f"[{'x' if it['done'] else ' '}] {label[it['type']]:<3} {it['minutes']:>2}분  {it['class_name']} "
                   f"{it['display_name']}{'  (고정)' if it['locked'] else ''}  · {it['why']}")
    for it in pw["moved"]:
        click.echo(f"    → 다음 주: {label[it['type']]} {it['class_name']} {it['display_name']}")
    click.echo(f"{pw['total']}/{pw['budget']}분 · {pw['coverage']['text']}")


@contact.command("goodnews")
@click.option("--date", "d", default=None)
@click.pass_obj
def contact_goodnews(obj: Ctx, d):
    """이달의 좋은 소식: positive-only drafts for every parent (copy, edit, send yourself)."""
    from . import contact as C
    day = parse_date(d)
    if not C.last_week_of_month(day):
        click.echo("(not the last week of the month yet; drafts are shown anyway)")
    for g in C.good_news(obj.conn, obj.cfg, day):
        click.echo(f"--- {g['class_name']} {g['display_name']}\n{g['text']}")


# ---------------------------------------------------------------- F14: weekly-test rounds

@cli.group("round")
def round_():
    """주간 테스트 rounds: students report on their page while a round is open."""


def _round_line(r) -> str:
    from . import rounds as R
    return (f"{r['id']}\t{r['label']}\t{r['grade'] or '-'}\tN={r['n']}\t{r['open_at']} ~ {r['close_at']}"
            f"{'  (open)' if R.is_open(r) else ''}")


@round_.command("add")
@click.argument("round_id")
@click.option("--grade", default=None, help="1학년 | 2학년 | 3학년")
@click.option("--classes", required=True, help="Comma-separated class names, e.g. 고1A,심화C")
@click.option("--quizzes", "n", type=int, required=True, help="Number of quizzes (1-60)")
@click.option("--open", "open_at", required=True, help='KST, e.g. "2026-11-06 20:00"')
@click.option("--close", "close_at", required=True, help='KST, e.g. "2026-11-08 23:59"')
@click.option("--label", default=None, help="What students see (default: 11/6 주간 테스트)")
@click.pass_obj
def round_add(obj: Ctx, round_id, grade, classes, n, open_at, close_at, label):
    from . import rounds as R
    conn = obj.conn
    try:
        r = R.add(conn, round_id, grade, classes, n, open_at, close_at, label)
    except R.RoundError as e:
        fail(str(e))
    conn.commit()
    click.echo(_round_line(r))
    msg = R.push(conn, obj.cfg)
    if msg:
        click.echo(msg)


@round_.command("edit")
@click.argument("round_id")
@click.option("--grade", default=None)
@click.option("--classes", default=None)
@click.option("--quizzes", "n", type=int, default=None)
@click.option("--open", "open_at", default=None)
@click.option("--close", "close_at", default=None)
@click.option("--label", default=None)
@click.pass_obj
def round_edit(obj: Ctx, round_id, **kw):
    from . import rounds as R
    conn = obj.conn
    try:
        r = R.edit(conn, round_id, **kw)
    except R.RoundError as e:
        fail(str(e))
    conn.commit()
    click.echo(_round_line(r))
    msg = R.push(conn, obj.cfg)
    if msg:
        click.echo(msg)


@round_.command("close")
@click.argument("round_id")
@click.pass_obj
def round_close(obj: Ctx, round_id):
    """Close a round now (students can no longer send or edit)."""
    from . import rounds as R
    conn = obj.conn
    try:
        r = R.close_now(conn, round_id)
    except R.RoundError as e:
        fail(str(e))
    conn.commit()
    click.echo(_round_line(r))
    msg = R.push(conn, obj.cfg)
    if msg:
        click.echo(msg)


@round_.command("list")
@click.pass_obj
def round_list(obj: Ctx):
    for r in obj.conn.execute("SELECT * FROM rounds ORDER BY open_at DESC"):
        click.echo(_round_line(r))


@round_.command("status")
@click.argument("round_id")
@click.pass_obj
def round_status(obj: Ctx, round_id):
    """Who has reported (✓); run `daybook pull` first."""
    from . import rounds as R
    try:
        p = R.progress(obj.conn, round_id)
    except R.RoundError as e:
        fail(str(e))
    click.echo(f"{p['label']}: {p['done']}/{p['total']} 제출")
    for x in p["students"]:
        click.echo(f"  {'✓' if x['done'] else '·'} {x['class_name']} {x['display_name']} {x['at']}")


# ---------------------------------------------------------------- G1/G2: preflight and backups

@cli.command()
@click.option("--dist", default="dist", show_default=True, help="The published site to lint")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_obj
def doctor(obj: Ctx, dist, as_json):
    """Preflight before every class and every publish: ✓ / ⚠ / ✗ with a one-line fix. Exit 0 when there is no ✗."""
    from . import doctor as D
    lines = D.run(obj.conn, obj.cfg, obj.db_path, dist)
    click.echo(json.dumps(D.as_json(lines), ensure_ascii=False, indent=1) if as_json else D.text(lines))
    if not D.ok(lines):
        sys.exit(1)


@cli.command()
@click.option("--to", "to", default=None, help='Folder (default: "backup_dir" in daybook.json, else backups/ next to the database)')
@click.option("--keep", type=int, default=30, show_default=True, help="Keep the newest N backups")
@click.pass_obj
def backup(obj: Ctx, to, keep):
    """A consistent copy of the database (safe while the board runs), checked with integrity_check."""
    from . import backup as B
    try:
        r = B.run(obj.db_path, obj.cfg, to, keep)
    except B.BackupError as e:
        fail(str(e))
    click.echo(f"{r.path}  (integrity {r.integrity}; removed {len(r.removed)} old)")


@cli.command()
@click.option("--out", default="cards/운영가이드.html", show_default=True, help="Output file (cards/ is gitignored)")
def guide(out):
    """G3: the Korean operations guide (docs/운영가이드.md) as printable A4 HTML."""
    from . import guide as G
    click.echo(f"{G.write(out)}  (open it in a browser and print, A4)")


@cli.command()
@click.argument("kind", type=click.Choice(["students", "parents"]))
@click.argument("class_name")
@click.option("--out", "out_dir", default="cards", show_default=True, help="Folder (gitignored)")
@click.pass_obj
def cards(obj: Ctx, kind, class_name, out_dir):
    """G4: printable 안내 cards (students: cut-out cards; parents: an A4 letter each). Never a link or a QR on paper."""
    from . import cards as C
    try:
        path = C.write(obj.conn, kind, class_name, out_dir)
    except (ops.OpError, ValueError) as e:
        fail(str(e))
    click.echo(f"{path}  (open it in a browser and print, A4)")


if __name__ == "__main__":
    main()
