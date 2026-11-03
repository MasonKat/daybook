import base64
import re

from console.db import new_token


def test_token_is_128_bit_base32_26_chars():
    t = new_token()
    assert re.fullmatch(r"[a-z2-7]{26}", t)
    raw = base64.b32decode(t.upper() + "======")
    assert len(raw) == 16  # 128 bits


def test_tokens_are_unique():
    toks = {new_token() for _ in range(5000)}
    assert len(toks) == 5000


def test_students_get_distinct_tokens(conn, klass):
    _, studs = klass
    all_toks = [s["student_token"] for s in studs] + [s["parent_token"] for s in studs]
    assert len(set(all_toks)) == len(all_toks)
