"""F20 `setup`: the Thursday checklist as a wizard. Every external call is mocked (wrangler, the Worker, the Pages
deploy); synthetic data only."""
import json
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from console import backup as B, db as dbmod, doctor as D, pull as pullmod, setup as SU
from console.config import ROOT
from console.ops import tokmap

CSV = "Number,Name,Student ID,Status,Phone Number\n1,한가람,S901,재원,010-4821-7735\n2,윤도담,S902,재원,010-5937-2164\n3,서하늘,S903,재원,010-6372-9048\n"
D1 = "12345678-1234-1234-1234-1234567890ab"


class Script:
    """Scripted answers; running out means the teacher stopped (like 그만)."""
    def __init__(self, answers):
        self.answers, self.asked, self.out = list(answers), [], []

    def ask(self, prompt):
        self.asked.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def say(self, text):
        self.out.append(text)

    @property
    def text(self):
        return "\n".join(self.out)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    toml = tmp_path / "wrangler.toml"
    shutil.copy(ROOT / "worker" / "wrangler.toml", toml)
    (tmp_path / "roster.csv").write_text(CSV, encoding="utf-8")
    sent = []
    monkeypatch.setattr(pullmod, "http_put_json", lambda cfg, path, payload, method="PUT": sent.append(path) or {"n": len(payload.get("tokmap", {}))})
    monkeypatch.setattr(B, "is_removable", lambda folder: True)
    db = tmp_path / "daybook.sqlite"

    def fetch(path):                                       # the Worker, as the doctor sees it
        if path.startswith("/health"):
            return json.dumps({"ok": True, "version": D.MIN_WORKER_VERSION, "d1": True}).encode()
        if path.startswith("/tokmap-info"):
            c = sqlite3.connect(db); c.row_factory = sqlite3.Row
            tm = tokmap(c); c.close()
            return json.dumps({"n": len(tm), "hash": D.tokmap_hash(tm)}).encode()
        if path.startswith("/submissions"):
            return json.dumps({"items": [], "now": "2026-11-05T12:00:00.000Z"}).encode()
        raise pullmod.PullError("Worker answered 404")

    import console.teacher as T
    monkeypatch.setattr(T, "http_fetch", lambda cfg: fetch)          # publish's own pull: the same fake Worker
    deployed = []
    runner = lambda cmd: deployed.append(cmd) or SimpleNamespace(returncode=0, stdout="✨ Deployment complete", stderr="")   # noqa: E731

    def make(answers, **kw):
        io = Script(answers)
        w = SU.Wizard(str(db), str(tmp_path / "daybook.json"), io=SU.IO(ask=io.ask, say=io.say), state_path=str(tmp_path / "setup_state.json"),
                      wrangler_toml=str(toml), fetch=fetch, deploy_runner=runner, dist=str(tmp_path / "dist"), **kw)
        return w, io
    return SimpleNamespace(tmp=tmp_path, db=db, toml=toml, make=make, sent=sent, deployed=deployed)


FIRST_FOUR = ["네",                                         # 1 db: create it
              "다 했어요",                                   # 2 subdomain
              "아니요", "다 했어요", D1, "네",               # 3 d1: by hand (no auto), id -> wrangler.toml
              "네", "아니요", "다 했어요"]                   # 4 token: write, then `wrangler secret put` by hand
REST = ["아니요", "다 했어요", "https://w.example", "네",    # 5 worker (by hand)
        "아니요",                                            # 6 vapid: not now
        "https://p.example", "AMP 수학", "usb", "", "", "네",  # 7 config (no mascot yet, 36 px)
        "고1A", "월,수,금", "2026-11-06..2027-02-26", "고1", "18:00", "20:00", "", "피자", "#16a34a", "고1 기본 월수금", "", "네", "",   # 8 classes
        "roster.csv", "", "네", "네", "가람", "", "7번", "",   # 9 roster (preview, call signs)
        "y", "n", "", "네",                                  # 10 consent
        "네", "Adaptive_Screeners_1S_Nov06_G10", "1학년", "", "20", "2026-11-06 20:00", "2026-11-08 23:59", "네", "아니요",   # 11 rounds
        "네",                                                # 12 publish
        "네"]                                                # 13 deploy (14 doctor asks nothing)


def test_full_synthetic_night(env):
    w, io = env.make(FIRST_FOUR + REST)
    assert w.run() is True, io.text
    cfg = json.loads((env.tmp / "daybook.json").read_text(encoding="utf-8"))
    assert cfg["worker_url"] == "https://w.example" and cfg["pages_url"] == "https://p.example" and len(cfg["teacher_token"]) >= 40
    assert cfg["practice_mode"] == "korea" and cfg["brand"]["mascot_px"] == 36 and cfg["backup_dir"] == "usb"
    assert f'database_id = "{D1}"' in env.toml.read_text(encoding="utf-8")
    c = dbmod.connect(env.db)
    cls = c.execute("SELECT * FROM classes").fetchone()
    assert (cls["name"], cls["start_time"], cls["end_time"], cls["late_min"], cls["grade"]) == ("고1A", "18:00", "20:00", 10, "고1")
    assert (cls["color"], cls["label"]) == ("#16a34a", "고1 기본 월수금")
    studs = c.execute("SELECT full_name, callsign, consent FROM students ORDER BY id").fetchall()
    assert [tuple(r) for r in studs] == [("한가람", "가람", "yes"), ("윤도담", None, "no"), ("서하늘", "7번", "unknown")]
    assert c.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] == 1
    assert (env.tmp / "dist" / "index.html").exists() and "/tokmap" in env.sent and env.deployed
    assert c.execute("SELECT COUNT(*) FROM events WHERE type = 'deploy'").fetchone()[0] == 1
    state = json.loads((env.tmp / "setup_state.json").read_text(encoding="utf-8"))
    assert list(state["done"]) == list(SU.STEPS)
    assert io.text.count("미리보기") >= 10                                          # a preview before every write
    assert "✗가 없어요" in io.text
    w2, io2 = env.make([])                                                          # all done: nothing asked again
    assert w2.run() is True and io2.asked == []


def test_resume_after_a_stop_at_step_5(env):
    w, io = env.make(FIRST_FOUR + ["그만"])
    assert w.run() is False
    assert "Worker 배포" in io.out[-1] and "이어서" in io.out[-1]
    state = json.loads((env.tmp / "setup_state.json").read_text(encoding="utf-8"))
    assert list(state["done"]) == ["db", "subdomain", "d1", "token"]
    w2, io2 = env.make(REST)
    assert w2.run() is True, io2.text
    assert "이어서" in io2.text.split("\n")[2] and "[5/14]" in io2.text                 # it continued at step 5
    assert "[4/14]" not in io2.text
    w3, io3 = env.make(["아니요", "다 했어요", "https://w2.example", "네"])             # --from worker: redo one step
    assert w3.run("worker") is True
    assert json.loads((env.tmp / "daybook.json").read_text(encoding="utf-8"))["worker_url"] == "https://w2.example"
    with pytest.raises(SU.SetupError):
        env.make([])[0].run("nope")


def test_refuses_to_reinit_an_existing_database_and_offers_a_backup(env):
    c = dbmod.connect(env.db); dbmod.init(c)
    c.execute("INSERT INTO classes (name, term, weekday_mask) VALUES ('고1A', '2026-11-06..2027-02-26', 21)"); c.commit(); c.close()
    (env.tmp / "daybook.json").write_text(json.dumps({"backup_dir": "usb"}), encoding="utf-8")
    w, io = env.make(["네", "그만"])                                                     # yes, back up first; then stop
    assert w.run() is False
    assert "처음부터 다시 만들지 않아요" in io.text and "백업했어요" in io.text
    assert len(B.existing(env.tmp / "usb")) == 1
    c = dbmod.connect(env.db)
    assert c.execute("SELECT COUNT(*) FROM classes").fetchone()[0] == 1                 # untouched


def test_never_touches_the_demo(env, tmp_path):
    from console import demo
    c = dbmod.connect(env.db); dbmod.init(c); demo.seed(c); c.close()
    w, _ = env.make([])
    with pytest.raises(SU.SetupError, match="체험용"):
        w.run()
    d = tmp_path / "demo.sqlite"; d.write_bytes(b"")
    w = SU.Wizard(str(d), str(tmp_path / "daybook.json"), io=SU.IO(ask=lambda p: "", say=lambda t: None), state_path=str(tmp_path / "s.json"))
    with pytest.raises(SU.SetupError):
        w.run()
    w = SU.Wizard(str(tmp_path / "x.sqlite"), str(ROOT / "demo" / "daybook.demo.json"), io=SU.IO(ask=lambda p: "", say=lambda t: None),
                  state_path=str(tmp_path / "s2.json"))
    with pytest.raises(SU.SetupError, match="체험용 설정"):
        w.run()


def test_backup_dir_warning(env, monkeypatch):
    monkeypatch.setattr(B, "is_removable", lambda folder: False)
    w, io = env.make(["https://p.example", "AMP 수학", "C:\\backup", "", "", "네"])
    assert w.step_config() == D.WARN
    assert "이동식(USB) 드라이브가 아니에요" in io.text
    monkeypatch.setattr(B, "is_removable", lambda folder: None)
    w, io = env.make(["https://p.example", "AMP 수학", "C:\\backup", "", "", "네"])
    w.step_config()
    assert "확인할 수 없어요" in io.text


def test_english_flag(env):
    w, io = env.make(["그만"], english=True)
    w.run()
    assert io.out[0] == SU.EN["title"] and "Paused" in io.out[-1]


def test_cli_entry(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from console.cli import cli
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli, ["--db", "daybook.sqlite", "--config", "daybook.json", "setup"], input="그만\n")
    assert r.exit_code == 3 and "멈췄어요" in r.output
    r = CliRunner().invoke(cli, ["--db", "daybook.sqlite", "setup", "--from", "nope"], input="")
    assert r.exit_code != 0 and "--from" in r.output


# ---------------------------------------------------------------- the rehearsal's fixes (11-05)

class FakeWrangler:
    """npx wrangler: not logged in at first; D1 'daybook' already exists; secrets arrive on stdin; deploy prints the URL."""
    def __init__(self):
        self.calls, self.logged = [], False

    def __call__(self, args, stdin):
        self.calls.append((args, stdin))
        if args == ["whoami"]:
            return (0, "Getting User settings...\nYou are not authenticated. Please run `wrangler login`.") if not self.logged else (0, "You are logged in")
        if args[:2] == ["d1", "list"]:
            return 0, json.dumps([{"uuid": D1, "name": "daybook", "created_at": "2026-11-05"}])
        if args[:2] == ["d1", "create"]:
            return 1, "A database with that name already exists"
        if args[:2] == ["secret", "put"]:
            return 0, f"✨ Success! Uploaded secret {args[2]} (value {stdin.strip()})"
        if args == ["deploy"]:
            return 0, "Uploaded daybook-worker\nhttps://daybook-worker.amp-test.workers.dev\nCurrent Version ID: x"
        return 1, "?"


def test_wrangler_steps_run_for_the_teacher_and_never_show_the_token(env):
    fw = FakeWrangler()
    w, io = env.make(["네", "다 했어요",
                      "네", "다 했어요", "네",                    # d1: auto -> login needed -> 다 했어요 -> found existing id -> write
                      "네", "네",                                  # token: write, auto
                      "네", "네",                                  # worker: auto deploy -> URL -> write
                      "그만"], wrangler=fw)
    fw_login = fw.__call__

    def logging_in(args, stdin):                                    # the teacher logs in while the wizard waits
        if args == ["whoami"] and any(a == ["whoami"] for a, _ in fw.calls):
            fw.logged = True
        return fw_login(args, stdin)
    w.wrangler = logging_in
    assert w.run() is False
    tok = json.loads((env.tmp / "daybook.json").read_text(encoding="utf-8"))["teacher_token"]
    assert tok not in io.text and SU.mask(tok) in io.text                                # masked, even in wrangler's own output
    assert ("secret", "put", "TEACHER_TOKEN") == tuple(next(a for a, _ in fw.calls if a[:2] == ["secret", "put"]))
    assert next(i for a, i in fw.calls if a[:2] == ["secret", "put"]) == tok + "\n"        # by stdin, never on the command line
    assert f'database_id = "{D1}"' in env.toml.read_text(encoding="utf-8") and "already exists" not in io.text
    assert "npx wrangler login" in io.text
    assert json.loads((env.tmp / "daybook.json").read_text(encoding="utf-8"))["worker_url"] == "https://daybook-worker.amp-test.workers.dev"


def test_pasted_lines_are_accepted_and_status_shows_the_progress(env):
    w, io = env.make(["네", "다 했어요", "아니요", "다 했어요", f"✅ database_id = \"{D1}\"", "네", "그만"])
    assert w.run() is False and f'database_id = "{D1}"' in env.toml.read_text(encoding="utf-8")
    w2, io2 = env.make([])
    w2.status()
    assert "d1" in io2.text and "token" in io2.text and "'token'" in io2.text.splitlines()[-1]


def test_roster_csv_from_excel_bom_cp949_korean_headers_blank_rows_and_call_signs(env):
    from console import importers, ops
    c = dbmod.connect(env.db); dbmod.init(c)
    cls = ops.save_class(c, "고1A", create=True, term="2026-11-06..2027-02-26", days="월,수,금")
    mapping = importers.load_mapping(ROOT / "mappings" / "roster.json")
    body = "번호, 이름 ,학번,상태,보호자 연락처,호출명\n1,한가람,S901,재원,010-4821-7735,가람\n,,,,,\n2,윤도담,S902,재원,010-5937-2164,7번\n\n3,서하늘,S903,휴원,,\n"
    for name, data in (("bom.csv", b"\xef\xbb\xbf" + body.encode("utf-8")), ("cp949.csv", body.encode("cp949"))):
        (env.tmp / name).write_bytes(data)
        rep = importers.import_roster(c, env.tmp / name, mapping, cls["id"], dry_run=True)
        assert [r["status"] for r in rep.rows] == ["new", "new", "new"], rep.rows                # blank rows ignored, not "skipped"
        assert ("CP949" in " ".join(rep.warnings)) == (name == "cp949.csv")
    importers.import_roster(c, env.tmp / "bom.csv", mapping, cls["id"])
    rows = [tuple(r) for r in c.execute("SELECT full_name, ext_id, active, callsign, parent_phone FROM students ORDER BY id")]
    assert rows == [("한가람", "S901", 1, "가람", "010-4821-7735"), ("윤도담", "S902", 1, "7번", "010-5937-2164"), ("서하늘", "S903", 0, None, None)]
    (env.tmp / "noname.csv").write_text("학번,상태\nS1,재원\n", encoding="utf-8")
    with pytest.raises(ValueError, match="name column"):
        importers.import_roster(c, env.tmp / "noname.csv", mapping, cls["id"], dry_run=True)
