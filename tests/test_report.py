"""F5: the end-of-term report (print-ready HTML) and data-based note drafts."""
from datetime import date

from click.testing import CliRunner

from console import ops
from console.cli import cli

from conftest import TODAY


def test_export_report_and_note_suggest(conn, klass, tmp_path, monkeypatch):
    cls, studs = klass
    s = studs[0]
    for d in ("2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"):
        ops.teacher_submit(conn, s["id"], date.fromisoformat(d))
    sid = ops.ensure_session(conn, cls["id"], date(2026, 11, 3))
    ops.set_attitude(conn, s["id"], sid, 3)
    ops.add_note(conn, studs[1]["id"], "2026-W45", "이미 쓴 메모")
    conn.commit()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAYBOOK_CONFIG", str(tmp_path / "none.json"))
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "export", "report", s["slug"], "--date", "2026-11-06"])
    assert r.exit_code == 0, r.output
    html = (tmp_path / "reports" / f"{s['slug']}.html").read_text(encoding="utf-8")
    assert "가람 · 학기 학습 기록" in html and "@page" in html and "<script" not in html
    assert s["full_name"] not in html and s["ext_id"] not in html
    assert "누적" in html and "3점" in html and "함께한 수업 장면" in html
    r = CliRunner().invoke(cli, ["--db", db, "note", "suggest", "고1A", "--date", "2026-11-06"])
    assert r.exit_code == 0, r.output
    lines = r.output.strip().splitlines()
    assert any(l.startswith(f'daybook note {s["slug"]} 2026-W45 "이번 주 수업일 2일 모두 제출, 태도 3/') for l in lines)
    assert not any(studs[1]["slug"] in l for l in lines)            # already has a note: skipped
    r = CliRunner().invoke(cli, ["--db", db, "note", s["slug"], "2026-W45", "직접 쓴 메모"])
    assert r.exit_code == 0 and conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 2
