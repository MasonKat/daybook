"""F19.1: the teacher's voice notes, imported from a Korean STT transcript (Naver CLOVA Note → 텍스트 내보내기).

Input is plain text in CLOVA's export shape: optional speaker labels ("참석자 1"), timestamps (00:12:31) and line breaks
anywhere; all of that is stripped. The text splits into utterances at the spoken keywords 칭찬 / 개념 / 장면 / 취소 (a
keyword is a word of its own: 개념을 / 장면이 do not split), each running until the next keyword:

    칭찬 <call sign> [n점] <text>   a shoutout for one student (the call sign is the roster's `callsign`)
    개념 <text>                     a teacher-only "오늘의 개념 메모"
    장면 <text>                     a 오늘의 장면 draft (text only) for the teacher to approve
    취소                            drops the previous utterance

Call signs are matched fuzzily against the class's own: Korean jamo edit distance ≤ 1 (numbers must match exactly) and
spoken numbers (칠번 = 7번, 한 점 / 일 점 = 1점). An ambiguous or unmatched 칭찬 goes to "누구?" with 2–3 candidates and is
never guessed. `preview()` stores nothing; `save()` stores only what the teacher kept. Every saved line is checked: a
negative word (filters/shoutout_negative.txt), the F18 word filter or the PII lint makes it teacher-only.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

from . import wordfilter as WF
from .config import ROOT, Config
from .db import log_event, now_utc_iso

KEYWORDS = ("칭찬", "개념", "장면", "취소")
SPEAKER_RE = re.compile(r"(?:참석자|화자|발언자|speaker)\s*\d+\s*:?", re.I)
TIME_RE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")
KW_RE = re.compile(r"(?:^|(?<=[\s.,!?·…\"'“”]))(칭찬|개념|장면|취소)(?=[\s.,!?·…:\"'“”]|$)")
SUFFIXES = ("이한테", "한테", "에게", "이랑", "랑", "이가", "이는", "이도", "이를", "이", "가", "는", "도", "를", "아", "야", "님")
NEGATIVE = (ROOT / "filters" / "shoutout_negative.txt", ROOT / "filters" / "shoutout_negative.local.txt")

SINO = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "륙": 6, "칠": 7, "팔": 8, "구": 9}
NATIVE = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "석": 3, "네": 4, "넷": 4, "다섯": 5, "여섯": 6, "일곱": 7,
          "여덟": 8, "아홉": 9}


class VoiceError(ValueError):
    pass


def number(word: str) -> int | None:
    """"7", "칠", "십이", "한", "열두", "다섯" -> int; anything else -> None."""
    w = word.strip()
    if w.isdigit():
        return int(w)
    m = re.fullmatch(r"([일이삼사오육륙칠팔구])?(십)?([일이삼사오육륙칠팔구])?", w)
    if m and w:
        if m.group(2):
            return (SINO[m.group(1)] if m.group(1) else 1) * 10 + (SINO[m.group(3)] if m.group(3) else 0)
        if m.group(1) and not m.group(3):
            return SINO[m.group(1)]
    m = re.fullmatch(r"(열|스물)?(한|하나|두|둘|세|셋|석|네|넷|다섯|여섯|일곱|여덟|아홉)?", w)
    if m and w:
        return {"열": 10, "스물": 20, None: 0}[m.group(1)] + (NATIVE[m.group(2)] if m.group(2) else 0)
    return None


def spoken(s: str) -> str:
    """The comparable form of a call sign or a heard word: no spaces, number words before 번 as digits (칠 번 -> 7번)."""
    t = re.sub(r"\s+", "", s or "")
    return re.sub(r"([일이삼사오육륙칠팔구십한두세네다섯여곱덟아홉열스물하나둘셋넷]+)번",
                  lambda m: f"{number(m.group(1))}번" if number(m.group(1)) is not None else m.group(0), t)


def _units(s: str) -> list[str]:
    out = []
    for ch in s:
        c = ord(ch) - WF.SBASE
        if 0 <= c < 11172:
            for p in (WF.L[c // 588], WF.V[(c % 588) // 28], WF.T[c % 28]):
                if p:
                    out.append(p)
        else:
            out.append(ch)
    return out


def distance(a: str, b: str) -> int:
    """Jamo-level edit distance; digits must agree exactly (7번 is never 1번)."""
    if re.sub(r"\D", "", a) != re.sub(r"\D", "", b):
        return 99
    x, y = _units(a), _units(b)
    prev = list(range(len(y) + 1))
    for i, cx in enumerate(x, 1):
        cur = [i]
        for j, cy in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (cx != cy)))
        prev = cur
    return prev[-1]


def clean(text: str) -> str:
    t = SPEAKER_RE.sub(" ", text or "")
    t = TIME_RE.sub(" ", t)
    return " ".join(t.split())


def split(text: str) -> list[dict]:
    """Utterances in order: {kind, body, dropped}. 취소 marks the previous one dropped (kept for the preview)."""
    t = clean(text)
    marks = list(KW_RE.finditer(t))
    out: list[dict] = []
    for i, m in enumerate(marks):
        body = t[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(t)].strip(" .,!?·…:")
        kw = m.group(1)
        if kw == "취소":
            live = [u for u in out if not u["dropped"]]
            if live:
                live[-1]["dropped"] = True
            continue
        out.append({"kind": {"칭찬": "praise", "개념": "concept", "장면": "scene"}[kw], "body": body, "dropped": False})
    return out


def _variants(word: str) -> list[str]:
    w = spoken(word)
    out = [w]
    for suf in SUFFIXES:
        if w.endswith(suf) and len(w) > len(suf):
            out.append(w[: -len(suf)])
    return out


def match(heard: str, roster: list[dict]) -> tuple[str, list[dict]]:
    """("exact" | "fuzzy" | "ambiguous" | "unknown", candidates). One candidate for exact/fuzzy."""
    scored = []
    for r in roster:
        d = min(distance(v, spoken(r["callsign"])) for v in _variants(heard))
        scored.append((d, r))
    scored.sort(key=lambda x: (x[0], x[1]["callsign"]))
    if not scored:
        return "unknown", []
    best = scored[0][0]
    tied = [r for d, r in scored if d == best]
    if best == 0 and len(tied) == 1:
        return "exact", tied
    if best == 1 and len(tied) == 1:
        return "fuzzy", tied
    if best <= 1:
        return "ambiguous", tied[:3]
    return "unknown", [r for d, r in scored if d < 99][:3] or [r for _, r in scored][:3]


def _praise(body: str, roster: list[dict]) -> dict:
    toks = body.split()
    best = None
    for n in (2, 1):                                   # "칠 번" (2 words) before "가람" (1 word)
        if len(toks) >= n:
            heard = " ".join(toks[:n])
            kind, cands = match(heard, roster)
            rank = {"exact": 0, "fuzzy": 1, "ambiguous": 2, "unknown": 3}[kind]
            if best is None or rank < best[0]:
                best = (rank, n, heard, kind, cands)
    if best is None:
        return {"heard": "", "match": "unknown", "candidates": roster[:3], "points": 1, "text": ""}
    _, n, heard, kind, cands = best
    rest = toks[n:]
    points = 1
    if rest:
        m = re.fullmatch(r"(\S+?)점[.,!]?", rest[0])
        if m and number(m.group(1)) is not None:
            points, rest = number(m.group(1)), rest[1:]
        elif len(rest) >= 2 and number(rest[0]) is not None and re.fullmatch(r"점[.,!]?", rest[1]):
            points, rest = number(rest[0]), rest[2:]
    return {"heard": heard, "match": kind, "candidates": cands, "points": max(0, min(points or 0, 5)),
            "text": " ".join(rest).strip(" .,!?·…:")}


def negative_words() -> list[str]:
    out = []
    for p in NEGATIVE:
        if p.exists():
            out += [w.strip() for w in p.read_text(encoding="utf-8").splitlines() if w.strip() and not w.strip().startswith("#")]
    return out


def privacy(text: str, scrub) -> str:
    """"" when the line may reach a page; else why it stays teacher-only: negative | filtered | pii."""
    flat = re.sub(r"\s+", "", text)
    if any(re.sub(r"\s+", "", w) in flat for w in negative_words()):
        return "negative"
    if not WF.check(text, WF.default_list())[0]:
        return "filtered"
    if not scrub(text):
        return "pii"
    return ""


def class_roster(conn: sqlite3.Connection, class_id: int) -> list[dict]:
    return [{"id": r["id"], "display_name": r["display_name"], "callsign": r["callsign"]} for r in conn.execute(
        "SELECT id, display_name, callsign FROM students WHERE class_id = ? AND active = 1 AND callsign IS NOT NULL ORDER BY id",
        (class_id,))]


def preview(conn: sqlite3.Connection, class_id: int, text: str) -> dict:
    """Rows for the preview table; nothing is stored."""
    from .lint import text_scrubber
    scrub = text_scrubber(conn)
    roster = class_roster(conn, class_id)
    rows = []
    for i, u in enumerate(split(text)):
        row = {"i": i, "kind": u["kind"], "dropped": u["dropped"], "text": u["body"]}
        if u["kind"] == "praise":
            p = _praise(u["body"], roster)
            sure = p["match"] in ("exact", "fuzzy")
            row.update(heard=p["heard"], match=p["match"], points=p["points"], text=p["text"],
                       student_id=p["candidates"][0]["id"] if sure else None,
                       candidates=[{"id": c["id"], "display_name": c["display_name"], "callsign": c["callsign"]} for c in p["candidates"]])
        row["private"] = privacy(row["text"], scrub) if row["text"] else ""
        row["keep"] = not u["dropped"] and bool(row["text"]) and (u["kind"] != "praise" or row.get("student_id") is not None)
        rows.append(row)
    who = [r for r in rows if r["kind"] == "praise" and r["student_id"] is None and not r["dropped"]]
    return {"rows": rows, "who": len(who), "callsigns": len(roster)}


def save(conn: sqlite3.Connection, cfg: Config, class_id: int, day: date, rows: list[dict]) -> dict:
    """Stores the rows the teacher kept (keep = true). Every row is re-checked here; the browser is not trusted."""
    from .lint import text_scrubber
    scrub = text_scrubber(conn)
    members = {r[0] for r in conn.execute("SELECT id FROM students WHERE class_id = ? AND active = 1", (class_id,))}
    ts = now_utc_iso()
    n = {"praise": 0, "concept": 0, "scene": 0, "private": 0, "skipped": 0}
    to_attitude = bool(cfg.extra.get("voice_points_to_attitude", False))
    for r in rows:
        text = " ".join(str(r.get("text") or "").split())[:200]
        if not r.get("keep") or not text or r.get("kind") not in ("praise", "concept", "scene"):
            n["skipped"] += 1
            continue
        if r["kind"] == "concept":
            conn.execute("INSERT INTO concept_notes (class_id, day, text, ts) VALUES (?, ?, ?, ?)", (class_id, day.isoformat(), text, ts))
            log_event(conn, "concept_note", {"class_id": class_id, "day": day.isoformat()})
        elif r["kind"] == "scene":
            conn.execute("INSERT INTO moment_drafts (class_id, day, text, ts) VALUES (?, ?, ?, ?)", (class_id, day.isoformat(), text, ts))
            log_event(conn, "moment_draft", {"class_id": class_id, "day": day.isoformat()})
        else:
            sid = r.get("student_id")
            if sid not in members:
                n["skipped"] += 1
                continue
            pts = max(0, min(int(r.get("points") or 0), 5))
            why = privacy(text, scrub)
            cur = conn.execute("""INSERT INTO shoutouts (student_id, class_id, day, ts, points, text, private, source)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, 'voice-import')""", (sid, class_id, day.isoformat(), ts, pts, text, 1 if why else 0))
            log_event(conn, "shoutout", {"id": cur.lastrowid, "student_id": sid, "points": pts, "private": why or None})
            n["private"] += 1 if why else 0
            if to_attitude and pts:
                _add_attitude(conn, class_id, sid, day, pts)
        n[r["kind"]] += 1
    log_event(conn, "voice_import", {"class_id": class_id, "day": day.isoformat(), **n})
    conn.commit()
    return n


def _add_attitude(conn, class_id, sid, day, pts) -> None:
    from . import ops
    sess = ops.ensure_session(conn, class_id, day)
    sess_id = sess["id"] if hasattr(sess, "keys") else sess
    cur = conn.execute("SELECT points FROM attitude WHERE student_id = ? AND session_id = ?", (sid, sess_id)).fetchone()
    ops.set_attitude(conn, sid, sess_id, min(3, (cur[0] if cur else 0) + pts))


def shoutouts(conn: sqlite3.Connection, student_id: int, public_only: bool = True, limit: int = 10) -> list[dict]:
    q = "SELECT day, points, text FROM shoutouts WHERE student_id = ?" + (" AND private = 0" if public_only else "")
    return [dict(r) for r in conn.execute(q + " ORDER BY day DESC, id DESC LIMIT ?", (student_id, limit))]


def mic_total(conn: sqlite3.Connection, student_id: int) -> int:
    return conn.execute("SELECT COALESCE(SUM(points), 0) FROM shoutouts WHERE student_id = ?", (student_id,)).fetchone()[0]


def today_board(conn: sqlite3.Connection, class_id: int, day: date) -> list[dict]:
    """The board's 오늘의 칭찬 (korea mode): display name + the line; teacher-only lines never."""
    return [dict(r) for r in conn.execute(
        """SELECT p.display_name, s.points, s.text FROM shoutouts s JOIN students_public p ON p.id = s.student_id
           WHERE s.class_id = ? AND s.day = ? AND s.private = 0 ORDER BY s.id""", (class_id, day.isoformat()))]


def read_input(path: str) -> str:
    import sys
    if path == "-":
        data = sys.stdin.buffer.read()
    else:
        data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949"):              # CLOVA exports UTF-8; a Windows editor may save CP949
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise VoiceError("the file is neither UTF-8 nor CP949 text")
