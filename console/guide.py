"""G3: `daybook guide`: docs/운영가이드.md as a printable A4 HTML page (one routine per page).

A small Markdown subset (headings, numbered and bulleted lists with one nesting level, paragraphs, **bold**, `code`) is
enough for the guide, so there is no Markdown dependency.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

from .config import ROOT

GUIDE = ROOT / "docs" / "운영가이드.md"

CSS = """
@page { size: A4; margin: 16mm 16mm 18mm; }
body { font: 11pt/1.6 -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif; color: #111; margin: 0; }
h1 { font-size: 20pt; margin: 0 0 4mm; }
h2 { font-size: 15pt; margin: 0 0 4mm; padding-bottom: 2mm; border-bottom: 2px solid #2563eb; }
section { page-break-after: always; break-after: page; }
section:last-child { page-break-after: auto; break-after: auto; }
ol, ul { margin: 2mm 0 3mm; padding-left: 7mm; } li { margin: 1.2mm 0; }
li > ul { margin: 1mm 0; }
code { font: 10pt ui-monospace, Consolas, monospace; background: #f3f4f6; padding: 0 1mm; border-radius: 1mm; }
b { color: #1e3a8a; }
p { margin: 2mm 0; }
.foot { color: #6b7280; font-size: 9pt; margin-top: 6mm; }
@media screen { body { max-width: 190mm; margin: 10mm auto; } section { border-bottom: 1px dashed #ccc; padding-bottom: 8mm; margin-bottom: 8mm; } }
"""


def _inline(t: str) -> str:
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)


def to_html(md: str, title: str = "DAYBOOK 운영 가이드") -> str:
    out, stack, para = [], [], []          # stack: open lists as (tag, indent)

    def close_para():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    def close_lists(to_indent=-1):
        while stack and stack[-1][1] > to_indent:
            tag, _ = stack.pop()
            out.append(f"</li></{tag}>")

    sections = 0
    for raw in md.splitlines():
        line = raw.rstrip()
        m = re.match(r"^(\s*)(\d+\.|-)\s+(.*)$", line)
        if line.startswith("# "):
            close_para(); close_lists()
            out.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("## "):
            close_para(); close_lists()
            if sections:
                out.append("</section>")
            out.append("<section>")
            sections += 1
            out.append(f"<h2>{_inline(line[3:])}</h2>")
        elif m:
            close_para()
            indent, tag = len(m.group(1)), "ol" if m.group(2)[0].isdigit() else "ul"
            if stack and indent > stack[-1][1]:
                out.append(f"<{tag}><li>")
                stack.append((tag, indent))
            else:
                close_lists(indent)
                if stack and stack[-1][1] == indent:
                    out.append("</li><li>")
                else:
                    out.append(f"<{tag}><li>")
                    stack.append((tag, indent))
            out.append(_inline(m.group(3)))
        elif not line.strip():
            close_para()
        elif stack and raw.startswith(" "):
            out.append(" " + _inline(line.strip()))           # a continuation line of a list item
        else:
            close_lists()
            para.append(line.strip())
    close_para(); close_lists()
    if sections:
        out.append("</section>")
    body = "\n".join(out)
    return (f'<!doctype html>\n<html lang="ko"><head><meta charset="utf-8"><meta name="robots" content="noindex">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>\n{body}\n"
            f'<p class="foot">DAYBOOK · 인쇄: 브라우저에서 열고 인쇄 → PDF 또는 프린터 (A4)</p></body></html>\n')


def write(out: str | Path) -> Path:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_html(GUIDE.read_text(encoding="utf-8")), encoding="utf-8", newline="")
    return path
