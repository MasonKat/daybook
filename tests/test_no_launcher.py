"""H1 (Windows): never call pip's generated launcher. Norton on the teacher's laptop blocks it and can kill the terminal.
Every Windows caller runs `python.exe -m console` (and `python.exe -m pytest`) instead."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = re.compile(r"daybook\.exe|Scripts[\\/]+(?:daybook|pytest)\b", re.I)


def test_no_generated_launcher_anywhere():
    files = [*ROOT.glob("scripts/*.ps1"), ROOT / "tests" / "e2e" / "check.cjs", *ROOT.glob("console/*.py"),
             ROOT / "README.md", ROOT / "HANDOFF.md", ROOT / "docs" / "운영가이드.md", ROOT / "Makefile"]
    hits = [f"{f.relative_to(ROOT)}:{i}: {line.strip()}" for f in files
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if LAUNCHER.search(line)]
    assert not hits, "\n".join(hits)


def test_python_dash_m_console_works():
    r = subprocess.run([sys.executable, "-m", "console", "--help"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0 and "doctor" in r.stdout and "round" in r.stdout
