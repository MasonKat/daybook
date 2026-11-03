"""Windows has no UTF-8 default (cp1252 / cp949). Run the real CLI with a non-UTF-8 locale and Python's UTF-8 mode off:
every Korean read, write and print must still work. (An ASCII locale is stricter than cp1252, so it covers it.)"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHONUTF8", "PYTHONIOENCODING", "LC_", "LANG"))}
    env.update({"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONPATH": str(ROOT),
                "DAYBOOK_CONFIG": str(cwd / "none.json")})
    return subprocess.run([sys.executable, "-X", "utf8=0", "-c",
                           "import locale, sys; assert locale.getencoding().lower() not in ('utf-8', 'utf8'); "
                           "from console.cli import main; sys.argv = ['daybook'] + sys.argv[1:]; main()", *args],
                          cwd=cwd, env=env, capture_output=True)


def test_cli_with_a_non_utf8_locale(tmp_path):
    db = str(tmp_path / "d.sqlite")
    r = run(["--db", db, "demo", "--out", str(tmp_path / "dist")], tmp_path)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "가상전학" in r.stdout.decode("utf-8")               # Korean printed as UTF-8
    r = run(["--db", db, "roster", "import", str(ROOT / "mappings" / "roster.example.csv"),
             "--map", str(ROOT / "mappings" / "roster.json"), "--class", "고1A"], tmp_path)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    r = run(["--db", db, "weekly", "고1A"], tmp_path)
    assert r.returncode == 0 and "[AMP 주간 기록]" in r.stdout.decode("utf-8")
    html = next((tmp_path / "dist" / "p").iterdir()) / "index.html"
    assert "AMP 수학" in html.read_text(encoding="utf-8")


def test_every_text_open_names_its_encoding():
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_encoding
    assert check_encoding.offenders() == []
