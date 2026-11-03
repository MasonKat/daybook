"""F8 spike: answer-card reading on synthetic phone photos."""
import random

from click.testing import CliRunner

from console import omr
from console.cli import cli


def test_reads_synthetic_cards():
    rng = random.Random(11)
    right = total = 0
    for _ in range(6):
        img, truth = omr.synthetic_card(rng)
        got = omr.read_card(img)
        right += sum(t == g for t, g in zip(truth, got))
        total += len(truth)
    assert right / total >= 0.98, f"{right}/{total}"


def test_perspective_roundtrip():
    src = [(10, 20), (900, 40), (30, 1300), (880, 1250)]
    c = omr.perspective_coeffs(omr.FID_C, src)
    a, b, cc, d, e, f, g, h = c
    for (x, y), (u, v) in zip(omr.FID_C, src):
        k = g * x + h * y + 1
        assert abs((a * x + b * y + cc) / k - u) < 1e-6 and abs((d * x + e * y + f) / k - v) < 1e-6


def test_cli_needs_spike_and_writes_effort(conn, klass, tmp_path, monkeypatch):
    _, studs = klass
    monkeypatch.setenv("DAYBOOK_CONFIG", str(tmp_path / "none.json"))
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    r = CliRunner().invoke(cli, ["--db", db, "omr", "--image", __file__])
    assert r.exit_code != 0 and "--spike" in r.output
    img, truth = omr.synthetic_card(random.Random(3))
    p = tmp_path / "card.jpg"
    img.save(p)
    key = ",".join(str(min(t) if t else 1) for t in truth)
    r = CliRunner().invoke(cli, ["--db", db, "omr", "--spike", "--image", str(p), "--student", studs[0]["slug"],
                                 "--set", "Weekly_Card_1", "--key", key])
    assert r.exit_code == 0, r.output
    rows = conn.execute("SELECT * FROM effort WHERE source = 'card' ORDER BY CAST(item_id AS INTEGER)").fetchall()
    assert len(rows) == 20 and rows[0]["set_id"] == "Weekly_Card_1"
    for row, t in zip(rows, truth):
        assert row["attempted"] == (1 if t else 0)
        assert row["correct"] == (1 if len(t) == 1 else 0)
    r = CliRunner().invoke(cli, ["--db", db, "omr", "--spike", "--eval", "2"])
    assert r.exit_code == 0 and '"cards": 2' in r.output
