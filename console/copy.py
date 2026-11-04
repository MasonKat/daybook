"""F22.3: varied sentence endings for the student-facing copy (and warm-formal ones for parents).

Every variant is hand-written in console/copy_ko.json (key -> list; never conjugated by rule). The pick is deterministic
per (token, date, key): stable within a day (no flicker between opens), different across days and students. Lines that
sit on the same screen are picked in screen order so that two neighbours never share an ending family.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

COPY_FILE = Path(__file__).with_name("copy_ko.json")

# Lines that appear together, in screen order (the no-two-alike rule runs along each list). Every key sits in one screen.
SCREENS: dict[str, list[str]] = {
    "today": ["ck_closed", "ck_none", "wt_todo", "wt_closed", "wt_hard_max", "cw_done", "cw_closed", "replaced", "queued_body",
              "a2hs_title", "live_wait", "live_sent", "live_wrong", "live_closed"],
    "me": ["messages_empty", "lines_empty", "sk_empty", "push_on_done"],
    "videos": ["vr_label"],
    "review": ["rv_empty", "rv_vote_hint", "rv_sent", "rv_one_a_day", "rv_st_approved", "rv_st_hidden", "rv_vote_closed",
               "rv_best_empty", "rv_voted"],
    "tour": ["tour_s1", "tour_s2", "tour_s3", "tour_s4", "tour_s5"],
    "rules": ["gr_rubric", "gr_never", "gr_month"],
    "settings": ["set_order_hint", "set_up_pending", "set_up_approved", "set_up_rejected", "set_up_limit", "set_saved"],
    "parent": ["tour_p1", "tour_p2", "tour_p3", "tour_p4", "tour_p5", "rv_parent_win"],
}
FAMILIES = ("니다", "쥬", "유", "죠", "요")
PARENT_OK = ("요", "니다", "죠")


@lru_cache(maxsize=1)
def variants() -> dict:
    d = json.loads(COPY_FILE.read_text(encoding="utf-8"))
    return {"student": d["student"], "parent": d["parent"]}


def family(text: str) -> str:
    """The ending family of a line's last sentence: 니다 | 쥬 | 유 | 죠 | 요 | '' (a trailing "(…)" note is skipped)."""
    for t in (text, re.sub(r"\s*\([^()]*\)\s*[.!?]*\s*$", "", text)):
        core = re.sub(r"[^가-힣]+$", "", t)
        for f in FAMILIES:
            if core.endswith(f):
                return f
    return ""


def _h(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12], 16)


def picks(token: str, day: str, audience: str = "student") -> dict[str, str]:
    """{key: the chosen line} for one page on one day."""
    pool = variants()[audience]
    out: dict[str, str] = {}
    for screen in SCREENS.values():
        prev = None
        for k in screen:
            if k not in pool:
                continue
            vs = pool[k]
            start = _h(token, day, k) % len(vs)
            pick = vs[start]
            for i in range(len(vs)):
                v = vs[(start + i) % len(vs)]
                if family(v) != prev:
                    pick = v
                    break
            out[k] = pick
            prev = family(pick)
    return out


def overlay(S: dict, token: str, day: str, audience: str = "student") -> dict:
    """The page's strings: the plain ones with this page's varied lines on top."""
    return {**S, **picks(token, day, audience)}
