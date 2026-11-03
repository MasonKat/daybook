"""Fail if any open()/read_text()/write_text() call in the Python sources lacks encoding= (Windows defaults to cp949/cp1252)."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def offenders():
    out = []
    for p in sorted(list((ROOT / "console").glob("*.py")) + list((ROOT / "tests").glob("*.py")) + list((ROOT / "scripts").glob("*.py"))):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name not in ("open", "read_text", "write_text"):
                continue
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("Image", "urllib", "webbrowser"):
                continue
            mode = node.args[1].value if name == "open" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else ""
            if "b" in str(mode):
                continue
            if not any(k.arg == "encoding" for k in node.keywords):
                out.append(f"{p.relative_to(ROOT)}:{node.lineno}: {name}() without encoding=")
    return out


if __name__ == "__main__":
    bad = offenders()
    print("\n".join(bad) or "ok: every text open/read/write names its encoding")
    sys.exit(1 if bad else 0)
