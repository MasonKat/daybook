"""No-PII lint over generated output (BUILD_BRIEF §11/§12).

Errors (publish stops): phone numbers, any student's full_name / ext_id / parent_phone,
a non-generic <title> or og: tag, third-party resource references, files over 100 KB.
Warnings (printed only): another student's display name on a per-student page.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

PHONE_RE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
OG_RE = re.compile(r'<meta[^>]+property=["\']og:[^"\']+["\'][^>]*content=["\']([^"\']*)["\']', re.I)
# resources the browser fetches automatically (not plain <a href> navigation)
RESOURCE_RE = re.compile(
    r'(?:<(?:script|img|iframe|source|video|audio|embed)[^>]*\ssrc|<link[^>]*\shref)\s*=\s*["\']\s*((?:https?:)?//[^"\']+)',
    re.I,
)
CSS_URL_RE = re.compile(r"url\(\s*['\"]?\s*((?:https?:)?//[^)'\"]+)", re.I)
IMPORT_RE = re.compile(r"@import\s+(?:url\()?['\"]?((?:https?:)?//[^)'\";]+)", re.I)
TEXT_SUFFIXES = {".html", ".htm", ".css", ".js", ".json", ".webmanifest", ".txt", ".svg", ".xml", ""}
MAX_BYTES = 100 * 1024


@dataclass
class Finding:
    level: str  # "error" | "warning"
    file: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.file}: {self.kind}: {self.detail}"


@dataclass
class PrivateRow:
    id: int
    full_name: str
    display_name: str
    ext_id: str | None
    parent_phone: str | None
    student_token: str
    parent_token: str


def private_rows(conn: sqlite3.Connection) -> list[PrivateRow]:
    """The lint is part of the console, so it may read the private columns (to search for them)."""
    return [
        PrivateRow(r["id"], r["full_name"], r["display_name"], r["ext_id"], r["parent_phone"],
                   r["student_token"], r["parent_token"])
        for r in conn.execute(
            "SELECT id, full_name, display_name, ext_id, parent_phone, student_token, parent_token FROM students"
        )
    ]


def _bounded(term: str) -> re.Pattern:
    return re.compile(r"(?<![0-9A-Za-z])" + re.escape(term) + r"(?![0-9A-Za-z])")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def lint_dir(root: Path, students: list[PrivateRow], page_title: str) -> list[Finding]:
    findings: list[Finding] = []
    ext_res = [(s, _bounded(s.ext_id)) for s in students if s.ext_id and s.ext_id.strip()]
    phones = [(s, _digits(s.parent_phone)) for s in students if len(_digits(s.parent_phone)) >= 7]
    by_token = {}
    for s in students:
        by_token[s.student_token] = s
        by_token[s.parent_token] = s

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if size > MAX_BYTES:
            findings.append(Finding("error", rel, "size", f"{size} bytes > {MAX_BYTES}"))
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        digits_only = _digits(text)

        for m in PHONE_RE.finditer(text):
            findings.append(Finding("error", rel, "phone", m.group(0)))
        for s, digits in phones:
            if digits in digits_only:
                findings.append(Finding("error", rel, "parent_phone", f"student id {s.id}"))
        for s in students:
            if s.full_name and s.full_name.strip() and s.full_name.strip() in text:
                findings.append(Finding("error", rel, "full_name",
                                        f"student id {s.id} (set a shorter display name: daybook roster edit)"))
        for s, rx in ext_res:
            if rx.search(text):
                findings.append(Finding("error", rel, "ext_id", f"student id {s.id}"))

        if path.suffix.lower() in (".html", ".htm"):
            titles = TITLE_RE.findall(text)
            if not titles:
                findings.append(Finding("error", rel, "title", "missing <title>"))
            for t in titles:
                if t.strip() != page_title:
                    findings.append(Finding("error", rel, "title", f"not generic: {t.strip()!r}"))
            for og in OG_RE.findall(text):
                if og.strip() != page_title:
                    findings.append(Finding("error", rel, "og", f"not generic: {og.strip()!r}"))
            for url in RESOURCE_RE.findall(text):
                findings.append(Finding("error", rel, "third_party", url))
            # another student's display name on a per-student page (warning: short names collide with words)
            parts = rel.split("/")
            if len(parts) >= 3 and parts[0] in ("s", "p") and parts[1] in by_token:
                owner = by_token[parts[1]]
                for s in students:
                    if s.id != owner.id and s.display_name and s.display_name != owner.display_name \
                            and len(s.display_name) >= 2 and s.display_name in text:
                        findings.append(Finding("warning", rel, "other_name", f"student id {s.id}"))
        if path.suffix.lower() == ".css":
            for url in CSS_URL_RE.findall(text) + IMPORT_RE.findall(text):
                findings.append(Finding("error", rel, "third_party", url))
    return findings


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == "error"]
