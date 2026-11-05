"""F22.5 ⚙ 설정 → 테마: preset palettes only (no custom CSS, no free colours), 밝게 / 어둡게 / 자동, 글자 크기, a background
slot, and unlocks that reward attendance only (연속 days, the class's 반 목표 first step) — never scores.

Every palette defines the accent (buttons, links), the ring gradient and the card tint, for light AND dark, and must pass
WCAG AA (4.5:1) for text on cards, text on the page, muted text on cards and button text on the accent
(tests/test_themes.py computes the ratios). The parent page keeps the academy's default look. Names avoid words that are
also given names (하늘, 바다, 노을…): a page carrying one would trip the publish lint's "another student's name" rule.
"""
from __future__ import annotations

import re
import sqlite3

# id -> name, gradient (3 stops), and per mode: accent, accent-fg (button text), card tint
THEMES: dict[str, dict] = {
    "amp": {"name": "보라 AMP", "grad": ("#7c3aed", "#ec4899", "#f59e0b"),
            "light": {"accent": "#6d28d9", "accent_fg": "#ffffff", "card": "#f5f3ff"},
            "dark": {"accent": "#7c3aed", "accent_fg": "#ffffff", "card": "#1c1730"}},
    "sea": {"name": "바다빛", "grad": ("#0ea5e9", "#2563eb", "#14b8a6"),
            "light": {"accent": "#0369a1", "accent_fg": "#ffffff", "card": "#f0f9ff"},
            "dark": {"accent": "#0369a1", "accent_fg": "#ffffff", "card": "#0f1d2b"}},
    "forest": {"name": "숲속", "grad": ("#22c55e", "#15803d", "#84cc16"),
               "light": {"accent": "#15803d", "accent_fg": "#ffffff", "card": "#f0fdf4"},
               "dark": {"accent": "#15803d", "accent_fg": "#ffffff", "card": "#10201a"}},
    "sunset": {"name": "노을빛", "grad": ("#f97316", "#ec4899", "#facc15"),
               "light": {"accent": "#c2410c", "accent_fg": "#ffffff", "card": "#fff7ed"},
               "dark": {"accent": "#c2410c", "accent_fg": "#ffffff", "card": "#26170f"}},
    "mono": {"name": "모노", "grad": ("#6b7280", "#111827", "#9ca3af"),
             "light": {"accent": "#374151", "accent_fg": "#ffffff", "card": "#f3f4f6"},
             "dark": {"accent": "#e5e7eb", "accent_fg": "#111827", "card": "#1a1d23"}},
    "blossom": {"name": "벚꽃잎", "grad": ("#f9a8d4", "#ec4899", "#fbcfe8"),
                "light": {"accent": "#be185d", "accent_fg": "#ffffff", "card": "#fdf2f8"},
                "dark": {"accent": "#be185d", "accent_fg": "#ffffff", "card": "#2a1420"}},
    "night": {"name": "별밤", "grad": ("#1e3a8a", "#6366f1", "#a78bfa"),
              "light": {"accent": "#4338ca", "accent_fg": "#ffffff", "card": "#eef2ff"},
              "dark": {"accent": "#4f46e5", "accent_fg": "#ffffff", "card": "#141633"}},
}
# the student page's base surfaces (static/app.css body[data-page="student"]); a theme tints the card only
BASE = {"light": {"bg": "#ffffff", "fg": "#111827", "muted": "#5b6472"},
        "dark": {"bg": "#0b0f17", "fg": "#f2f4f8", "muted": "#9aa3b2"}}
BASE_FULL = {"light": "--bg: #ffffff; --fg: #111827; --muted: #5b6472; --card: #f3f4f6; --line: #e5e7eb; --tile-off: #f6f7f9; --tile-sub-fg: #1e3a8a; --up: #15803d;",
             "dark": "--bg: #0b0f17; --fg: #f2f4f8; --muted: #9aa3b2; --card: #161c27; --line: #263041; --tile-off: #121823; --tile-sub-fg: #dbe4ff; --up: #4ade80;"}
# the scrim over a background picture (behind the header and the story rings), per mode
SCRIM = {"light": ("#ffffff", 0.84), "dark": ("#0b0f17", 0.80)}
MODES = ("auto", "light", "dark")
# light animated effects (static/app.js draws them on one canvas behind the content); 크리스마스 is December only
FX = ("none", "snow", "xmas", "petals", "leaves", "stars")
SIZES = ("md", "lg")
# attendance-only unlocks (DECISIONS F22.5): streak days, or the class reaching its first 반 목표 step
DEFAULT_LOCKS = {"sunset": {"streak": 7}, "night": {"streak": 21}, "blossom": {"goal": 1}}
BG_RE = re.compile(r"^[a-z0-9_-]{1,24}$")
BG_MAX = 120 * 1024


# ---------------------------------------------------------------- WCAG contrast
def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def rgb(hexc: str) -> tuple[int, int, int]:
    h = hexc.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def luminance(c) -> float:
    r, g, b = rgb(c) if isinstance(c, str) else c
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def blend(top: str, alpha: float, under: tuple[int, int, int]) -> tuple[int, int, int]:
    t = rgb(top)
    return tuple(round(alpha * t[i] + (1 - alpha) * under[i]) for i in range(3))


def pairs(theme_id: str, mode: str) -> dict[str, tuple]:
    """The text/background pairs a palette must keep >= 4.5:1."""
    t, b = THEMES[theme_id][mode], BASE[mode]
    return {"text on card": (b["fg"], t["card"]), "muted on card": (b["muted"], t["card"]), "text on page": (b["fg"], b["bg"]),
            "button": (t["accent_fg"], t["accent"])}


def scrim_worst(mode: str) -> float:
    """Text over the scrimmed background picture, for the worst possible pixel (black or white) under it."""
    col, a = SCRIM[mode]
    return min(contrast(BASE[mode]["fg"], blend(col, a, under)) for under in ((0, 0, 0), (255, 255, 255)))


# ---------------------------------------------------------------- CSS (published as /static/themes.css; the student page only)
def css() -> str:
    out = ["/* F22.5 student themes (generated by console/themes.py: presets only). The parent page never links this file. */"]
    sel = 'body[data-page="student"]'
    out.append(f'{sel}[data-mode="light"] {{ {BASE_FULL["light"]} }}')
    out.append(f'{sel}[data-mode="dark"] {{ {BASE_FULL["dark"]} }}')
    for tid, t in THEMES.items():
        g = t["grad"]
        grad = f"linear-gradient(135deg, {g[0]} 0%, {g[1]} 55%, {g[2]} 100%)"

        soft = "linear-gradient(135deg, {} 0%, {} 55%, {} 100%)".format(*("rgba({}, {}, {}, .16)".format(*rgb(c)) for c in g))

        def block(m):
            v = t[m]
            return (f'--accent: {v["accent"]}; --accent-fg: {v["accent_fg"]}; --card: {v["card"]}; --grad: {grad}; '
                    f'--grad-soft: {soft};')
        # the student page is dark unless the phone (or the student) asks for light
        out.append(f'{sel}[data-theme="{tid}"] {{ {block("dark")} }}')
        out.append(f'@media (prefers-color-scheme: light) {{ {sel}[data-theme="{tid}"]:not([data-mode="dark"]) {{ {block("light")} }} }}')
        out.append(f'{sel}[data-theme="{tid}"][data-mode="light"] {{ {block("light")} }}')
        out.append(f'{sel}[data-theme="{tid}"][data-mode="dark"] {{ {block("dark")} }}')
    lc, la = SCRIM["light"]
    dc, da = SCRIM["dark"]
    r = lambda c, a: "rgba({}, {}, {}, {})".format(*rgb(c), a)   # noqa: E731
    out.append(f'{sel} {{ --scrim: {r(dc, da)}; }}')
    out.append(f'@media (prefers-color-scheme: light) {{ {sel}:not([data-mode="dark"]) {{ --scrim: {r(lc, la)}; }} }}')
    out.append(f'{sel}[data-mode="light"] {{ --scrim: {r(lc, la)}; }}')
    out.append(f'{sel}[data-mode="dark"] {{ --scrim: {r(dc, da)}; }}')
    out.append(f'{sel}[data-bg] .app {{ background-image: linear-gradient(var(--scrim), var(--scrim)), var(--bgimg); '
               'background-repeat: no-repeat; background-size: 100% 196px, cover; background-position: top center; }')
    out.append(f'{sel}[data-size="lg"] .s-main {{ zoom: 1.12; }}')   # the scrolling content only: the header keeps its fit
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- unlocks and the effective look
def locks(extra: dict | None = None) -> dict:
    """theme/background id -> {"streak": n} | {"goal": 1}; the brand config may add locks to backgrounds."""
    out = dict(DEFAULT_LOCKS)
    for b in backgrounds(extra or {}):
        if b.get("streak"):
            out["bg:" + b["id"]] = {"streak": int(b["streak"])}
    return out


def earned(streak: int, goal_steps: int, lk: dict) -> set[str]:
    """What attendance has opened (streak = 연속 days, goal_steps = the class's 반 목표 steps reached). No score enters."""
    return {k for k, r in lk.items() if ("streak" in r and streak >= r["streak"]) or ("goal" in r and goal_steps >= r["goal"])}


def remember(conn: sqlite3.Connection, student_id: int, items: set[str], ts: str) -> set[str]:
    """Once open, always open (a broken streak never takes a theme away). Returns everything unlocked so far."""
    for i in items:
        conn.execute("INSERT OR IGNORE INTO unlocks (student_id, item, ts) VALUES (?, ?, ?)", (student_id, i, ts))
    return {r[0] for r in conn.execute("SELECT item FROM unlocks WHERE student_id = ?", (student_id,))}


def backgrounds(extra: dict) -> list[dict]:
    """brand.backgrounds: [{"id", "file", "label"?, "streak"?}] (local files in brand/backgrounds/, <= 120 KB)."""
    out = []
    for b in ((extra.get("brand") or {}).get("backgrounds") or []):
        if isinstance(b, dict) and BG_RE.match(str(b.get("id", ""))) and b.get("file"):
            out.append(b)
    return out


def effective(pf: dict, unlocked: set[str], bg_ids: set[str], lk: dict, taste: bool = False, month: int = 0) -> dict:
    """The look a page renders: a locked or unknown choice falls back to the default (the Worker cannot know locks)."""
    ok = lambda k: taste or k not in lk or k in unlocked   # noqa: E731
    theme = pf.get("theme", "")
    bg = pf.get("bg", "")
    return {"theme": theme if theme in THEMES and ok(theme) else "",
            "mode": pf.get("mode") if pf.get("mode") in MODES else "auto",
            "size": pf.get("size") if pf.get("size") in SIZES else "md",
            "bg": bg if bg in bg_ids and ok("bg:" + bg) else "",
            "fx": fx_of(pf.get("fx"), month)}


def fx_of(v, month: int) -> str:
    """An unknown effect is 없음; 크리스마스 only in December (month 0 = unknown: allowed, the page checks again)."""
    if v not in FX or (v == "xmas" and month not in (0, 12)):
        return "none"
    return v
