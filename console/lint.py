"""No-PII lint over generated output (BUILD_BRIEF §11/§12).

Errors (publish stops): phone numbers, any student's full_name / ext_id / parent_phone,
a non-generic <title> or og: tag, third-party resource references, files over 100 KB,
thumbnails over 60 KB, brand words (kakao, instagram, #FEE500), an <img> showing a submission photo.
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
# internal round / assignment ids and file names must never reach a student or parent page
RAW_ID_RE = re.compile(r"Adaptive_|round_|_G(?:[1-9]|1[0-2])(?![0-9A-Za-z])|\.json\b")
BRAND_RE = re.compile(r"kakao|instagram|#fee500", re.I)
IMG_SRC_RE = re.compile(r"<img[^>]*\ssrc\s*=\s*[\"']([^\"']*)", re.I)
# a submission photo: the Worker's /photo endpoint, a KV photo key, or an R2 key <day>/<token>.<ext>
PHOTO_SRC_RE = re.compile(r"/photo\b|photo:\d{4}-|\d{4}-\d{2}-\d{2}/[a-z2-7]{26}\.|^blob:|^data:", re.I)
MAX_IMAGE_BYTES = 60 * 1024
TEXT_SUFFIXES = {".html", ".htm", ".css", ".js", ".json", ".webmanifest", ".txt", ".svg", ".xml", ""}
MAX_BYTES = 100 * 1024
VENDOR_PREFIX = "static/katex/"        # live-v2: KaTeX 0.16.11 (MIT), self-hosted; loaded only while a Live room is open
VENDOR_MAX_BYTES = 300 * 1024


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
        cap = VENDOR_MAX_BYTES if rel.startswith(VENDOR_PREFIX) else MAX_BYTES
        if size > cap:
            findings.append(Finding("error", rel, "size", f"{size} bytes > {cap}"))
        if rel.startswith("t/") and size > MAX_IMAGE_BYTES:
            findings.append(Finding("error", rel, "image_size", f"{size} bytes > {MAX_IMAGE_BYTES}"))
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

        for m in BRAND_RE.finditer(text):
            findings.append(Finding("error", rel, "brand", m.group(0)))

        if path.suffix.lower() in (".html", ".htm"):
            for src in IMG_SRC_RE.findall(text):
                if PHOTO_SRC_RE.search(src):
                    findings.append(Finding("error", rel, "photo_img", src[:60]))
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
            if rel.split("/")[0] in ("s", "p"):
                for m in RAW_ID_RE.finditer(text):
                    findings.append(Finding("error", rel, "raw_id", m.group(0)))
            # another student's display name on a per-student page (warning: short names collide with words)
            parts = rel.split("/")
            if len(parts) >= 3 and parts[0] in ("s", "p") and parts[1] in by_token:
                owner = by_token[parts[1]]
                if parts[0] == "s":     # F19.3: korea mode's "열심히 본 친구" names classmates on purpose (student pages only)
                    text = CLASS_NAMES_RE.sub("", text)
                for s in students:
                    if s.id != owner.id and s.display_name and s.display_name != owner.display_name \
                            and len(s.display_name) >= 2 and s.display_name in text:
                        findings.append(Finding("warning", rel, "other_name", f"student id {s.id}"))
        if path.suffix.lower() == ".css":
            for url in CSS_URL_RE.findall(text) + IMPORT_RE.findall(text):
                findings.append(Finding("error", rel, "third_party", url))
    return findings


CLASS_NAMES_RE = re.compile(r'<p class="rk-friends" data-lint="class-names">.*?</p>', re.S)

# H4: words that mean test data leaked into the build students see
TEST_WORD_RE = re.compile(r"(?<![A-Za-z])(e2e|test|tests|fixture|fixtures|dummy)(?![A-Za-z])", re.I)
WORKER_HINT_RE = re.compile(r"workers\.dev|/submit\b|/status\?|/checkin\b|/react\b|X-Teacher-Token", re.I)


def text_scrubber(conn: sqlite3.Connection):
    """For text a student wrote (오늘 한 줄): returns f(text) -> text, or "" when the text would trip the lint
    (a phone number, any full name / ext_id / parent phone, a raw id). The page then simply leaves it out."""
    rows = private_rows(conn)
    names = [r.full_name.strip() for r in rows if r.full_name and r.full_name.strip()]
    ids = [_bounded(r.ext_id) for r in rows if r.ext_id and r.ext_id.strip()]
    phones = [_digits(r.parent_phone) for r in rows if len(_digits(r.parent_phone)) >= 7]

    def scrub(text: str | None) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        d = _digits(t)
        if (PHONE_RE.search(t) or RAW_ID_RE.search(t) or BRAND_RE.search(t) or any(n in t for n in names)
                or any(rx.search(t) for rx in ids) or any(p in d for p in phones)):
            return ""
        return t
    return scrub


def lint_taste(root: Path, students: list[PrivateRow], taste_slug_display: str, worker_urls: list[str]) -> list[Finding]:
    """Taste build (F1): only the demo student's display name, and no Worker URL anywhere."""
    out: list[Finding] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for u in worker_urls:
            if u and u in text:
                out.append(Finding("error", rel, "taste_worker", u))
        if path.suffix.lower() in (".html", ".htm"):
            visible = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
            visible = re.sub(r"<[^>]+>", " ", visible)
            for m in TEST_WORD_RE.finditer(visible):
                out.append(Finding("error", rel, "taste_test_text", m.group(0)))
        if path.suffix.lower() in (".html", ".htm", ".json", ".webmanifest"):
            for m in WORKER_HINT_RE.finditer(text):
                out.append(Finding("error", rel, "taste_worker", m.group(0)))
            for s in students:
                if s.display_name != taste_slug_display and len(s.display_name) >= 2 and s.display_name in text:
                    out.append(Finding("error", rel, "taste_name", f"student id {s.id}"))
    return out


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == "error"]
