"""F16.3: the teacher's weekly line, drafted from the data, and a comment lint that only ever warns (never blocks).

korea (default): a warm, natural Korean line in the teacher's warm-formal voice (해요체, polite to parents who read it);
  the lint warns only on privacy: another student's name, a phone number, a raw id, a rank (등수/석차/n등) next to a name.
  Enthusiastic praise ("정말 최고였어요") is normal and good in Korean and is never flagged.
manitoba (opt-in practice): strength / challenge / next step (Korean, or English with "comment_lang": "en", 40-60 words)
  and the full lint: absolutes, comparisons with other students, hedges, jargon (configurable "comment_jargon").
"""
from __future__ import annotations

import re
import sqlite3

from .config import Config
from .lint import PHONE_RE, RAW_ID_RE

RANK_RE = re.compile(r"석차|등수|순위|\d+\s*등(?!급)|\d+\s*위\b|top\s*\d+|ranked", re.I)
ABSOLUTE_RE = re.compile(r"항상|절대|최고|완벽|전혀|\b(?:never|always|best|perfect|worst)\b", re.I)
COMPARE_RE = re.compile(r"반에서|다른 학생|친구들보다|누구보다|남들보다|\bthan (?:others|the others|classmates|the class)\b|compared (?:to|with) (?:others|classmates)", re.I)
HEDGE_RE = re.compile(r"것 같|듯해|듯합|아마|하지만|그런데|\b(?:appears to|seems|seemed|but|however|maybe|perhaps)\b", re.I)
DEFAULT_JARGON = ("확보 효율", "도달 범위", "도달", "persona", "reach ceiling", "efficiency", "commitment")


def names(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    out = []
    for r in conn.execute("SELECT id, display_name, full_name FROM students"):
        for n in {r["display_name"], r["full_name"]}:
            if n and len(n.strip()) >= 2:
                out.append((r["id"], n.strip()))
    return out


def lint(conn: sqlite3.Connection, cfg: Config, text: str, student_id: int | None, mode: str) -> list[dict]:
    """[{kind, match}] warnings for one comment about one student. Never raises, never blocks."""
    t = text or ""
    out = []

    def add(kind, m):
        out.append({"kind": kind, "match": m if isinstance(m, str) else m.group(0)})

    everyone = names(conn)
    for sid, n in everyone:
        if sid != student_id and n in t:
            add("other_student", n)
    for m in PHONE_RE.finditer(t):
        add("phone", m)
    for m in RAW_ID_RE.finditer(t):
        add("raw_id", m)
    rank = RANK_RE.search(t)
    if rank and (mode == "manitoba" or any(n in t for _, n in everyone)):
        add("rank", rank)
    if mode == "manitoba":
        for kind, rx in (("absolute", ABSOLUTE_RE), ("comparison", COMPARE_RE), ("hedge", HEDGE_RE)):
            for m in rx.finditer(t):
                add(kind, m)
        jargon = cfg.extra.get("comment_jargon") or DEFAULT_JARGON
        for j in jargon:
            if j and re.search(re.escape(j), t, re.I):
                add("jargon", j)
    return out


# ---------------------------------------------------------------- drafts

def _facts(w: dict, streak: int, sessions: int) -> dict:
    return {"all": bool(w["c"]) and w["d"] == w["c"], "k": w["k"], "c": w["c"], "missed": max(0, w["c"] - w["d"]),
            "streak": streak, "att": w["week_sum"], "att_max": 3 * sessions,
            "test_up": w["has_tests"] and w["dir_eff"] == "up", "test_down": w["has_tests"] and w["dir_eff"] == "adjust"}


def draft_korea(f: dict) -> str:
    """Warm, natural Korean, 1-3 short sentences, praise first."""
    parts = []
    if f["all"]:
        parts.append(f"이번 주 수업일 {f['c']}일 모두 과제를 냈어요.")
    elif f["k"]:
        parts.append(f"이번 주 {f['k']}일 과제를 올렸어요.")
    if f["streak"] >= 7:
        parts.append(f"{f['streak']}일째 꾸준히 이어 가고 있어서 정말 대단해요.")
    if f["test_up"]:
        parts.append("주간 테스트 흐름도 좋아지고 있어요.")
    if f["att_max"] and f["att"] >= f["att_max"] * 0.8:
        parts.append("수업 태도도 아주 좋았어요.")
    if f["missed"] and not f["all"]:
        parts.append(f"다음 주에는 수업일 과제를 하나도 빠뜨리지 않는 것을 같이 목표로 해 봐요.")
    elif f["test_down"]:
        parts.append("다음 주에는 틀린 문제를 한 번 더 풀어 보면서 감을 되찾아 봐요.")
    if not parts:
        parts.append("이번 주도 수업에 함께해 줘서 고마워요. 다음 주에도 차근차근 해 봐요.")
    return " ".join(parts[:3])


def draft_manitoba(f: dict, lang: str = "ko") -> str:
    """Strength / challenge / next step, from the evidence."""
    if lang == "en":
        strength = (f"submitted the homework on all {f['c']} class days this week" if f["all"] else
                    f"kept a {f['streak']}-day homework streak" if f["streak"] >= 7 else
                    f"handed in homework on {f['k']} day(s) this week" if f["k"] else "came to class and took part")
        if f["missed"]:
            challenge, step = (f"{f['missed']} class-day homework task(s) were not handed in",
                               "upload the homework photo right after each class, before leaving the room")
        elif f["test_down"]:
            challenge, step = ("the weekly test result dipped compared with the previous rounds",
                               "redo the questions marked wrong on the last test and check each answer step by step")
        else:
            challenge, step = ("the next step is to attempt harder problems with less help",
                               "try two challenge problems each week and write down the first step before asking")
        text = f"Strength: this week the student {strength}. Challenge: {challenge}. Next step: {step}."
        # 40-60 words: closing sentences are added only while they fit
        for extra in ("This shows steady effort.", "We will check this together in the next lesson.",
                      "Small daily routines like this build lasting confidence in mathematics."):
            if len(text.split()) + len(extra.split()) <= 60:
                text += " " + extra
            if len(text.split()) >= 40:
                break
        return text
    strength = (f"수업일 {f['c']}일 과제를 모두 냈어요." if f["all"] else
                f"과제를 {f['streak']}일 연속으로 이어 가고 있어요." if f["streak"] >= 7 else
                f"이번 주 {f['k']}일 과제를 올렸어요." if f["k"] else "수업에 꾸준히 참여했어요.")
    if f["missed"]:
        challenge, step = f"수업일 과제 {f['missed']}개가 아직 없어요.", "수업이 끝나면 바로 과제 사진을 올려요."
    elif f["test_down"]:
        challenge, step = "이번 주간 테스트 결과가 지난 회차보다 내려갔어요.", "틀린 문제를 다시 풀고 풀이를 한 줄씩 확인해요."
    else:
        challenge, step = "조금 더 어려운 문제에 혼자 도전할 때예요.", "매주 도전 문제 두 개를 풀고 첫 줄을 먼저 써 봐요."
    return f"강점: {strength} 과제: {challenge} 다음 단계: {step}"


def draft(w: dict, streak: int, sessions: int, mode: str, lang: str = "ko") -> str:
    f = _facts(w, streak, sessions)
    return draft_manitoba(f, lang) if mode == "manitoba" else draft_korea(f)
