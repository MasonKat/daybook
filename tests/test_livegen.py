"""live-v2 `live gen`: a mocked Messages API (no network, no key), parsing, the checks, the key never written anywhere."""
import json
import threading
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from console import livegen as LG

GOOD = """### 문항 1
문제: 이차방정식 $x^2-5x+6=0$의 두 근의 합은?
보기1: $2$
보기2: $3$
보기3: $5$
보기4: $6$
보기5: $-5$
정답: 3
검산: 2+3
해설: 근과 계수의 관계로 합은 5예요.
### 문항 2
문제: $\\frac{1}{2}+\\frac{1}{3}$의 값은?
보기1: $\\frac{1}{6}$
보기2: $\\frac{2}{5}$
보기3: $\\frac{5}{6}$
보기4: $1$
보기5: $\\frac{1}{5}$
정답: 3
검산: 1/2+1/3
해설: 통분해요.
### 문항 3
문제: 검산이 틀린 문항
보기1: 1
보기2: 2
보기3: 3
보기4: 4
보기5: 5
정답: 1
검산: 2*2
### 문항 4
문제: 보기가 겹치는 문항
보기1: 1
보기2: 1
보기3: 3
보기4: 4
보기5: 5
정답: 3
"""
MORE = """### 문항 1
문제: $3^2$의 값은?
보기1: $6$
보기2: $8$
보기3: $9$
보기4: $12$
보기5: $27$
정답: 3
검산: 3**2
"""


class Fake(BaseHTTPRequestHandler):
    replies, seen = [], []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append({"key": self.headers.get("x-api-key"), "version": self.headers.get("anthropic-version"), "body": body})
        text = Fake.replies.pop(0) if Fake.replies else ""
        out = json.dumps({"content": [{"type": "text", "text": text}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def api(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("DAYBOOK_ANTHROPIC_URL", f"http://127.0.0.1:{srv.server_port}/v1/messages")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-NOT-A-REAL-KEY-123")
    Fake.replies, Fake.seen = [], []
    yield Fake
    srv.shutdown()


def test_generate_parses_checks_and_asks_once_more(api, tmp_path, capsys):
    api.replies = [GOOD, MORE]
    data = LG.generate("이차방정식", "easy", 5)
    assert [q["answer"] for q in data["questions"]] == ["3", "3", "3"] and data["calls"] == 2
    assert sorted(data["dropped"]) == sorted(["검산 4 differs from the marked choice", "needs 5 distinct choices"])
    s = api.seen[0]
    assert s["key"] == "sk-test-NOT-A-REAL-KEY-123" and s["version"] == "2023-06-01" and s["body"]["model"] == "claude-haiku-4-5-20251001"
    assert "JSON 금지" in s["body"]["messages"][0]["content"] and "2022 개정" in s["body"]["system"]
    assert "문항 수: 3" in api.seen[1]["body"]["messages"][0]["content"]                 # only the missing ones, once
    p = LG.write(data, tmp_path / "live" / "set.json")
    text = p.read_text(encoding="utf-8")
    assert "sk-test" not in text and "\\\\frac" in text and json.loads(text)["questions"][1]["choices"][2] == "$\\frac{5}{6}$"


def test_the_key_is_required_and_never_in_an_error(api, monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(LG.LiveGenError, match="ANTHROPIC_API_KEY is not set"):
        LG.generate("이차방정식", "easy", 5)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-SECRET")
    monkeypatch.setenv("DAYBOOK_ANTHROPIC_URL", "http://127.0.0.1:9/v1/messages")          # nothing listens there
    with pytest.raises(LG.LiveGenError) as e:
        LG.generate("이차방정식", "easy", 5)
    assert "sk-test" not in str(e.value)


def test_cli_writes_the_set_and_prints_no_key(api, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from console.cli import cli
    monkeypatch.chdir(tmp_path)
    api.replies = [GOOD + MORE.replace("### 문항 1", "### 문항 5") + MORE.replace("### 문항 1", "### 문항 6").replace("3^2", "3^3").replace("$27$", "$28$").replace("$9$", "$27$").replace("3**2", "3**3")]
    r = CliRunner().invoke(cli, ["--db", str(tmp_path / "x.sqlite"), "live", "gen", "--unit", "이차방정식", "--level", "easy", "--n", "5", "--out", "live/q.json"])
    assert r.exit_code == 0, r.output
    assert "sk-test" not in r.output and (tmp_path / "live" / "q.json").exists() and "세트 미리보기" in r.output
    assert CliRunner().invoke(cli, ["--db", str(tmp_path / "x.sqlite"), "live", "gen", "--unit", "x", "--level", "hard", "--out", "a.json"]).exit_code != 0


def test_safe_eval_is_arithmetic_only():
    assert LG.safe_eval("(3/4)*2+1") == Fraction(5, 2) and LG.safe_eval("2^10") == 1024 and LG.safe_eval("-1/3") == Fraction(-1, 3)
    for bad in ("__import__('os')", "x+1", "abs(-1)", "2**100", "[1][0]", "1 if 1 else 2", "()"):
        with pytest.raises((ValueError, SyntaxError)):
            LG.safe_eval(bad)
    assert LG.choice_value("$-\\dfrac{1}{3}$") == Fraction(-1, 3) and LG.choice_value("$x=2$") is None
