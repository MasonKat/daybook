"""H5: the Windows scripts must run on Windows PowerShell 5.1 (the teacher's default shell).
They were written for 5.1 and 7 but cannot be executed in the cloud build; these static checks guard the known traps."""
import re
from pathlib import Path

SCRIPTS = sorted((Path(__file__).resolve().parent.parent / "scripts").glob("*.ps1"))


def test_scripts_exist():
    assert {p.name for p in SCRIPTS} >= {"demo.ps1", "check.ps1", "deploy-pages.ps1", "deploy-taste.ps1"}


def test_ascii_only():
    # PowerShell 5.1 reads a BOM-less script as the ANSI code page: any non-ASCII character would be mangled
    for p in SCRIPTS:
        assert all(b < 128 for b in p.read_bytes()), p.name


def test_no_stop_with_native_commands():
    for p in SCRIPTS:
        text = p.read_text(encoding="utf-8")
        assert "$ErrorActionPreference = 'Stop'" not in text, p.name
        assert "$ErrorActionPreference = 'Continue'" in text, p.name
        assert not re.search(r"^\s*node [^\n]*2>\$null", text, re.M), p.name   # the probe goes through cmd /c


def test_every_native_step_checks_the_exit_code():
    for p in SCRIPTS:
        text = p.read_text(encoding="utf-8")
        natives = re.findall(r"^\s*(\.\\\.venv\\Scripts\\\S+|npx \S+|node \S+)", text, re.M)
        if natives:
            assert "$LASTEXITCODE" in text, p.name
