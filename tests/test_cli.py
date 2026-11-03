"""CLI smoke tests (click CliRunner) on a temporary database."""
import os

import pytest
from click.testing import CliRunner

from console.cli import cli


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DAYBOOK_DB", raising=False)
    monkeypatch.setenv("DAYBOOK_CONFIG", str(tmp_path / "none.json"))
    runner = CliRunner()

    def _run(*args, ok=True):
        r = runner.invoke(cli, ["--db", str(tmp_path / "d.sqlite"), *args], catch_exceptions=False)
        if ok:
            assert r.exit_code == 0, r.output
        return r
    return _run


def test_full_week_flow(run, tmp_path):
    run("init")
    run("class", "add", "고1A", "--term", "2026-11-02..2027-02-26", "--days", "화,금", "--reward", "피자")
    run("roster", "add", "고1A", "한가람", "--ext-id", "S901")
    run("roster", "add", "고1A", "윤도담")
    r = run("roster", "list", "고1A", "--links")
    assert "가람" in r.output and "/s/" in r.output and "/p/" in r.output
    run("session", "start", "고1A", "--date", "2026-11-06", "--set", "Adaptive_Screeners_3S_Nov06_G10")
    run("score", "고1A", "2026-11-06", "가람", "3")
    run("submit", "도담", "--date", "2026-11-06", "--source", "paper")
    run("note", "가람", "2026-W45", "풀이 과정을 끝까지 적었어요.")
    r = run("import", "screeners", os.path.join(os.path.dirname(__file__), "..", "demo", "screeners"))
    assert "unmatched" in r.output and "S999" in r.output
    r = run("publish", "--date", "2026-11-07")
    assert "lint passed" in r.output
    assert len(list((tmp_path / "dist" / "s").iterdir())) == 2
    r = run("weekly", "고1A", "--date", "2026-11-07")
    assert "[AMP 주간 기록] 가람" in r.output and "태도 점수: 이번 주 3점" in r.output
    assert "한가람" not in r.output
    r = run("class", "list")
    assert "고1A" in r.output


def test_publish_exits_nonzero_when_lint_fails(run, tmp_path):
    run("init")
    run("class", "add", "B", "--term", "2026-11-02..2027-02-26", "--days", "월")
    run("roster", "add", "B", "이솔")   # 2-character name: display == full name
    r = run("publish", ok=False)
    assert r.exit_code == 1 and "STOPPED" in r.output
    run("roster", "edit", "이솔", "--display", "솔")
    assert "lint passed" in run("publish").output


def test_export_pdf_is_a_stub(run):
    run("init")
    r = run("export", "pdf", "x", ok=False)
    assert r.exit_code == 2 and "not implemented" in r.output


def test_commands_need_init(run):
    r = run("roster", "list", ok=False)
    assert r.exit_code != 0 and "daybook init" in r.output


def test_pull_without_config_explains(run):
    run("init")
    r = run("pull", ok=False)
    assert r.exit_code != 0 and "worker_url" in r.output


def test_demo_refuses_a_real_database(run):
    run("init")
    run("class", "add", "B", "--term", "2026-11-02..2027-02-26", "--days", "월")
    run("roster", "add", "B", "한가람")
    r = run("demo", ok=False)
    assert r.exit_code != 0 and "real (non-demo)" in r.output
