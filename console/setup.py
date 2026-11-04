"""F20: `python -m console setup`: the HANDOFF Thursday checklist as a guided, resumable wizard for the real data.

Every step says what it does, asks for the minimum, shows a dry-run preview before any write ("이대로 할까요?"), runs it,
and verifies the result with the matching doctor check (✓ / ⚠ / ✗) before moving on. The state lives in
setup_state.json (gitignored): running it again continues where it stopped; `--from <step>` redoes one step. Typing
그만 (or stop) at any prompt stops and saves. Steps on the Cloudflare dashboard print exactly what to click or run, wait for
"다 했어요", and verify what can be verified (wrangler.toml's D1 binding, the Worker's /health).

Safety: it never touches the demo database (demo.sqlite or one seeded by `daybook demo`) or the demo config; an existing
database with records is never re-initialized (a backup is offered instead). External calls (wrangler, the Worker) go
through injectable functions, so the tests run the whole night with everything mocked.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import db as dbmod
from . import doctor as D
from .config import ROOT, Config, strings
from .config import load as load_config

STEPS = ("db", "subdomain", "d1", "token", "worker", "vapid", "config", "classes", "roster", "consent", "rounds",
         "publish", "deploy", "doctor")
YES = {"네", "예", "응", "y", "yes", "ㅇ", "ok"}
NO = {"아니요", "아니오", "아니", "n", "no"}
DONE = {"다 했어요", "다했어요", "다 했어", "done", "네", "y", "yes"}
SKIP = {"건너뛰기", "skip", "s"}
STOP = {"그만", "stop", "q", "quit"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

EN = {
    "title": "DAYBOOK first setup (Thursday night, real data)",
    "intro": "One step at a time. Type 'stop' at any prompt to pause; run it again to continue where you stopped.",
    "header": "[{n}/{total}] {title}", "preview": "Preview (nothing has changed yet):", "confirm": "Go ahead? (yes/no)",
    "wait": "Type 'done' when you have done it ('skip' to skip for now)", "stopped": "Paused. Run setup again to continue at '{step}'.",
    "all_done": "Every step is done. Thank you!", "resume": "Continuing at step '{step}' (done: {n} of {total}).",
    "retry": "Check again? (yes / stop)", "again": "Then let's enter it again.", "skipped": "skipped for now",
    "t_db": "the database", "t_subdomain": "the workers.dev subdomain", "t_d1": "the D1 database", "t_token": "the teacher token",
    "t_worker": "deploy the Worker", "t_vapid": "push keys (optional)", "t_config": "daybook.json", "t_classes": "classes",
    "t_roster": "the roster", "t_consent": "consent slips", "t_rounds": "weekly-test rounds (Fri, Sat)", "t_publish": "publish",
    "t_deploy": "deploy the pages", "t_doctor": "the final check",
    "demo_db": "This is the demo database. Use the real one (--db daybook.sqlite); setup never touches demo data.",
    "demo_cfg": "This is the demo config. Use daybook.json (copy daybook.example.json); setup never touches demo files.",
    "db_new": "create a new database: {path}", "db_has": "{path} already has records ({classes} classes, {students} students): it is not re-initialized.",
    "db_backup": "Back it up now first? (yes/no)", "db_backed": "backup: {path}",
    "sub_do": "In a browser: Cloudflare dashboard → Workers & Pages → (first time only) choose your free workers.dev subdomain.",
    "sub_note": "checked later, when the Worker answers",
    "d1_do": "In PowerShell, in the DAYBOOK folder:\n  cd worker\n  npx wrangler login\n  npx wrangler d1 create daybook\nIt prints a database_id (8-4-4-4-12 letters and digits).",
    "d1_ask": "Paste the database_id", "d1_bad": "That is not a database_id (8-4-4-4-12).", "d1_write": "worker/wrangler.toml: database_id = {id}",
    "d1_ok": "worker/wrangler.toml has the D1 binding ({id})", "d1_missing": "worker/wrangler.toml still has no database_id",
    "tok_have": "daybook.json already has a teacher token; keeping it.", "tok_write": "daybook.json: teacher_token = (a new random token, 43 characters)",
    "tok_do": "In the worker folder run:\n  npx wrangler secret put TEACHER_TOKEN\nand paste this value when it asks:\n  {token}",
    "wk_do": "In the worker folder run:\n  npx wrangler deploy\nIt prints the Worker address (https://….workers.dev).",
    "wk_ask": "Paste the Worker address", "wk_bad": "That is not an http(s) address.", "wk_write": "daybook.json: worker_url = {url}",
    "vp_ask": "Use push notifications? (yes/no)", "vp_subject": "Contact for the push service (mailto:you@example.com)",
    "vp_write": "daybook.json: vapid_public / vapid_private / vapid_subject (new keys)",
    "vp_do": "In the worker folder run these three, each pasting the value from daybook.json:\n  npx wrangler secret put VAPID_PUBLIC   (vapid_public)\n  npx wrangler secret put VAPID_PRIVATE  (vapid_private)\n  npx wrangler secret put VAPID_SUBJECT  (vapid_subject)\nthen once more: npx wrangler deploy",
    "vp_ok": "the Worker has the push keys", "vp_not": "the Worker does not report push keys yet",
    "cf_pages": "Pages address (https://<project>.pages.dev)", "cf_academy": "Academy name for the printed letter",
    "cf_backup": "Backup folder (on the BitLocker-encrypted USB drive, e.g. E:\\daybook-backup)",
    "cf_mascot": "Mascot image file in brand\\ (Enter = none for now)", "cf_write": "daybook.json: {changes}",
    "cf_not_removable": "warning: {folder} is not on a removable drive. Use a folder on a BitLocker-encrypted USB drive.",
    "cf_unknown": "note: this computer cannot tell whether {folder} is removable; use a BitLocker-encrypted USB drive.",
    "cl_name": "Class name (e.g. 고1A; Enter = no more classes)", "cl_days": "Days (e.g. 월,수,금)", "cl_term": "Term (e.g. 2026-11-06..2027-02-26)",
    "cl_grade": "Grade (e.g. 고1)", "cl_start": "Start (e.g. 18:00)", "cl_end": "End (e.g. 20:00)", "cl_late": "Late after how many minutes (Enter = 10)",
    "cl_reward": "Class reward (e.g. 피자 파티; Enter = none)", "cl_write": "class {name}: {days} · {term} · {grade} · {start}–{end} · late {late} min",
    "cl_exists": "class {name} exists; skipped", "cl_none": "no class yet", "cl_ok": "{n} class(es): {names}",
    "ro_csv": "Roster CSV file (Enter = no more)", "ro_class": "Class for new students ({names})", "ro_preview": "rows: new {new}, updated {updated}, unchanged {unchanged}, skipped {skipped}",
    "ro_calls": "Add call signs (호출명) now? (yes/no)", "ro_call": "{display} ({full}) call sign (Enter = skip)", "ro_ok": "{n} active students",
    "ro_none": "no students yet", "ro_missing": "no such file: {path}",
    "co_ask": "{display} ({cls}): consent? y = yes, n = no, Enter = not back yet", "co_write": "consent: yes {y}, no {n}, not back {u}",
    "rd_ask": "Add a weekly-test round? (yes/no)", "rd_id": "Round id (e.g. Adaptive_Screeners_1S_Nov06_G10)", "rd_grade": "Grade (e.g. 1학년)",
    "rd_classes": "Classes, comma separated ({names})", "rd_n": "Number of quizzes", "rd_open": "Opens (YYYY-MM-DD HH:MM)", "rd_close": "Closes (YYYY-MM-DD HH:MM)",
    "rd_write": "round {id}: {grade} · {classes} · {n} quizzes · {open} – {close}", "rd_ok": "{n} round(s)",
    "pb_write": "publish: build the student and parent pages in dist/ (the no-PII lint), send links, consent and rounds to the Worker, back up",
    "dp_write": "deploy: npx wrangler pages deploy (dist/ → your Pages project)", "dp_ok": "deployed", "dp_fail": "deploy failed: {out}",
    "dr_ok": "no ✗: ready for Friday", "dr_fail": "{n} ✗ left: fix them (each line says how), then run setup --from doctor",
}


class Stop(Exception):
    """The teacher typed 그만 (or the scripted answers ran out in a test): save and stop."""


class SetupError(RuntimeError):
    pass


@dataclass
class IO:
    ask: Callable[[str], str] = lambda prompt: input(prompt + " > ")
    say: Callable[[str], None] = print


@dataclass
class Wizard:
    db_path: str
    config_path: str
    io: IO = field(default_factory=IO)
    english: bool = False
    state_path: str = "setup_state.json"
    wrangler_toml: str = str(ROOT / "worker" / "wrangler.toml")
    fetch: Callable[[str], bytes] | None = None        # the Worker (doctor); None = the real one
    deploy_runner: Callable | None = None              # wrangler pages deploy; None = the real one
    dist: str = "dist"

    def __post_init__(self):
        self.T = EN if self.english else strings()["setup"]
        self.state = self._load_state()
        self.cfg = load_config(self.config_path)
        self.conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------ prompts
    def t(self, key: str, **kw) -> str:
        return self.T[key].format(**kw) if kw else self.T[key]

    def say(self, text: str) -> None:
        self.io.say(text)

    def ask(self, key: str, default: str = "", **kw) -> str:
        prompt = self.t(key, **kw) + (f" [{default}]" if default else "")
        try:
            a = (self.io.ask(prompt) or "").strip()
        except (EOFError, StopIteration, IndexError) as e:
            raise Stop() from e
        if a.lower() in STOP:
            raise Stop()
        return a or default

    def yes(self, key: str, **kw) -> bool:
        while True:
            a = self.ask(key, **kw).lower()
            if a in YES:
                return True
            if a in NO:
                return False

    def preview(self, lines: list[str]) -> bool:
        """The dry run before every write: what will change, then 이대로 할까요?"""
        self.say(self.t("preview"))
        for line in lines:
            self.say("  · " + line)
        return self.yes("confirm")

    def wait(self, key: str, **kw) -> bool:
        """A dashboard / PowerShell step: print exactly what to do, wait for 다 했어요. False = 건너뛰기."""
        self.say(self.t(key, **kw))
        while True:
            a = self.ask("wait").lower()
            if a in DONE:
                return True
            if a in SKIP:
                return False

    def show(self, lines: list[D.Line]) -> str:
        """Prints doctor lines; returns the worst status."""
        for x in lines:
            self.say(f"  {x.status} {x.label}" + (f" — {x.detail}" if x.detail else "") + (f"\n      → {x.fix}" if x.fix and x.status != D.OK else ""))
        st = [x.status for x in lines]
        return D.FAIL if D.FAIL in st else D.WARN if D.WARN in st else D.OK

    # ------------------------------------------------------------ state and config
    def _load_state(self) -> dict:
        p = Path(self.state_path)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"done": {}}
        except ValueError:
            return {"done": {}}

    def _save_state(self) -> None:
        Path(self.state_path).write_text(json.dumps(self.state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="")

    def mark(self, step: str, status: str) -> None:
        self.state["done"][step] = {"status": status, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        self._save_state()

    def write_config(self, changes: dict) -> None:
        p = Path(self.config_path)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        for k, v in changes.items():
            if isinstance(v, dict) and isinstance(data.get(k), dict):
                data[k] = {**data[k], **v}
            else:
                data[k] = v
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
        self.cfg = load_config(self.config_path)

    def db(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = dbmod.connect(self.db_path)
            dbmod.migrate(self.conn)
        return self.conn

    def _guard(self) -> None:
        if Path(self.config_path).name == "daybook.demo.json":
            raise SetupError(self.t("demo_cfg"))
        p = Path(self.db_path)
        if p.name == "demo.sqlite":
            raise SetupError(self.t("demo_db"))
        if p.exists():
            c = dbmod.connect(p)
            try:
                if dbmod.is_initialized(c) and c.execute("SELECT 1 FROM events WHERE type = 'demo_seed' LIMIT 1").fetchone():
                    raise SetupError(self.t("demo_db"))
            finally:
                c.close()

    # ------------------------------------------------------------ the run
    def run(self, start: str | None = None) -> bool:
        """True when every step is done. `start` (--from) redoes that step, then continues with what is not done."""
        self._guard()
        self.say(self.t("title"))
        self.say(self.t("intro"))
        if start:
            if start not in STEPS:
                raise SetupError(f"--from: one of {', '.join(STEPS)}")
            self.state["done"].pop(start, None)
        todo = [s for s in STEPS if s not in self.state["done"]]
        if start:
            todo = [start] + [s for s in todo if s != start and STEPS.index(s) > STEPS.index(start)] + \
                   [s for s in todo if s != start and STEPS.index(s) < STEPS.index(start)]
        if not start and todo and len(todo) < len(STEPS):
            self.say(self.t("resume", step=self.t("t_" + todo[0]), n=len(STEPS) - len(todo), total=len(STEPS)))
        for step in todo:
            self.say("")
            self.say(self.t("header", n=STEPS.index(step) + 1, total=len(STEPS), title=self.t("t_" + step)))
            try:
                status = getattr(self, "step_" + step)()
            except Stop:
                self._save_state()
                self.say(self.t("stopped", step=self.t("t_" + step)))
                return False
            if status == D.FAIL:
                self.say(self.t("stopped", step=self.t("t_" + step)))
                return False
            self.mark(step, status)
        self.say("")
        self.say(self.t("all_done"))
        return True

    # ------------------------------------------------------------ steps
    def step_db(self) -> str:
        p = Path(self.db_path)
        if p.exists():
            c = self.db()
            if dbmod.is_initialized(c):
                n_cls = c.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
                n_st = c.execute("SELECT COUNT(*) FROM students").fetchone()[0]
                if n_cls or n_st:                          # real records: never re-initialized
                    self.say(self.t("db_has", path=p, classes=n_cls, students=n_st))
                    if self.yes("db_backup"):
                        from . import backup as B
                        r = B.run(self.db_path, self.cfg)
                        self.say(self.t("db_backed", path=r.path))
                    return self.show(D.check_db(c))
        if not self.preview([self.t("db_new", path=p.resolve())]):
            raise Stop()
        c = self.db()
        dbmod.init(c)
        return self.show(D.check_db(c))

    def step_subdomain(self) -> str:
        if not self.wait("sub_do"):
            return D.WARN
        return self.show([D.Line(D.OK, self.t("t_subdomain"), self.t("sub_note"))])

    def _toml_id(self) -> str:
        text = Path(self.wrangler_toml).read_text(encoding="utf-8")
        m = re.search(r'^database_id\s*=\s*"([^"]*)"', text, re.M)
        return m.group(1) if m else ""

    def step_d1(self) -> str:
        if not UUID_RE.match(self._toml_id()):
            if not self.wait("d1_do"):
                return D.WARN
            while True:
                did = self.ask("d1_ask").lower()
                if UUID_RE.match(did):
                    break
                self.say(self.t("d1_bad"))
            if not self.preview([self.t("d1_write", id=did)]):
                raise Stop()
            p = Path(self.wrangler_toml)
            p.write_text(re.sub(r'^(database_id\s*=\s*)"[^"]*"', lambda m: f'{m.group(1)}"{did}"', p.read_text(encoding="utf-8"), count=1, flags=re.M),
                         encoding="utf-8", newline="")
        did = self._toml_id()
        return self.show([D.Line(D.OK, "D1", self.t("d1_ok", id=did)) if UUID_RE.match(did) else
                          D.Line(D.FAIL, "D1", self.t("d1_missing"))])

    def step_token(self) -> str:
        if self.cfg.teacher_token:
            self.say(self.t("tok_have"))
        else:
            token = secrets.token_urlsafe(32)
            if not self.preview([self.t("tok_write")]):
                raise Stop()
            self.write_config({"teacher_token": token})
        if not self.wait("tok_do", token=self.cfg.teacher_token):
            return D.WARN
        return self.show([x for x in D.check_config(self.cfg) if x.label == "teacher_token"])

    def step_worker(self) -> str:
        if not self.wait("wk_do"):
            return D.WARN
        while True:
            url = self.ask("wk_ask", self.cfg.worker_url).rstrip("/")
            if re.match(r"^https?://[^\s/]+", url):
                break
            self.say(self.t("wk_bad"))
        if url != self.cfg.worker_url:
            if not self.preview([self.t("wk_write", url=url)]):
                raise Stop()
            self.write_config({"worker_url": url})
        while True:
            st = self.show([x for x in D.check_worker(self.db(), self.cfg, self.fetch) if x.label != "student links on the Worker"])
            if st != D.FAIL or not self.yes("retry"):
                return st

    def step_vapid(self) -> str:
        if not self.yes("vp_ask"):
            return D.OK
        if not self.cfg.extra.get("vapid_private"):
            subject = self.ask("vp_subject")
            if not self.preview([self.t("vp_write")]):
                raise Stop()
            from . import push as P
            P.write_keys(self.cfg, subject if subject.startswith(("mailto:", "https://")) else "mailto:" + subject)
            self.cfg = load_config(self.config_path)
        if not self.wait("vp_do"):
            return D.WARN
        from . import push as P
        from .pull import PullError
        try:
            ok = bool(P.count(self.db(), self.cfg).get("vapid"))
        except PullError:
            ok = False
        return self.show([D.Line(D.OK if ok else D.WARN, "push", self.t("vp_ok" if ok else "vp_not"))])

    def backup_warning(self, folder: str) -> str | None:
        from . import backup as B
        rem = B.is_removable(Path(folder))
        if rem is False:
            return self.t("cf_not_removable", folder=folder)
        if rem is None:
            return self.t("cf_unknown", folder=folder)
        return None

    def step_config(self) -> str:
        e = self.cfg.extra
        pages = self.ask("cf_pages", self.cfg.pages_url or "https://daybook.pages.dev").rstrip("/")
        academy = self.ask("cf_academy", str(e.get("academy") or ""))
        backup_dir = self.ask("cf_backup", str(e.get("backup_dir") or ""))
        brand = e.get("brand") if isinstance(e.get("brand"), dict) else {}
        mascot = self.ask("cf_mascot", str(brand.get("mascot") or ""))
        warn = self.backup_warning(backup_dir) if backup_dir else None
        if warn:
            self.say(warn)
        changes = {"pages_url": pages, "academy": academy, "practice_mode": "korea",
                   "brand": {"mascot_px": int(brand.get("mascot_px") or 36), **({"mascot": mascot} if mascot else {})}}
        if backup_dir:
            changes["backup_dir"] = backup_dir
        if not self.preview([self.t("cf_write", changes=json.dumps(changes, ensure_ascii=False))]):
            raise Stop()
        self.write_config(changes)
        lines = [x for x in D.check_config(self.cfg) if x.label in ("daybook.json", "pages_url")]     # this step's part
        if warn:
            lines.append(D.Line(D.WARN, "backup drive", warn))
        return self.show(lines)

    def step_classes(self) -> str:
        from . import ops
        c = self.db()
        while True:
            name = self.ask("cl_name")
            if not name:
                break
            if c.execute("SELECT 1 FROM classes WHERE name = ?", (name,)).fetchone():
                self.say(self.t("cl_exists", name=name))
                continue
            v = {"days": self.ask("cl_days"), "term": self.ask("cl_term"), "grade": self.ask("cl_grade"),
                 "start": self.ask("cl_start"), "end": self.ask("cl_end"), "late": self.ask("cl_late", "10"), "reward": self.ask("cl_reward")}
            if not self.preview([self.t("cl_write", name=name, **v)]):
                self.say(self.t("again"))
                continue
            try:
                cls = ops.save_class(c, name, create=True, term=v["term"], days=v["days"], grade=v["grade"] or None,
                                     start_time=v["start"] or None, reward_label=v["reward"] or None)
                pol = ops.class_policy(c, cls, {"end_time": v["end"], "late_min": int(v["late"] or 10)})
                if pol:
                    c.execute(f"UPDATE classes SET {', '.join(k + ' = ?' for k in pol)} WHERE id = ?", (*pol.values(), cls["id"]))
                c.commit()
            except (ops.OpError, ValueError) as err:
                c.rollback()
                self.say(f"  {D.FAIL} {err}")
                continue
        names = [r[0] for r in c.execute("SELECT name FROM classes ORDER BY id")]
        return self.show([D.Line(D.OK, self.t("t_classes"), self.t("cl_ok", n=len(names), names=", ".join(names))) if names else
                          D.Line(D.FAIL, self.t("t_classes"), self.t("cl_none"))])

    def step_roster(self) -> str:
        from . import importers, ops
        c = self.db()
        names = [r[0] for r in c.execute("SELECT name FROM classes ORDER BY id")]
        mapping = importers.load_mapping(ROOT / "mappings" / "roster.json")
        while True:
            path = self.ask("ro_csv")
            if not path:
                break
            if not Path(path).is_file():
                self.say(self.t("ro_missing", path=path))
                continue
            cname = self.ask("ro_class", names[0] if len(names) == 1 else "", names=", ".join(names))
            try:
                cls = ops.get_class(c, cname)
                rep = importers.import_roster(c, path, mapping, cls["id"], dry_run=True)
            except (ops.OpError, ValueError, UnicodeDecodeError) as err:
                self.say(f"  {D.FAIL} {err}")
                continue
            counts = {k: sum(1 for r in rep.rows if r["status"] == k) for k in ("new", "updated", "unchanged", "skipped")}
            rows = [f"{r['status']}: {r['name']}" + (f" ({r['ext_id']})" if r.get("ext_id") else "") + (f" · {r['why']}" if r.get("why") else "")
                    for r in rep.rows][:40]
            if not self.preview([self.t("ro_preview", **counts)] + rows + [f"warning: {w}" for w in rep.warnings]):
                self.say(self.t("again"))
                continue
            importers.import_roster(c, path, mapping, cls["id"])
            c.commit()
            if self.yes("ro_calls"):
                for s in c.execute("SELECT * FROM students WHERE class_id = ? AND active = 1 ORDER BY id", (cls["id"],)).fetchall():
                    while True:
                        cs = self.ask("ro_call", s["callsign"] or "", display=s["display_name"], full=s["full_name"])
                        if not cs or cs == (s["callsign"] or ""):
                            break
                        try:
                            ops.edit_student(c, s["slug"], callsign=cs)
                            c.commit()
                            break
                        except ops.OpError as err:
                            self.say(f"  {D.FAIL} {err}")
        n = c.execute("SELECT COUNT(*) FROM students WHERE active = 1").fetchone()[0]
        return self.show([D.Line(D.OK, self.t("t_roster"), self.t("ro_ok", n=n)) if n else D.Line(D.FAIL, self.t("t_roster"), self.t("ro_none"))])

    def step_consent(self) -> str:
        from . import ops
        c = self.db()
        rows = c.execute("""SELECT s.*, k.name AS class_name FROM students s LEFT JOIN classes k ON k.id = s.class_id
                            WHERE s.active = 1 ORDER BY s.class_id, s.id""").fetchall()
        picks = {}
        for s in rows:
            while True:
                a = self.ask("co_ask", display=s["display_name"], cls=s["class_name"] or "-").lower()
                if a in ("", "y", "yes", "네", "동의", "n", "no", "아니요", "안 함"):
                    break
            picks[s["slug"]] = "yes" if a in ("y", "yes", "네", "동의") else "no" if a else s["consent"]
        vals = list(picks.values())
        if not self.preview([self.t("co_write", y=vals.count("yes"), n=vals.count("no"), u=len(vals) - vals.count("yes") - vals.count("no"))]):
            raise Stop()
        for slug, v in picks.items():
            if v != c.execute("SELECT consent FROM students WHERE slug = ?", (slug,)).fetchone()[0]:
                ops.edit_student(c, slug, consent=v)
        c.commit()
        return self.show(D.check_consent(c))

    def step_rounds(self) -> str:
        from . import rounds as R
        c = self.db()
        names = [r[0] for r in c.execute("SELECT name FROM classes ORDER BY id")]
        while self.yes("rd_ask"):
            v = {"id": self.ask("rd_id"), "grade": self.ask("rd_grade"), "classes": self.ask("rd_classes", ",".join(names), names=", ".join(names)),
                 "n": self.ask("rd_n", "20"), "open": self.ask("rd_open"), "close": self.ask("rd_close")}
            if not self.preview([self.t("rd_write", **v)]):
                self.say(self.t("again"))
                continue
            try:
                R.add(c, v["id"], v["grade"] or None, v["classes"], int(v["n"]), v["open"], v["close"])
                c.commit()
            except (R.RoundError, ValueError) as err:
                c.rollback()
                self.say(f"  {D.FAIL} {err}")
        n = c.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]
        return self.show([D.Line(D.OK if n else D.WARN, self.t("t_rounds"), self.t("rd_ok", n=n))])

    def step_publish(self) -> str:
        from . import teacher as T
        if not self.preview([self.t("pb_write")]):
            raise Stop()
        res = T.publish(self.db(), self.cfg, Path(self.dist), db_path=self.db_path)
        for line in res["steps"]:
            self.say("  " + line)
        for f in res["findings"]:
            self.say("  " + f)
        lines = D.check_dist(self.db(), self.dist)
        if self.cfg.worker and self.cfg.teacher_token:
            lines += [x for x in D.check_worker(self.db(), self.cfg, self.fetch) if x.label == "student links on the Worker"]
        return self.show(lines) if res["ok"] else D.FAIL

    def step_deploy(self) -> str:
        from . import teacher as T
        if not self.preview([self.t("dp_write")]):
            raise Stop()
        try:
            res = T.deploy(self.cfg, runner=self.deploy_runner, out=self.dist)
        except T.TeacherError as err:
            res = {"ok": False, "output": str(err)}
        if res.get("ok"):
            dbmod.log_event(self.db(), "deploy", {"project": self.cfg.extra.get("pages_project") or "daybook"})
            self.db().commit()
            return self.show([D.Line(D.OK, self.t("t_deploy"), self.t("dp_ok"))])
        return self.show([D.Line(D.FAIL, self.t("t_deploy"), self.t("dp_fail", out=str(res.get("output", ""))[-300:]))])

    def step_doctor(self) -> str:
        lines = D.run(self.db(), self.cfg, self.db_path, self.dist, fetch=self.fetch)
        st = self.show(lines)
        n = sum(1 for x in lines if x.status == D.FAIL)
        self.say(self.t("dr_ok") if not n else self.t("dr_fail", n=n))
        return st
