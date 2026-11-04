"""F19.1: the F18 review filter in Python, for text the console sends to pages (the teacher's voice-note 칭찬 lines).

A line-by-line port of worker/src/review_filter.js (the phone and the Worker run that file); tests/test_voice.py runs
both on the same samples so they cannot drift apart. The wordlist is the same (filters/review_words.txt + .local.txt).
"""
from __future__ import annotations

import re
import unicodedata

SBASE = 0xAC00
L = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
V = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
T = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ",
     "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
SPLIT = {"ㅘ": "ㅗㅏ", "ㅙ": "ㅗㅐ", "ㅚ": "ㅗㅣ", "ㅝ": "ㅜㅓ", "ㅞ": "ㅜㅔ", "ㅟ": "ㅜㅣ", "ㅢ": "ㅡㅣ", "ㄳ": "ㄱㅅ", "ㄵ": "ㄴㅈ",
         "ㄶ": "ㄴㅎ", "ㄺ": "ㄹㄱ", "ㄻ": "ㄹㅁ", "ㄼ": "ㄹㅂ", "ㄽ": "ㄹㅅ", "ㄾ": "ㄹㅌ", "ㄿ": "ㄹㅍ", "ㅀ": "ㄹㅎ", "ㅄ": "ㅂㅅ",
         "ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㅅ", "ㅉ": "ㅈ"}
QWERTY = dict(zip("qwertyuiopasdfghjklzxcvbnm", "ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ"))
PHONE_RE = re.compile(r"01[016789][\s.\-]*\d{3,4}[\s.\-]*\d{4}")
LINK_RE = re.compile(r"https?:|www\.|\b[a-z0-9-]+\.(?:com|net|org|kr|io|me|ly|gg|co)\b|bit\.ly|open\.ka[k]ao|카톡\s*아이디", re.I)


def _syl(ch: str) -> bool:
    return "가" <= ch <= "힣"


def _jamo(ch: str) -> bool:
    return "ㄱ" <= ch <= "ㅣ"


def keys(s: str) -> str:
    out = []
    for ch in s:
        c = ord(ch) - SBASE
        if 0 <= c < 11172:
            for p in (L[c // 588], V[(c % 588) // 28], T[c % 28]):
                out.append(SPLIT.get(p, p))
        elif _jamo(ch):
            out.append(SPLIT.get(ch, ch))
    return "".join(out)


def collapse(syl: str) -> str:
    out = ""
    for ch in syl:
        c = ord(ch) - SBASE
        p = ord(out[-1]) - SBASE if out else -1
        if p >= 0 and p % 28 == 0 and c // 588 == 11 and (c % 588) // 28 == (p % 588) // 28:
            if c % 28:
                out = out[:-1] + chr(SBASE + p + c % 28)
            continue
        out += ch
    return out


def readings(text: str) -> dict:
    t = "".join(ch if _jamo(ch) else unicodedata.normalize("NFKC", ch) for ch in str(text or "")).lower()
    syl = "".join(ch for ch in t if _syl(ch))
    jamo = "".join(ch for ch in t if _jamo(ch))
    lat = "".join(ch for ch in t if "a" <= ch <= "z")
    typed = "".join(QWERTY.get(ch, "") for ch in lat)
    return {"raw": t, "syl": syl, "sylc": collapse(syl), "jamo": jamo, "lat": lat, "typed": typed}


def parse_list(text: str) -> dict:
    block, allow = [], []
    for line in str(text or "").split("\n"):
        w = line.strip().lower()
        if not w or w.startswith("#"):
            continue
        (allow if w.startswith("!") else block).append(re.sub(r"\s+", "", w.lstrip("!")))
    return {"block": block, "allow": allow}


def check(text: str, lst: dict) -> tuple[bool, str]:
    """(True, "") or (False, reason): word | phone | link."""
    r = readings(text)
    if PHONE_RE.search(re.sub(r"[^\d\s.\-]", " ", r["raw"])) or PHONE_RE.search(r["raw"]):
        return False, "phone"
    if LINK_RE.search(r["raw"]):
        return False, "link"
    syl, sylc = r["syl"], r["sylc"]
    for a in lst["allow"]:
        if any(_syl(ch) for ch in a):
            syl, sylc = syl.replace(a, ""), sylc.replace(a, "")
    jk = keys(r["jamo"])
    for w in lst["block"]:
        if re.fullmatch(r"[ㄱ-ㅣ]+", w):
            if w in r["jamo"] or keys(w) in jk:
                return False, "word"
            continue
        if re.fullmatch(r"[a-z]+", w):
            if w in r["lat"]:
                return False, "word"
            continue
        if w in syl or w in sylc:
            return False, "word"
        k = keys(w)
        if len(k) >= 4 and (k in r["typed"] or (r["jamo"] and k in jk)):
            return False, "word"
    return True, ""


_LIST: dict | None = None


def default_list() -> dict:
    global _LIST
    if _LIST is None:
        from .reviews import words_text
        _LIST = parse_list(words_text())
    return _LIST
