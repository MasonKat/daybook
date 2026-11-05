"""live-v2: `live gen` — generate an easy / moderate AMP Live set with the Anthropic Messages API (stdlib urllib only).

The key comes ONLY from the environment variable ANTHROPIC_API_KEY on the teacher's laptop: it is never written to
daybook.json, the Worker, Pages, a log, an event or any output, and every error message is built without it.
The model is asked for labelled plain-text sections (LaTeX inside JSON breaks on backslashes), which are parsed here and
verified: exactly 5 distinct choices, exactly one marked correct, and an optional 검산 line (a pure arithmetic expression,
evaluated with an ast whitelist over Fraction) must equal the marked choice. Items that fail are dropped; if the set comes
up short, the missing number is asked for once more. The set still needs the teacher's 세트 미리보기 approval to open.
"""
from __future__ import annotations

import ast
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
API_URL = "https://api.anthropic.com/v1/messages"
LEVELS = {"easy": "쉬움 (개념 확인, 한 단계 계산)", "moderate": "보통 (두세 단계, 대표 유형)"}
CURRICULUM = "2022 개정 교육과정 고등학교 수학: 공통수학1, 공통수학2, 대수, 미적분I"


class LiveGenError(RuntimeError):
    pass


# ---------------------------------------------------------------- the prompt (plain-text sections, never JSON)
SYSTEM = (f"너는 한국 고등학교 수학 학원 선생님을 돕는 출제 도우미야. 범위: {CURRICULUM}. "
          "교실에서 휴대폰으로 30초~2분 안에 푸는 5지선다 문항을 만든다. 수식은 $...$ 안에 LaTeX로 쓴다. "
          "학생 이름, 학교 이름, 실제 인물은 절대 쓰지 않는다. 형식 밖의 말은 쓰지 않는다.")


def prompt(unit: str, level: str, n: int) -> str:
    return (f"단원: {unit}\n난이도: {LEVELS[level]}\n문항 수: {n}\n\n"
            "각 문항을 정확히 아래 형식으로 써 줘 (JSON 금지, 줄 이름은 그대로):\n"
            "### 문항 1\n문제: (한두 문장, 수식은 $...$)\n보기1: ...\n보기2: ...\n보기3: ...\n보기4: ...\n보기5: ...\n"
            "정답: (1~5 중 하나의 숫자)\n검산: (정답이 수일 때만: 정답 값을 계산하는 순수 산술식, 예: (3/4)*2+1. 변수·함수 금지. 아니면 이 줄 생략)\n"
            "해설: (한 줄)\n\n보기 5개는 서로 달라야 하고, 정답은 하나뿐이어야 해.")


# ---------------------------------------------------------------- the API call (urllib only; the key never leaves this call)
def call_api(text: str, model: str, max_tokens: int = 4000, url: str | None = None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise LiveGenError("ANTHROPIC_API_KEY is not set. In PowerShell: $env:ANTHROPIC_API_KEY = \"<your key>\" "
                           "(this window only; never put it in daybook.json or a file)")
    body = json.dumps({"model": model, "max_tokens": max_tokens, "system": SYSTEM,
                       "messages": [{"role": "user", "content": text}]}).encode("utf-8")
    req = urllib.request.Request(url or os.environ.get("DAYBOOK_ANTHROPIC_URL") or API_URL, data=body, method="POST",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LiveGenError(f"the API answered {e.code} ({_reason(e)})") from None       # never the request (it holds the key)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise LiveGenError(f"cannot reach the API: {getattr(e, 'reason', e)}") from None
    parts = [c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"]
    if not parts:
        raise LiveGenError("the API returned no text")
    return "\n".join(parts)


def _reason(e: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(e.read().decode("utf-8")).get("error", {}).get("type", ""))[:60]
    except Exception:            # noqa: BLE001
        return ""


# ---------------------------------------------------------------- parsing and checks
ITEM_RE = re.compile(r"^#{2,4}\s*문항\s*\d+\s*$", re.M)
FIELD_RE = re.compile(r"^(문제|보기([1-5])|정답|검산|해설)\s*[:：]\s*(.*)$")


def parse(text: str) -> list[dict]:
    """Labelled sections -> [{q, choices, answer, check, explain}] (unchecked)."""
    out = []
    for block in ITEM_RE.split(text)[1:]:
        it = {"q": "", "choices": [""] * 5, "answer": "", "check": "", "explain": ""}
        last = None
        for line in block.strip().splitlines():
            m = FIELD_RE.match(line.strip())
            if m:
                name, k, val = m.group(1), m.group(2), m.group(3).strip()
                if k:
                    it["choices"][int(k) - 1] = val
                    last = ("c", int(k) - 1)
                else:
                    key = {"문제": "q", "정답": "answer", "검산": "check", "해설": "explain"}[name]
                    it[key] = val
                    last = ("f", key)
            elif line.strip() and last:                 # a continued line (a long stem)
                if last[0] == "c":
                    it["choices"][last[1]] += " " + line.strip()
                else:
                    it[last[1]] += " " + line.strip()
        out.append(it)
    return out


_OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}


def safe_eval(expr: str) -> Fraction:
    """A pure arithmetic expression (numbers, + - * / **, parentheses) as an exact Fraction; anything else is refused."""
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**").strip()
    if not expr or len(expr) > 120:
        raise ValueError("empty or too long")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return Fraction(str(n.value))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = ev(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
            b, e = ev(n.left), ev(n.right)
            if e.denominator != 1 or abs(e) > 12:
                raise ValueError("only small whole exponents")
            return b ** int(e)
        raise ValueError(f"not allowed: {type(n).__name__}")
    return ev(ast.parse(expr, mode="eval"))


def choice_value(text: str) -> Fraction | None:
    """A choice as a number when it is one ($\\frac{3}{4}$, -2, 0.5, $-\\dfrac{1}{3}$); None otherwise."""
    t = text.strip().strip("$").replace(" ", "")
    t = re.sub(r"\\[dt]?frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", t)
    if not re.fullmatch(r"[-+0-9./()]+", t):
        return None
    try:
        return safe_eval(t)
    except (ValueError, ZeroDivisionError, SyntaxError):
        return None


def verify(it: dict) -> str | None:
    """None when the item is sound, else why it was dropped."""
    ch = [c.strip() for c in it["choices"]]
    if not it["q"].strip():
        return "no stem"
    if any(not c for c in ch) or len(set(ch)) != 5:
        return "needs 5 distinct choices"
    if not re.fullmatch(r"[1-5]", it["answer"].strip()):
        return "the answer must be one of 1-5"
    if it.get("check"):
        try:
            v = safe_eval(it["check"])
        except (ValueError, ZeroDivisionError, SyntaxError) as e:
            return f"검산 is not plain arithmetic ({e})"
        mine = choice_value(ch[int(it["answer"]) - 1])
        if mine is not None and mine != v:
            return f"검산 {v} differs from the marked choice"
        if sum(1 for c in ch if choice_value(c) == v) > 1:
            return "검산 matches more than one choice"
    return None


# ---------------------------------------------------------------- the whole run
def generate(unit: str, level: str, n: int, model: str = DEFAULT_MODEL, api=None) -> dict:
    """{"title", "unit", "level", "questions": […], "dropped": [why…], "calls": k}. `api(text, model)` is call_api."""
    if level not in LEVELS:
        raise LiveGenError("--level easy or moderate")
    if not 5 <= n <= 10:
        raise LiveGenError("--n 5-10")
    if not unit.strip() or len(unit) > 40:
        raise LiveGenError("--unit: the 단원 name (e.g. 이차방정식)")
    api = api or call_api
    good, dropped, calls = [], [], 0
    want = n
    for _ in range(2):                                    # the first ask, then once more for what was dropped
        calls += 1
        for it in parse(api(prompt(unit, level, want), model)):
            why = verify(it)
            if why:
                dropped.append(why)
            elif len(good) < n and it["q"] not in {g["q"] for g in good}:
                good.append(it)
        want = n - len(good)
        if want <= 0:
            break
    if not good:
        raise LiveGenError("no item passed the checks; try again or another --unit")
    qs = [{"q": g["q"], "kind": "choice", "choices": [c.strip() for c in g["choices"]], "answer": g["answer"].strip(),
           **({"explain": g["explain"]} if g["explain"] else {})} for g in good]
    return {"title": f"{unit} · {'쉬움' if level == 'easy' else '보통'} {len(qs)}문항", "unit": unit, "level": level,
            "generated": {"model": model, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
            "questions": qs, "dropped": dropped, "calls": calls}


def write(data: dict, out: str | Path) -> Path:
    p = Path(out)
    if p.suffix != ".json":
        raise LiveGenError("--out must be a .json file (e.g. live/이차방정식-easy.json)")
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in data.items() if k not in ("dropped", "calls")}
    p.write_text(json.dumps(keep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p
