"""F14: weekly-test rounds and reports (주간 테스트): rounds, the Worker payload, pull, export, nothing in dist/."""
import csv
import json
from datetime import datetime, time

import pytest
from click.testing import CliRunner

from console import generate, importers, demo, pull as pullmod, rounds as R
from console.cli import cli
from console.config import ROOT, Config
from console.db import KST

from conftest import TODAY

RID = "Adaptive_Screeners_1S_Nov06_G10"
NOW = datetime(2026, 11, 6, 21, 0, tzinfo=KST)


@pytest.fixture
def rnd(conn, klass):
    cls, studs = klass
    R.add(conn, RID, "1학년", "고1A", 20, "2026-11-06 20:00", "2026-11-08 23:59")
    conn.commit()
    return cls, studs


def item(tok, **kw):
    it = {"rid": R.rid(RID), "token": tok, "incorrect": "3,7", "missed": "12", "hardest": "7,12", "reasons": "계산실수,시간부족",
          "wishlist": "풀이 영상 더", "metaphor": "마라톤", "ts": "2026-11-06T12:00:00.000Z", "updated": "2026-11-06T12:00:00.000Z", "n": 1}
    return {**it, **kw}


def test_round_add_edit_close_and_validation(conn, rnd):
    r = R.get(conn, RID)
    assert (r["grade"], r["n"], r["label"], r["open_at"], r["close_at"]) == ("1학년", 20, "11/6 주간 테스트", "2026-11-06 20:00", "2026-11-08 23:59")
    assert len(r["rid"]) == 16 and RID not in r["rid"]
    assert R.is_open(r, NOW) and not R.is_open(r, datetime(2026, 11, 6, 19, 59, tzinfo=KST))
    for bad in [dict(round_id="has space"), dict(n=0), dict(n=61), dict(grade="4학년"), dict(classes="없는반"),
                dict(open_at="11/6 20:00"), dict(close_at="2026-11-06 19:00"), dict(label="Adaptive_Screeners_X")]:
        args = {"round_id": "R2", "grade": "1학년", "classes": "고1A", "n": 10, "open_at": "2026-11-06 20:00",
                "close_at": "2026-11-07 20:00", "label": None, **bad}
        with pytest.raises(R.RoundError):
            R.add(conn, **args)
    with pytest.raises(R.RoundError):
        R.add(conn, RID, "1학년", "고1A", 20, "2026-11-06 20:00", "2026-11-08 23:59")          # exists
    R.edit(conn, RID, n=25, label="11/6 테스트")
    assert R.get(conn, RID)["n"] == 25 and R.get(conn, RID)["label"] == "11/6 테스트"
    R.close_now(conn, RID, now=datetime(2026, 11, 7, 10, 0, tzinfo=KST))
    assert R.get(conn, RID)["close_at"] == "2026-11-07 10:00"
    types = [r[0] for r in conn.execute("SELECT type FROM events WHERE type LIKE 'round_%' ORDER BY id")]
    assert types == ["round_add", "round_edit", "round_close"]


def test_worker_payload_has_hashes_and_class_keys_only(conn, rnd):
    from console.ops import class_key
    cls, _ = rnd
    p = R.worker_payload(conn, NOW)
    assert p == [{"rid": R.rid(RID), "classes": [class_key(cls["id"])], "n": 20, "label": "11/6 주간 테스트",
                  "open_from": "2026-11-06T11:00:00+00:00", "open_until": "2026-11-08T14:59:00+00:00"}]
    assert RID not in json.dumps(p) and "고1A" not in json.dumps(p, ensure_ascii=False)
    assert R.worker_payload(conn, datetime(2026, 12, 1, tzinfo=KST)) == []          # closed more than 14 days ago


def test_pull_imports_reports_and_an_edit_replaces_it(conn, rnd):
    _, studs = rnd
    tok = studs[0]["student_token"]
    assert R.apply_reports(conn, [item(tok)]) == (1, 0)
    row = conn.execute("SELECT * FROM test_reports").fetchone()
    assert json.loads(row["incorrect"]) == [3, 7] and json.loads(row["reasons"]) == ["계산실수", "시간부족"]
    assert R.apply_reports(conn, [item(tok, incorrect="1", updated="2026-11-07T01:00:00.000Z")]) == (0, 1)
    assert R.apply_reports(conn, [item(tok, incorrect="9", updated="2026-11-06T13:00:00.000Z")]) == (0, 0)   # older: ignored
    assert json.loads(conn.execute("SELECT incorrect FROM test_reports").fetchone()[0]) == [1]
    assert conn.execute("SELECT COUNT(*) FROM test_reports").fetchone()[0] == 1
    assert R.apply_reports(conn, [item("x" * 26), item(tok, rid="ffffffffffffffff")]) == (0, 0)     # unknown: skipped
    p = R.progress(conn, RID)
    assert (p["done"], p["total"]) == (1, 3) and [x["done"] for x in p["students"]] == [True, False, False]


def test_pull_fetches_reports_from_the_worker(conn, rnd):
    _, studs = rnd
    pages = {"/submissions": {"items": [], "now": "2026-11-06T12:00:00Z"}, "/checkins": {"items": []},
             "/live/results": {"items": []}, "/reports": {"items": [item(studs[1]["student_token"])]}}
    rep = pullmod.pull(conn, lambda path: json.dumps(pages[path.split("?")[0]]).encode())
    assert rep.reports == 1 and conn.execute("SELECT student_id FROM test_reports").fetchone()[0] == studs[1]["id"]


def test_export_columns_and_order_are_exact(conn, rnd, tmp_path):
    _, studs = rnd
    conn.execute("UPDATE students SET ext_id = 'S902' WHERE id = ?", (studs[1]["id"],))
    R.apply_reports(conn, [item(studs[1]["student_token"]),
                           item(studs[0]["student_token"], incorrect="", missed="", reasons="", hardest="", wishlist="", metaphor="")])
    path = R.export(conn, RID, "csv", tmp_path)
    assert path.name == f"{RID}.csv"
    raw = path.read_bytes()                       # identical bytes on every OS (the local Notion sync depends on it)
    assert raw.startswith(b"\xef\xbb\xbfStudent ID,") and b"\r" not in raw
    assert b"\r" not in R.export(conn, RID, "notion-json", tmp_path).read_bytes()
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    assert rows[0] == ["Student ID", "Name", "Grade", "Round ID", "Screening Date", "Incorrect Quiz #", "Missed Quiz #",
                       "Most Challenging Quiz", "Reasoning", "Wishlist", "metaphor"]
    assert rows[1] == ["S901", "한가람", "1학년", RID, "2026-11-06", "", "", "", "", "", ""]
    assert rows[2] == ["S902", "윤도담", "1학년", RID, "2026-11-06", "3,7", "12", "7,12", "계산실수,시간부족", "풀이 영상 더", "마라톤"]
    j = json.loads(R.export(conn, RID, "notion-json", tmp_path).read_text(encoding="utf-8"))
    assert j["columns"] == rows[0] and list(j["rows"][1]) == rows[0]
    assert j["rows"][1]["Incorrect Quiz #"] == [3, 7] and j["rows"][1]["Reasoning"] == ["계산실수", "시간부족"]
    ex = json.loads((ROOT / "mappings" / "reports.example.json").read_text(encoding="utf-8"))
    assert ex["columns"] == rows[0] and all(list(r) == rows[0] for r in ex["rows"])


def test_cli_round_and_export(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    run = lambda *a: CliRunner().invoke(cli, ["--db", db, *a])
    r = run("round", "add", RID, "--grade", "1학년", "--classes", "고1A", "--quizzes", "20", "--open", "2026-11-06 20:00",
            "--close", "2026-11-08 23:59")
    assert r.exit_code == 0, r.output
    assert "11/6 주간 테스트" in run("round", "list").output
    assert "0/3 제출" in run("round", "status", RID).output
    r = run("export", "reports", "--round", RID)
    assert r.exit_code == 0 and (tmp_path / "reports" / f"{RID}.csv").exists() and "not reported yet" in r.output
    assert run("round", "close", RID).exit_code == 0


def test_round_ids_never_reach_dist_or_the_taste_build(conn, tmp_path):
    demo.seed(conn, today=TODAY)
    importers.import_screeners(conn, ROOT / "demo" / "screeners")
    R.add(conn, RID, "1학년", "고1A,기초B", 20, "2026-11-06 20:00", "2026-11-08 23:59")
    conn.commit()
    cfg = Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t",
                 videos_path=str(ROOT / "videos.json"))
    for taste in (False, True):
        out = tmp_path / ("t" if taste else "d")
        res = generate.publish(conn, cfg, out, today=TODAY, now=NOW, taste=taste)
        assert res.ok, [str(f) for f in res.findings]
        for f in out.rglob("*"):
            if f.is_file() and f.suffix in (".html", ".js", ".json", ".css", ".webmanifest"):
                t = f.read_text(encoding="utf-8")
                assert RID not in t and R.rid(RID) not in t, f
