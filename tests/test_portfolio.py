"""F16.5: the synthetic portfolio export: class level, generated names, a strict leak check."""
import pytest
from click.testing import CliRunner

from console import demo, portfolio as PF
from console.cli import cli
from console.config import Config

from conftest import TODAY


def test_portfolio_is_synthetic_and_leak_checked(conn, tmp_path, monkeypatch):
    demo.seed(conn, today=TODAY)
    conn.execute("UPDATE students SET parent_phone = '010-2222-3333' WHERE id = 1")
    conn.commit()
    folder = PF.export(conn, Config(), tmp_path / "pf", TODAY)
    names = sorted(p.name for p in folder.iterdir())
    assert "comments.txt" in names and "metrics_all.csv" in names and len([n for n in names if n.startswith("metrics_class_")]) == 4
    assert len([n for n in names if n.endswith(".png")]) == 3
    comments = (folder / "comments.txt").read_text(encoding="utf-8").splitlines()
    assert len([line for line in comments if line[:3].rstrip(". ").isdigit()]) == 12
    text = "".join(p.read_text(encoding="utf-8-sig") for p in folder.iterdir() if p.suffix in (".csv", ".txt"))
    for full, disp, ext in conn.execute("SELECT full_name, display_name, ext_id FROM students"):
        assert full not in text and disp not in text and ext not in text
    assert "고1A" not in text and "Class 1" in text and "22223333" not in text.replace("-", "")
    assert PF._leaks(conn, folder) == []


def test_a_leak_stops_the_export_and_keeps_nothing(conn, tmp_path, monkeypatch):
    demo.seed(conn, today=TODAY)
    real = conn.execute("SELECT display_name FROM students LIMIT 1").fetchone()[0]
    monkeypatch.setattr(PF, "SYNTH_GIVEN", [real] * 16)              # a "generated" name that is a real one
    with pytest.raises(PF.PortfolioError):
        PF.export(conn, Config(), tmp_path / "pf", TODAY)
    assert not (tmp_path / "pf").exists()


def test_cli_requires_synthetic(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    assert CliRunner().invoke(cli, ["--db", db, "export", "portfolio"]).exit_code != 0
    r = CliRunner().invoke(cli, ["--db", db, "export", "portfolio", "--synthetic", "--date", TODAY.isoformat()])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "reports" / f"portfolio-{TODAY.isoformat()}" / "comments.txt").exists()
