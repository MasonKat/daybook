"""F13.5: 오늘 푼 문제 수 (self-reported, 0-60, never compared) and the self-study block."""
from datetime import date, datetime, time

import pytest

from console import generate, ops, pull as pullmod
from console.config import Config
from console.db import KST

from conftest import TODAY


def item(tok, day, solved=None, **kw):
    it = {"token": tok, "day": day, "ts": f"{day}T11:30:00.000Z", "key": f"photo:{day}:{tok}", "n": 1, "set": ""}
    if solved is not None:
        it["solved"] = solved
    return {**it, **kw}


def test_pull_stores_the_count_and_keeps_a_teacher_correction(conn, klass):
    _, studs = klass
    tok = studs[0]["student_token"]
    pullmod.apply_items(conn, [item(tok, "2026-11-03", 12)])
    get = lambda d: conn.execute("SELECT solved FROM submissions WHERE student_id = ? AND day = ?", (studs[0]["id"], d)).fetchone()[0]
    assert get("2026-11-03") == 12
    pullmod.apply_items(conn, [item(tok, "2026-11-03")])                 # a replacement without a count keeps it
    assert get("2026-11-03") == 12
    pullmod.apply_items(conn, [item(tok, "2026-11-04", 99)])             # out of range: ignored
    assert get("2026-11-04") is None
    ops.set_solved(conn, studs[0]["id"], "2026-11-03", 10)               # the teacher corrects it
    pullmod.apply_items(conn, [item(tok, "2026-11-03", 15)])
    assert get("2026-11-03") == 10
    with pytest.raises(ops.OpError):
        ops.set_solved(conn, studs[0]["id"], "2026-11-03", 61)
    with pytest.raises(ops.OpError):
        ops.set_solved(conn, studs[0]["id"], "2026-11-05", 3)            # no submission that day


def test_weekly_sum_on_the_card_and_the_profile_and_class_hours(conn, klass, tmp_path):
    cls, studs = klass
    conn.execute("UPDATE classes SET start_time = '17:30', end_time = '20:00', self_study_until = '21:00' WHERE id = ?", (cls["id"],))
    for d, n in (("2026-11-03", 12), ("2026-11-05", 8), ("2026-10-30", 30)):
        conn.execute("INSERT INTO submissions (student_id, day, ts, source, solved) VALUES (?, ?, ?, 'phone', ?)",
                     (studs[0]["id"], d, f"{d}T11:00:00.000Z", n))
    conn.execute("INSERT INTO submissions (student_id, day, ts, source) VALUES (?, '2026-11-03', '2026-11-03T11:00:00.000Z', 'phone')",
                 (studs[1]["id"],))
    conn.commit()
    cfg = Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t")
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=datetime.combine(TODAY, time(21), KST))
    assert res.ok, [str(f) for f in res.findings]
    p0 = (tmp_path / "dist" / "p" / studs[0]["parent_token"] / "index.html").read_text(encoding="utf-8")
    s0 = (tmp_path / "dist" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    p1 = (tmp_path / "dist" / "p" / studs[1]["parent_token"] / "index.html").read_text(encoding="utf-8")
    assert "수업 17:30–20:00 · 자습 ~21:00" in p0
    assert "이번 주 20문제" in p0 and "이번 주 30문제" in p0          # this week (12 + 8) and last week's card
    assert "이번 주 푼 문제 20개" in s0
    assert 'id="solved"' not in p1                                   # no count entered: no row at all
    assert "평균" not in p0                                         # a sum of their own, never compared


def test_board_polls_through_the_self_study_block():
    w = ops.board_window(date(2026, 11, 5), "17:30", "21:00")
    assert w[1].strftime("%H:%M") == "21:00"
    assert ops.board_window(date(2026, 11, 5), "18:00", "19:00")[1].strftime("%H:%M") == "20:00"
    assert ops.board_window(date(2026, 11, 5), "18:00")[1].strftime("%H:%M") == "20:00"
