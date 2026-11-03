from datetime import date, timedelta

import pytest

from console import models as M
from console import ops

D = date.fromisoformat


def days(*s):
    return {D(x) for x in s}


# --- streak (§8): a day counts if it has a submission; a class day without one breaks; other days are skipped
def test_streak_counts_consecutive_submissions():
    subs = days("2026-11-03", "2026-11-04", "2026-11-05")
    assert M.streak(subs, set(), D("2026-11-05")) == 3


def test_streak_skips_days_with_neither_class_nor_submission():
    # Tue class + submission, Wed nothing, Thu nothing, Fri submission -> 2
    subs = days("2026-11-03", "2026-11-06")
    cls = days("2026-11-03", "2026-11-06")
    assert M.streak(subs, cls, D("2026-11-06")) == 2


def test_streak_breaks_on_class_day_without_submission():
    subs = days("2026-11-02", "2026-11-04", "2026-11-05")
    cls = days("2026-11-03")  # Tue class, no submission
    assert M.streak(subs, cls, D("2026-11-05")) == 2


def test_streak_today_is_open():
    subs = days("2026-11-04", "2026-11-05")
    cls = days("2026-11-06")  # class today, not submitted yet
    assert M.streak(subs, cls, D("2026-11-06")) == 2
    assert M.streak(subs, cls, D("2026-11-06"), today_open=False) == 0
    assert M.streak(subs | days("2026-11-06"), cls, D("2026-11-06")) == 3


def test_streak_zero_without_data():
    assert M.streak(set(), set(), D("2026-11-06")) == 0


# --- class total / goal / milestones
def test_class_total_counts_active_students_only(conn, klass):
    cls, studs = klass
    sid = ops.ensure_session(conn, cls["id"], D("2026-11-03"))
    ops.set_attitude(conn, studs[0]["id"], sid, 3)
    ops.set_attitude(conn, studs[1]["id"], sid, 2)
    ops.set_attitude(conn, studs[2]["id"], sid, 1)
    assert M.class_total(conn, cls["id"]) == 6
    conn.execute("UPDATE students SET active = 0 WHERE id = ?", (studs[2]["id"],))
    assert M.class_total(conn, cls["id"]) == 5


def test_second_tap_corrects_the_session_score(conn, klass):
    cls, studs = klass
    sid = ops.ensure_session(conn, cls["id"], D("2026-11-03"))
    ops.set_attitude(conn, studs[0]["id"], sid, 1)
    ops.set_attitude(conn, studs[0]["id"], sid, 3)
    assert M.class_total(conn, cls["id"]) == 3
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='attitude'").fetchone()[0] == 2
    with pytest.raises(ops.OpError):
        ops.set_attitude(conn, studs[0]["id"], sid, 4)


def test_remaining_sessions_and_goal(conn, klass):
    cls, _ = klass
    # Tue+Fri from Fri 2026-12-18 through 2026-12-31: 18, 22, 25, 29 -> 4
    assert M.remaining_sessions(cls, D("2026-12-18")) == 4
    assert M.compute_goal(3, 4, 2.4) == 29  # ceil(28.8)
    assert M.compute_goal(6, 10, 2.4) == 144


def test_goal_is_frozen_at_publish(conn, klass):
    cls, _ = klass
    r = M.reward_for(conn, cls, D("2026-12-18"), freeze=True)
    assert r.goal == 29
    cls2 = conn.execute("SELECT * FROM classes WHERE id = ?", (cls["id"],)).fetchone()
    assert cls2["reward_goal_points"] == 29
    # later publishes keep the frozen goal even though fewer sessions remain
    assert M.reward_for(conn, cls2, D("2026-12-29")).goal == 29


def test_milestones():
    assert M.milestones(144, "0.33,0.67") == [48, 96]
    r = M.Reward("피자", 50, 100, [33, 67])
    assert r.to_go == 50 and r.pct == 50.0 and r.milestone_pcts() == [33.0, 67.0]
    assert M.Reward("x", 150, 100, []).to_go == 0


# --- trend
def test_direction_thresholds():
    assert M.direction([0.5, 0.52, 0.54], M.EFFICIENCY_THRESHOLD) == "up"       # slope 0.02 > 0.01
    assert M.direction([0.5, 0.52, 0.54], M.REACH_THRESHOLD) == "keep"         # 0.02 < 0.03
    assert M.direction([0.6, 0.5, 0.4], M.REACH_THRESHOLD) == "adjust"
    assert M.direction([0.5], M.REACH_THRESHOLD) == "keep"


def test_efficiency():
    assert M.efficiency(0, 0) == 1.0
    assert M.efficiency(6, 2) == 0.75


# --- names, dates
def test_display_name_rule():
    assert M.default_display_name("한가람") == "가람"
    assert M.default_display_name("남궁하늘") == "남궁하늘"
    assert M.default_display_name("이솔") == "이솔"
    assert M.default_display_name("Ann Lee") == "Ann Lee"


def test_weekday_mask_and_week():
    assert M.mask_from_days("화,금") == 2 | 16 == M.mask_from_days("tue fri")
    assert M.iso_week(D("2026-11-06")) == "2026-W45"
    assert M.week_bounds(D("2026-11-06")) == (D("2026-11-02"), D("2026-11-08"))
    with pytest.raises(ValueError):
        M.mask_from_days("xyz")


def test_cancelled_day_stops_counting(conn, klass, tmp_path):
    from datetime import datetime
    from console import generate
    from console.config import Config
    from console.db import KST
    cls, studs = klass
    s = studs[0]
    # Tue 11-03 class, Wed, Thu, Fri 11-06 class: submissions on Wed and Thu only
    for d in ("2026-11-04", "2026-11-05"):
        ops.teacher_submit(conn, s["id"], D(d))
    cd = M.class_days(conn, cls, D("2026-11-06"))
    subs = days("2026-11-04", "2026-11-05")
    assert M.streak(subs, cd, D("2026-11-06")) == 2                     # Tue was a class day (before the gap)
    assert M.streak(subs, cd, D("2026-11-07"), today_open=False) == 0   # Fri missed breaks it
    ops.cancel_class(conn, cls, D("2026-11-06"))
    cd = M.class_days(conn, cls, D("2026-11-07"))
    assert D("2026-11-06") not in cd
    assert M.streak(subs, cd, D("2026-11-07"), today_open=False) == 2   # 휴강 no longer breaks the streak
    assert M.remaining_sessions(cls, D("2026-11-06"), set(M.cancelled_days(conn, cls["id"]))) == \
        M.remaining_sessions(cls, D("2026-11-06")) - 1
    conn.commit()
    res = generate.publish(conn, Config(worker_url="https://w.example"), tmp_path / "dist", today=D("2026-11-07"),
                           now=datetime(2026, 11, 7, 10, 0, tzinfo=KST))
    assert res.ok
    html = (tmp_path / "dist" / "s" / s["student_token"] / "index.html").read_text(encoding="utf-8")
    assert '<li class="tile cancel" data-date="2026-11-06"><span class="tl tnum">11/6</span><span class="tw">휴강</span>' in html
    ops.cancel_class(conn, cls, D("2026-11-06"), None)                  # --undo
    assert D("2026-11-06") in M.class_days(conn, cls, D("2026-11-07"))
