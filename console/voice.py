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


CLASS_WORDS = {"전체", "반전체", "다같이", "우리반", "모두"}     # the class as the target (반 전체): the class goal, no student
NEGATION_RE = re.compile(r"안\s*(했|늦|떠들|졸|잤|빠졌|하고)|없었|없이|않았|안\s*늦")
NUMW = "0-9일이삼사오육륙칠팔구십한두세네다섯여곱덟아홉열스물하나둘셋넷"
B = r"(?:^|(?<=[\s.,!?~(\-]))"
PTS_ATTACHED = re.compile(B + r"(?:(일단|추가|적립)\s*)?([" + NUMW + r"]+)점(?:\s*(적립|추가))?(?=[\s.,!?~)\-]|$)")
PTS_SPACED = re.compile(B + r"(?:(일단|추가|적립)\s*)?([" + NUMW + r"]+)\s+점(?:\s*(적립|추가))?(?=[\s.,!?~)\-]|$)")


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


def clean_lines(text: str) -> list[str]:
    """Lines of speech: speaker labels and timestamps become line breaks; spaces collapse."""
    t = SPEAKER_RE.sub("\n", text or "")
    t = TIME_RE.sub("\n", t)
    return [" ".join(x.split()) for x in t.splitlines() if x.strip()]


def clean(text: str) -> str:
    return " ".join(clean_lines(text))


def points_in(text: str) -> list[tuple[int, int, int]]:
    """Every points phrase as (start, end, n): 2점, 1점 적립, 3점 추가, 일단 1점, 한 점!; a spaced one ("두 점") only with
    적립 / 추가 / 일단 or at the end of a clause (so "이 점 잘했어" is not 2 points)."""
    out = []
    for rx, spaced in ((PTS_ATTACHED, False), (PTS_SPACED, True)):
        for m in rx.finditer(text):
            n = number(m.group(2))
            if n is None or n > 10:
                continue
            if spaced and not (m.group(1) or m.group(3) or m.end() == len(text) or text[m.end():m.end() + 1] in "!.~"):
                continue
            out.append((m.start(), m.end(), n))
    return sorted(set(out))


def _target_at(toks: list[str], i: int, roster: list[dict]) -> tuple[str, int] | None:
    """("class", width) for 전체 / 다 같이 / 우리 반, ("exact", width) for a call sign said as is (particles read through)."""
    w1 = re.sub(r"[^\w]", "", toks[i])
    w2 = w1 + re.sub(r"[^\w]", "", toks[i + 1]) if i + 1 < len(toks) else ""
    for w, width in ((w2, 2), (w1, 1)):
        if w and any(v in CLASS_WORDS for v in _variants(w)):
            return "class", width
    if roster and w1 and match(w1, roster)[0] == "exact":
        return "exact", 1
    return None


def split(text: str, roster: list[dict] | None = None) -> list[dict]:
    """Utterances in order: {kind, body, dropped, natural}. A keyword starts one (it runs on over the next lines);
    without a keyword, a line that names a student by call sign (or the class) is a 칭찬 of its own ("나이스 가람! 1점
    적립"), and a line with only a points phrase (… N점 추가) is a 칭찬 for 누구?. 취소 drops the utterance before it."""
    out: list[dict] = []
    cur = None
    for line in clean_lines(text):
        marks = list(KW_RE.finditer(line))
        pre = line[: marks[0].start()].strip() if marks else line
        if pre:
            toks = pre.split()
            hit = next(((i, t) for i in range(len(toks)) if (t := _target_at(toks, i, roster or []))), None)
            loose = any(e for e in points_in(pre)) and re.search(r"적립|추가|일단|점!", pre)
            if hit or (roster and loose and (cur is None or points_in(cur["body"]))):
                out.append({"kind": "praise", "body": pre, "dropped": False, "natural": True,
                            "at": hit[0] if hit else None, "width": hit[1][1] if hit else 0})
                cur = None
            elif cur is not None:
                cur["body"] = (cur["body"] + " " + pre).strip()
        for i, m in enumerate(marks):
            body = line[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(line)].strip()
            if m.group(1) == "취소":
                live = [u for u in out if not u["dropped"]]
                if live:
                    live[-1]["dropped"] = True
                cur = None
                continue
            cur = {"kind": {"칭찬": "praise", "개념": "concept", "장면": "scene"}[m.group(1)], "body": body, "dropped": False,
                   "natural": False}
            out.append(cur)
    for u in out:
        u["body"] = u["body"].strip(" .,!?·…:")
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


def _praise(body: str, roster: list[dict], at: int | None = 0, width: int = 0) -> dict:
    """{heard, match, candidates, points, text, target}. The name is the first 1-2 words of a 칭찬 utterance, or the word
    at `at` in a line without a keyword; 전체 / 다 같이 / 우리 반 make it the class (target "class"). Points: the LAST
    points phrase in the line (2점 줄려했는데, 일단 1점 -> 1), default 1."""
    toks = body.split()
    target, heard, kind, cands = "student", "", "unknown", roster[:3]
    if at is None:                                     # a points-only line: nobody named
        rest = toks
    else:
        if at == 0 and width == 0:                     # a 칭찬 keyword utterance: "칠 번" (2 words) before "가람" (1 word)
            ct = _target_at(toks, 0, roster) if toks else None
            if ct and ct[0] == "class":
                width = ct[1]
            else:
                best = None
                for n in (2, 1):
                    if len(toks) >= n:
                        k, c = match(" ".join(toks[:n]), roster)
                        rank = {"exact": 0, "fuzzy": 1, "ambiguous": 2, "unknown": 3}[k]
                        if best is None or rank < best[0]:
                            best = (rank, n, k, c)
                width = best[1] if best else 0
                kind, cands = (best[2], best[3]) if best else ("unknown", roster[:3])
        heard = " ".join(toks[at: at + width])
        if any(v in CLASS_WORDS for v in _variants(re.sub(r"[^\w]", "", heard.replace(" ", "")))):
            target, kind, cands = "class", "exact", []
        elif kind == "unknown" and heard:
            kind, cands = match(re.sub(r"[^\w\s]", "", heard), roster)
        rest = toks[:at] + toks[at + width:]
    text = " ".join(rest)
    pts = points_in(text)
    points = 1
    if pts:
        s0, e0, points = pts[-1]
        text = (text[:s0] + " " + text[e0:])
    text = " ".join(t for t in text.split() if re.search(r"\w", t))     # drop words that are only punctuation (" - ")
    text = re.sub(r"^[,!.\-~\s]+|[,\-\s]+$", "", text).strip(" .,!?·…:-~")
    return {"heard": heard, "match": kind, "candidates": cands, "points": max(0, min(points, 5)), "text": text, "target": target}


def negative_words() -> list[str]:
    out = []
    for p in NEGATIVE:
        if p.exists():
            out += [w.strip() for w in p.read_text(encoding="utf-8").splitlines() if w.strip() and not w.strip().startswith("#")]
    return out


def privacy(text: str, scrub) -> str:
    """"" when the line may reach a page; else why not: negative (확인 필요: the teacher decides) | filtered | pii."""
    if not WF.check(text, WF.default_list())[0]:
        return "filtered"
    if not scrub(text):
        return "pii"
    flat = re.sub(r"\s+", "", text)
    if any(re.sub(r"\s+", "", w) in flat for w in negative_words()):
        return "negative"
    return ""


def lean_public(text: str) -> bool:
    """A negative word that is negated ("지각 안 했네", "안 늦었네", "실수 없었네") leans 공개; the teacher decides."""
    return bool(NEGATION_RE.search(text))


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
    for i, u in enumerate(split(text, roster)):
        row = {"i": i, "kind": u["kind"], "dropped": u["dropped"], "text": u["body"], "target": "student"}
        if u["kind"] == "praise":
            p = _praise(u["body"], roster, u.get("at", 0), u.get("width", 0)) if u["natural"] else _praise(u["body"], roster)
            sure = p["match"] in ("exact", "fuzzy")
            row.update(heard=p["heard"], match=p["match"], points=p["points"], text=p["text"], target=p["target"],
                       student_id=p["candidates"][0]["id"] if sure and p["target"] == "student" else None,
                       candidates=[{"id": c["id"], "display_name": c["display_name"], "callsign": c["callsign"]} for c in p["candidates"]])
        row["private"] = privacy(row["text"], scrub) if row["text"] else ""
        row["publish"] = row["private"] == "" or (row["private"] == "negative" and lean_public(row["text"]))
        whom = row["kind"] != "praise" or row["target"] == "class" or row.get("student_id") is not None
        row["keep"] = not u["dropped"] and (bool(row["text"]) or row["kind"] == "praise") and whom
        rows.append(row)
    who = [r for r in rows if r["kind"] == "praise" and r["target"] == "student" and r["student_id"] is None and not r["dropped"]]
    return {"rows": rows, "who": len(who), "callsigns": len(roster)}


def _visibility(text: str, scrub, publish: bool | None) -> tuple[int, int, str]:
    """(private, hold, why). filtered / pii: teacher-only, always. negative: the teacher's 공개 / 비공개 when given (the
    preview), else held as 확인 필요 (the remote)."""
    why = privacy(text, scrub)
    if why in ("filtered", "pii"):
        return 1, 0, why
    if why == "negative":
        return (0, 0, why) if publish is True else (1, 0, why) if publish is False else (1, 1, why)
    return 0, 0, ""


def _class_shout(conn, class_id: int, day: str, ts: str, pts: int, text: str, private: int, source: str, ref: str | None) -> int:
    cur = conn.execute("""INSERT INTO class_shouts (class_id, day, ts, points, text, private, source, ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (class_id, day, ts, pts, text, private, source, ref))
    if pts > 0:                                     # 반 전체: the class goal (반 목표 bar), never one student's points
        conn.execute("INSERT OR IGNORE INTO class_bonus (class_id, session_id, points, source, ref, ts) VALUES (?, NULL, ?, 'shout', ?, ?)",
                     (class_id, pts, ref or f"class_shout:{cur.lastrowid}", ts))
    log_event(conn, "class_shout", {"id": cur.lastrowid, "class_id": class_id, "points": pts, "source": source})
    return cur.lastrowid


def save(conn: sqlite3.Connection, cfg: Config, class_id: int, day: date, rows: list[dict]) -> dict:
    """Stores the rows the teacher kept (keep = true). Every row is re-checked here; the browser is not trusted."""
    from .lint import text_scrubber
    scrub = text_scrubber(conn)
    members = {r[0] for r in conn.execute("SELECT id FROM students WHERE class_id = ? AND active = 1", (class_id,))}
    ts = now_utc_iso()
    n = {"praise": 0, "concept": 0, "scene": 0, "private": 0, "skipped": 0, "class": 0}
    to_attitude = bool(cfg.extra.get("voice_points_to_attitude", False))
    for r in rows:
        text = " ".join(str(r.get("text") or "").split())[:200]
        if not r.get("keep") or r.get("kind") not in ("praise", "concept", "scene") or (not text and r.get("kind") != "praise"):
            n["skipped"] += 1
            continue
        if r["kind"] == "concept":
            conn.execute("INSERT INTO concept_notes (class_id, day, text, ts) VALUES (?, ?, ?, ?)", (class_id, day.isoformat(), text, ts))
            log_event(conn, "concept_note", {"class_id": class_id, "day": day.isoformat()})
        elif r["kind"] == "scene":
            conn.execute("INSERT INTO moment_drafts (class_id, day, text, ts) VALUES (?, ?, ?, ?)", (class_id, day.isoformat(), text, ts))
            log_event(conn, "moment_draft", {"class_id": class_id, "day": day.isoformat()})
        else:
            pts = max(0, min(int(r.get("points") or 0), 5))
            private, _, why = _visibility(text, scrub, bool(r.get("publish")))
            if r.get("target") == "class":
                _class_shout(conn, class_id, day.isoformat(), ts, pts, text, private, "voice-import", None)
                n["class"] += 1
            else:
                sid = r.get("student_id")
                if sid not in members:
                    n["skipped"] += 1
                    continue
                cur = conn.execute("""INSERT INTO shoutouts (student_id, class_id, day, ts, points, text, private, source)
                                      VALUES (?, ?, ?, ?, ?, ?, ?, 'voice-import')""", (sid, class_id, day.isoformat(), ts, pts, text, private))
                log_event(conn, "shoutout", {"id": cur.lastrowid, "student_id": sid, "points": pts, "private": why or None})
                if to_attitude and pts:
                    _add_attitude(conn, class_id, sid, day, pts)
            n["private"] += private
        n[r["kind"]] += 1
    log_event(conn, "voice_import", {"class_id": class_id, "day": day.isoformat(), **n})
    conn.commit()
    return n


def apply_remote(conn: sqlite3.Connection, cfg: Config, items: list[dict]) -> int:
    """F19.4: shout-outs from the teacher remote (pulled), by token or "*" (the class) and class key; an undo deletes the
    row. A negative word holds the line as 확인 필요 (teacher-only until 공개); the F18 filter or the PII lint keeps it
    teacher-only."""
    from datetime import date as _date
    from .lint import text_scrubber
    from .ops import class_key
    from .pull import _token_map
    scrub = text_scrubber(conn)
    tokens = _token_map(conn)
    keys = {class_key(r[0]): r[0] for r in conn.execute("SELECT id FROM classes")}
    to_attitude = bool(cfg.extra.get("voice_points_to_attitude", False))
    n = 0
    for it in items:
        ref = "remote:" + str(it.get("id", ""))
        if it.get("deleted"):
            for t in ("shoutouts", "class_shouts"):
                if conn.execute(f"DELETE FROM {t} WHERE ref = ?", (ref,)).rowcount:
                    log_event(conn, "shoutout_undo", {"ref": ref})
            conn.execute("DELETE FROM class_bonus WHERE source = 'shout' AND ref = ?", (ref,))
            continue
        if conn.execute("SELECT 1 FROM shoutouts WHERE ref = ? UNION SELECT 1 FROM class_shouts WHERE ref = ?", (ref, ref)).fetchone():
            continue
        text = " ".join(str(it.get("text") or "").split())[:200]
        pts = max(0, min(int(it.get("points") or 0), 3))
        day = str(it.get("day") or "")
        private, hold, why = _visibility(text, scrub, None) if text else (0, 0, "")
        if it.get("token") == "*":
            cid = keys.get(str(it.get("class", "")))
            if cid is None:
                continue
            _class_shout(conn, cid, day, it.get("ts", ""), pts, text, private, "remote", ref)
        else:
            sid = tokens.get(str(it.get("token", "")))
            row = conn.execute("SELECT class_id FROM students WHERE id = ?", (sid,)).fetchone() if sid is not None else None
            if not row:
                continue
            cur = conn.execute("""INSERT INTO shoutouts (student_id, class_id, day, ts, points, text, private, source, ref, hold)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, 'remote', ?, ?)""", (sid, row[0], day, it.get("ts", ""), pts, text, private, ref, hold))
            log_event(conn, "shoutout", {"id": cur.lastrowid, "student_id": sid, "points": pts, "private": why or None, "source": "remote"})
            if to_attitude and pts:
                _add_attitude(conn, row[0], sid, _date.fromisoformat(day), pts)
        n += 1
    return n


def decide(conn: sqlite3.Connection, shout_id: int, show: bool) -> None:
    """확인 필요 -> 공개 / 비공개 (the F18 filter and the PII lint still apply at publish)."""
    if not conn.execute("SELECT 1 FROM shoutouts WHERE id = ?", (shout_id,)).fetchone():
        raise VoiceError("no such shout-out")
    conn.execute("UPDATE shoutouts SET private = ?, hold = 0 WHERE id = ?", (0 if show else 1, shout_id))
    log_event(conn, "shoutout_" + ("show" if show else "hide"), {"id": shout_id})
    conn.commit()


def _add_attitude(conn, class_id, sid, day, pts) -> None:
    from . import ops
    sess = ops.ensure_session(conn, class_id, day)
    sess_id = sess["id"] if hasattr(sess, "keys") else sess
    cur = conn.execute("SELECT points FROM attitude WHERE student_id = ? AND session_id = ?", (sid, sess_id)).fetchone()
    ops.set_attitude(conn, sid, sess_id, min(3, (cur[0] if cur else 0) + pts))


def shoutouts(conn: sqlite3.Connection, student_id: int, public_only: bool = True, limit: int = 10) -> list[dict]:
    q = "SELECT day, points, text FROM shoutouts WHERE student_id = ? AND text != ''" + (" AND private = 0" if public_only else "")
    return [dict(r) for r in conn.execute(q + " ORDER BY day DESC, id DESC LIMIT ?", (student_id, limit))]


def mic_total(conn: sqlite3.Connection, student_id: int) -> int:
    return conn.execute("SELECT COALESCE(SUM(points), 0) FROM shoutouts WHERE student_id = ?", (student_id,)).fetchone()[0]


def today_board(conn: sqlite3.Connection, class_id: int, day: date, class_label: str = "반 전체") -> list[dict]:
    """The board's 오늘의 칭찬 (korea mode): display name (or 반 전체) + the line; teacher-only lines never."""
    rows = [dict(r) for r in conn.execute(
        """SELECT p.display_name, s.points, s.text, s.ts FROM shoutouts s JOIN students_public p ON p.id = s.student_id
           WHERE s.class_id = ? AND s.day = ? AND s.private = 0""", (class_id, day.isoformat()))]
    rows += [{"display_name": class_label, "points": r["points"], "text": r["text"], "ts": r["ts"]} for r in conn.execute(
        "SELECT points, text, ts FROM class_shouts WHERE class_id = ? AND day = ? AND private = 0", (class_id, day.isoformat()))]
    return sorted(rows, key=lambda r: r["ts"])


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
