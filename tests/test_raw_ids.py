"""Students and parents never see an internal round/assignment id (DESIGN v2 §1)."""
import json
import re
import shutil
import subprocess
from datetime import date, datetime

import pytest

from console import generate, lint, ops
from console.db import KST

from conftest import TODAY

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=KST)
RAW = "Adaptive_Screeners_3S_Nov06_G10"


@pytest.mark.parametrize("text", [RAW, "round_X_2026-11-01", "Weekly_G11", "rounds.json", "set_G9 check"])
def test_lint_catches_raw_ids(tmp_path, text):
    (tmp_path / "p" / "x").mkdir(parents=True)
    (tmp_path / "p" / "x" / "index.html").write_text(f"<title>AMP 학습 기록</title><p>{text}</p>", encoding="utf-8")
    assert "raw_id" in {f.kind for f in lint.errors(lint.lint_dir(tmp_path, [], "AMP 학습 기록"))}


def test_lint_allows_ordinary_text(tmp_path):
    (tmp_path / "s" / "x").mkdir(parents=True)
    (tmp_path / "s" / "x" / "index.html").write_text(
        '<title>AMP 학습 기록</title><link rel="manifest" href="manifest.webmanifest"><p>11/6 과제 · G10반</p>',
        encoding="utf-8")
    assert not lint.errors(lint.lint_dir(tmp_path, [], "AMP 학습 기록"))


def test_raw_id_in_a_note_stops_publish(conn, cfg, klass, tmp_path):
    _, studs = klass
    ops.add_note(conn, studs[0]["id"], "2026-W45", f"{RAW} 다시 풀기")
    conn.commit()
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW)
    assert not res.ok and "raw_id" in {f.kind for f in lint.errors(res.findings)}


def test_set_labels_hide_the_id(conn, cfg, klass, tmp_path):
    cls, studs = klass
    ops.ensure_session(conn, cls["id"], date(2026, 11, 3), RAW)
    ops.ensure_session(conn, cls["id"], date(2026, 11, 6), "Adaptive_Screeners_3S_Nov06b_G10", topic="금요일 프린트")
    conn.commit()
    cfg.assignments_path = str(tmp_path / "none.json")
    labels = generate.set_labels(conn, cfg, cls["id"])
    assert labels[generate.set_hash(RAW)] == "11/3 과제"
    assert labels[generate.set_hash("Adaptive_Screeners_3S_Nov06b_G10")] == "금요일 프린트"
    (tmp_path / "assignments.json").write_text(json.dumps({RAW: "11/3 프린트", "_comment": "x"}), encoding="utf-8")
    cfg.assignments_path = str(tmp_path / "assignments.json")
    assert generate.set_labels(conn, cfg, cls["id"])[generate.set_hash(RAW)] == "11/3 프린트"
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=NOW)
    assert res.ok, [str(f) for f in res.findings]
    html = (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert "Adaptive" not in html and "11/3 프린트" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_python_and_js_hash_agree():
    ids = [RAW, "", "abc", "x-y_z"]
    js = ("function fnv(str){var h=0x811c9dc5;for(var i=0;i<str.length;i++){h^=str.charCodeAt(i);"
          "h=Math.imul(h,0x01000193)>>>0;}return h.toString(36);}"
          f"console.log(JSON.stringify({json.dumps(ids)}.map(fnv)))")
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out == [generate.set_hash(i) for i in ids]
    src = open(generate.STATIC / "app.js", encoding="utf-8").read()
    assert re.search(r"h \^= str\.charCodeAt\(i\); h = Math\.imul\(h, 0x01000193\) >>> 0;", src)
