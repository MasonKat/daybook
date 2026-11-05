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
import shutil
import sqlite3
import subprocess
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
UUID_SEARCH = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
WORKER_URL_RE = re.compile(r"https://[a-z0-9.-]+\.workers\.dev")
# PowerShell one-liner: reads a value from daybook.json and pipes it to wrangler, so the value is never on screen
PIPE = "(Get-Content '{path}' -Raw | ConvertFrom-Json).{key} | npx wrangler secret put {name}"


def mask(value: str) -> str:
    """Never a secret in full on screen: the first 4 and the last 2 characters."""
    return (value[:4] + "…" + value[-2:]) if len(value) > 8 else "…"

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
    "d1_do": "In PowerShell, in the DAYBOOK folder:\n  cd worker\n  npx wrangler login\n  npx wrangler d1 create daybook\nIt prints a database_id (8-4-4-4-12 letters and digits).\nIf it says 'already exists': npx wrangler d1 list, and copy the id on the daybook line.",
    "d1_ask": "Paste the database_id", "d1_bad": "That is not a database_id (8-4-4-4-12).", "d1_write": "worker/wrangler.toml: database_id = {id}",
    "d1_ok": "worker/wrangler.toml has the D1 binding ({id})", "d1_missing": "worker/wrangler.toml still has no database_id",
    "tok_have": "daybook.json already has a teacher token; keeping it.", "tok_write": "daybook.json: teacher_token = (a new random token, 43 characters)",
    "wk_do": "In the worker folder run:\n  npx wrangler deploy\nIt prints the Worker address (https://….workers.dev).",
    "wk_ask": "Paste the Worker address", "wk_bad": "That is not an http(s) address.", "wk_write": "daybook.json: worker_url = {url}",
    "vp_ask": "Use push notifications? (yes/no)", "vp_subject": "Contact for the push service (mailto:you@example.com)",
    "vp_write": "daybook.json: vapid_public / vapid_private / vapid_subject (new keys)",
    "vp_do": "In PowerShell, in the worker folder, run these (each reads its value from daybook.json without showing it):\n  {cmds}\nthen once more: npx wrangler deploy",
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
    "auto_ask": "Shall I run wrangler for you? (yes = I run it, no = you run it yourself)",
    "wr_login": "wrangler is not logged in. In PowerShell, in the worker folder, run:\n  npx wrangler login\n(a browser opens: allow it)",
    "wr_fail": "wrangler did not finish (exit {rc}). The last lines:\n{out}",
    "wr_nonpx": "npx was not found: install Node.js 22 LTS (nodejs.org), open a NEW PowerShell, then run setup again.",
    "d1_found": "found the existing D1 'daybook': {id}", "d1_made": "created D1 'daybook': {id}",
    "tok_masked": "teacher token: {masked} ({n} characters, kept in daybook.json; never shown in full)",
    "tok_put": "Worker secret TEACHER_TOKEN set", "tok_manual": "In PowerShell, in the worker folder, run this one line (it reads the token from daybook.json without showing it):\n  {cmd}",
    "wk_found": "Worker address: {url}", "vp_put": "Worker secrets VAPID_PUBLIC / VAPID_PRIVATE / VAPID_SUBJECT set",
    "cf_px": "Mascot size in px (28-44; Enter = 36)", "cf_no_file": "no such file: {path} (put it in the brand folder, or Enter for none)",
    "cl_color": "Banner colour #rrggbb (Enter = automatic)", "cl_label": "Short label (e.g. 고1 기본 월수금 20시; Enter = the class name)",
    "cl_rubric": "Rubric 1점|2점|3점 (Enter = the default rubric)",
    "st_title": "setup progress ({path}):", "st_line": "  {n:>2}. {step:<10} {mark} {title}{at}", "st_next": "next: setup continues at '{step}'; redo one step: setup --from <step>",
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
    wrangler: Callable | None = None                   # (args, input) -> (returncode, output); None = npx wrangler in worker/

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

    def ask_valid(self, key: str, check: Callable[[str], str | None], default: str = "") -> str:
        """Ask until the answer is valid: a typo is caught at its own question, never after the whole form."""
        while True:
            a = self.ask(key, default)
            why = check(a)
            if not why:
                return a
            self.say(f"  {D.FAIL} {a!r} → {why}")

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

    # ------------------------------------------------------------ wrangler (the teacher's own login; secrets go in by stdin)
    def run_wrangler(self, args: list[str], stdin: str | None = None, secret: str | None = None) -> tuple[int, str]:
        if self.wrangler:
            rc, out = self.wrangler(args, stdin)
        else:
            npx = shutil.which("npx")
            if not npx:
                return 127, self.t("wr_nonpx")
            try:
                r = subprocess.run([npx, "wrangler", *args], cwd=Path(self.wrangler_toml).parent, input=stdin, capture_output=True,
                                   timeout=600, encoding="utf-8", errors="replace")
                rc, out = r.returncode, (r.stdout or "") + (r.stderr or "")
            except (OSError, subprocess.SubprocessError) as e:
                rc, out = 1, str(e)
        if secret:
            out = out.replace(secret, mask(secret))
        return rc, out

    def logged_in(self) -> bool:
        """wrangler whoami; when it is not logged in, say how and wait (다 했어요 = check again, 건너뛰기 = manual path)."""
        while True:
            rc, out = self.run_wrangler(["whoami"])
            if rc == 0 and "not authenticated" not in out.lower():
                return True
            if rc == 127:
                self.say(self.t("wr_nonpx"))
                return False
            if not self.wait("wr_login"):
                return False

    def fail_lines(self, rc: int, out: str) -> None:
        self.say(self.t("wr_fail", rc=rc, out="\n".join("    " + x for x in out.strip().splitlines()[-8:])))

    def put_secret(self, name: str, value: str) -> bool:
        rc, out = self.run_wrangler(["secret", "put", name], stdin=value + "\n", secret=value)
        if rc != 0:
            self.fail_lines(rc, out)
        return rc == 0

    # ------------------------------------------------------------ the run
    def status(self) -> None:
        """setup --status: every step, done or not, and where the next run continues."""
        self.say(self.t("st_title", path=self.state_path))
        todo = None
        for i, st in enumerate(STEPS, 1):
            d = self.state["done"].get(st)
            if not d and todo is None:
                todo = st
            self.say(self.t("st_line", n=i, step=st, mark=(d or {}).get("status", "·"), title=self.t("t_" + st),
                            at=f"  ({d['at']})" if d else ""))
        if todo:
            self.say(self.t("st_next", step=todo))

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

    def d1_auto(self) -> str:
        """The existing D1 'daybook' (a re-run, or one made earlier), else a new one: never a second 'already exists'."""
        if not self.logged_in():
            return ""
        rc, out = self.run_wrangler(["d1", "list", "--json"])
        if rc == 0:
            try:
                for x in json.loads(out[out.index("["):]):
                    if x.get("name") == "daybook" and UUID_RE.match(str(x.get("uuid", ""))):
                        self.say(self.t("d1_found", id=x["uuid"]))
                        return x["uuid"]
            except ValueError:
                pass
        rc, out = self.run_wrangler(["d1", "create", "daybook"])
        m = UUID_SEARCH.search(out)
        if rc == 0 and m:
            self.say(self.t("d1_made", id=m.group(0)))
            return m.group(0)
        self.fail_lines(rc, out)
        return ""

    def step_d1(self) -> str:
        if not UUID_RE.match(self._toml_id()):
            did = self.d1_auto() if self.yes("auto_ask") else ""
            if not did:
                if not self.wait("d1_do"):
                    return D.WARN
                while True:
                    m = UUID_SEARCH.search(self.ask("d1_ask").lower())     # a whole pasted line is fine
                    if m:
                        did = m.group(0)
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
        tok = self.cfg.teacher_token
        self.say(self.t("tok_masked", masked=mask(tok), n=len(tok)))
        if self.yes("auto_ask") and self.logged_in() and self.put_secret("TEACHER_TOKEN", tok):
            self.say(f"  {D.OK} " + self.t("tok_put"))
        elif not self.wait("tok_manual", cmd=PIPE.format(path=Path(self.config_path).resolve(), key="teacher_token", name="TEACHER_TOKEN")):
            return D.WARN
        return self.show([x for x in D.check_config(self.cfg) if x.label == "teacher_token"])

    def step_worker(self) -> str:
        url = ""
        if self.yes("auto_ask") and self.logged_in():
            rc, out = self.run_wrangler(["deploy"])
            m = WORKER_URL_RE.search(out)
            if rc == 0 and m:
                url = m.group(0)
                self.say(self.t("wk_found", url=url))
            else:
                self.fail_lines(rc, out)
        if not url:
            if not self.wait("wk_do"):
                return D.WARN
            while True:
                a = self.ask("wk_ask", self.cfg.worker_url)
                m = WORKER_URL_RE.search(a) or re.match(r"^https?://[^\s/]+", a)   # a whole pasted line is fine
                if m:
                    url = m.group(0).rstrip("/")
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
        e = self.cfg.extra
        if self.yes("auto_ask") and self.logged_in() and all(self.put_secret(n, str(e.get(k) or "")) for n, k in (
                ("VAPID_PUBLIC", "vapid_public"), ("VAPID_PRIVATE", "vapid_private"), ("VAPID_SUBJECT", "vapid_subject"))):
            self.say(f"  {D.OK} " + self.t("vp_put"))
            rc, out = self.run_wrangler(["deploy"])
            if rc != 0:
                self.fail_lines(rc, out)
        elif not self.wait("vp_do", cmds="\n  ".join(PIPE.format(path=Path(self.config_path).resolve(), key=k, name=n) for n, k in (
                ("VAPID_PUBLIC", "vapid_public"), ("VAPID_PRIVATE", "vapid_private"), ("VAPID_SUBJECT", "vapid_subject")))):
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
        while True:
            mascot = self.ask("cf_mascot", str(brand.get("mascot") or ""))
            if not mascot or any(c.is_file() for c in (Path(mascot), Path.cwd() / mascot, ROOT / mascot, ROOT / "brand" / mascot)):
                break
            self.say(self.t("cf_no_file", path=mascot))
        if mascot and not Path(mascot).is_file() and (ROOT / "brand" / mascot).is_file() and not (ROOT / mascot).is_file():
            mascot = "brand/" + mascot                         # just the file name of something in brand\\
        px = self.ask("cf_px", str(brand.get("mascot_px") or 36))
        px = int(px) if px.isdigit() and 28 <= int(px) <= 44 else 36
        warn = self.backup_warning(backup_dir) if backup_dir else None
        if warn:
            self.say(warn)
        changes = {"pages_url": pages, "academy": academy, "practice_mode": "korea",
                   "brand": {"mascot_px": px, **({"mascot": mascot} if mascot else {})}}
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
            from . import models as M
            hhmm = lambda x: None if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", x) else "HH:MM (예: 18:00)"   # noqa: E731

            def days_ok(x):
                try:
                    return None if x and M.mask_from_days(x) else "월,수,금"
                except (ValueError, KeyError):
                    return "월,수,금"
            v = {"days": self.ask_valid("cl_days", days_ok),
                 "term": self.ask_valid("cl_term", lambda x: None if M.parse_term(x) != (None, None) else "2026-11-06..2027-02-26"),
                 "grade": self.ask("cl_grade"), "start": self.ask_valid("cl_start", hhmm), "end": self.ask_valid("cl_end", hhmm),
                 "late": self.ask_valid("cl_late", lambda x: None if x.isdigit() and 0 < int(x) <= 60 else "1-60", "10"),
                 "reward": self.ask("cl_reward"),
                 "color": self.ask_valid("cl_color", lambda x: None if not x or ops.COLOR_RE.match(x) else "#16a34a"),
                 "label": self.ask("cl_label"), "rubric": self.ask("cl_rubric")}
            extra = [x for x in (v["color"] and f"colour {v['color']}", v["label"] and f"label {v['label']}",
                                 v["rubric"] and f"rubric {v['rubric']}") if x]
            if not self.preview([self.t("cl_write", name=name, **v)] + extra):
                self.say(self.t("again"))
                continue
            try:
                cls = ops.save_class(c, name, create=True, term=v["term"], days=v["days"], grade=v["grade"] or None,
                                     start_time=v["start"] or None, reward_label=v["reward"] or None)
                pol = ops.class_policy(c, cls, {"end_time": v["end"], "late_min": int(v["late"] or 10)})
                if pol:
                    c.execute(f"UPDATE classes SET {', '.join(k + ' = ?' for k in pol)} WHERE id = ?", (*pol.values(), cls["id"]))
                cls = c.execute("SELECT * FROM classes WHERE id = ?", (cls["id"],)).fetchone()
                ops.set_class_look(c, cls, color=v["color"] or None, label=v["label"] or None)     # F21.3
                if v["rubric"]:                                                                       # F22.1
                    ops.set_goal_rules(c, cls, None, None, [x.strip() for x in v["rubric"].split("|")])
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
        if self.deploy_runner is None and not self.logged_in():          # the teacher's own wrangler login comes first
            return self.show([D.Line(D.FAIL, self.t("t_deploy"), self.t("wr_login").splitlines()[0])])
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
