"""F13: the parent-contact desk. Rules fire and clear, per-class thresholds, snoozes, the 100-minute planner,
the 4-week rotation, the tone of parent drafts, and that nothing of it ever reaches a published page."""
import json
import re
from datetime import date, datetime, time, timedelta, timezone

import pytest

from console import contact as C, demo, generate, importers, ops
from console.config import ROOT, Config, strings
from console.db import KST
from console.importers import add_student

from conftest import TODAY   # Fri 2026-11-06; the klass fixture meets Tue + Fri

LAST6 = [date(2026, 11, 6), date(2026, 11, 3), date(2026, 10, 30), date(2026, 10, 27), date(2026, 10, 23), date(2026, 10, 20)]
PAST5 = LAST6[1:]


def utc(d: date, hh: int, mm: int) -> str:
    return datetime.combine(d, time(hh, mm), KST).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def k(conn, klass):
    cls, studs = klass
    conn.execute("UPDATE classes SET start_time = '18:00', end_time = '20:00' WHERE id = ?", (cls["id"],))
    old = utc(TODAY - timedelta(days=60), 12, 0)
    conn.execute("UPDATE students SET created_at = ?", (old,))
    for s in studs:     # a recent contact so that only the rule under test fires
        C.log_contact(conn, s["id"], "call", "학기 초 상담", ts=utc(date(2026, 10, 15), 13, 0))
    conn.commit()
    return conn.execute("SELECT * FROM classes WHERE id = ?", (cls["id"],)).fetchone(), studs


def attend(conn, cls, s, d, hhmm=(17, 55), submit=True):
    sid = ops.ensure_session(conn, cls["id"], d)
    if hhmm:
        ops.record_checkin(conn, s["id"], sid, utc(d, *hhmm), "phone")
    if submit:
        conn.execute("INSERT OR IGNORE INTO submissions (student_id, session_id, day, ts, source) VALUES (?, ?, ?, ?, 'phone')",
                     (s["id"], sid, d.isoformat(), utc(d, 20, 30)))


def rules(conn, cfg, s, day=TODAY):
    return {f["rule"]: f for f in next((x for x in C.flags(conn, cfg, day) if x["id"] == s["id"]), {"rules": []})["rules"]}


def regular(conn, cls, studs, days=LAST6, skip=()):
    for s in studs:
        for d in days:
            if (s["slug"], d) not in skip:
                attend(conn, cls, s, d)


def test_a_regular_class_has_no_flags(conn, cfg, k):
    cls, studs = k
    regular(conn, cls, studs)
    assert C.flags(conn, cfg, TODAY) == []


def test_late_by_count_fires_with_dates_and_times_and_clears_after_a_call(conn, cfg, k):
    cls, studs = k
    s = studs[0]
    regular(conn, cls, studs[1:])
    for d, t in zip(LAST6, [(18, 25), (18, 31), (17, 50), (18, 14), (17, 58), (17, 55)]):
        attend(conn, cls, s, d, t)
    r = rules(conn, cfg, s)
    assert set(r) == {"late"} and r["late"]["level"] == "need"
    assert "6회 중 3회 지각" in r["late"]["reason"] and "11/6 18:25" in r["late"]["reason"] and "10/27 18:14" in r["late"]["reason"]
    # 통화함 on the day of the latest evidence clears it until new data
    C.log_contact(conn, s["id"], "call", "출석 시간 이야기", ts=utc(TODAY, 21, 0))
    assert rules(conn, cfg, s) == {}
    # new data after the call: late again on the next class day -> fires again
    nxt = date(2026, 11, 10)
    attend(conn, cls, s, nxt, (18, 20))
    assert "late" in rules(conn, cfg, s, nxt)


def test_late_by_average_and_per_class_thresholds(conn, cfg, k):
    cls, studs = k
    s = studs[0]
    regular(conn, cls, studs[1:])
    for d in LAST6:                                     # 16-24 min late: avg 20, every day > 10
        attend(conn, cls, s, d, (18, 16 + LAST6.index(d) % 3 * 4))
    assert "late" in rules(conn, cfg, s)
    # a basic class sets late_min 30: no single day counts; the average (20) is still >= the default 15
    ops.class_policy(conn, cls, {"late_min": 30})
    r = rules(conn, cfg, s)
    assert "평균 20분" in r["late"]["reason"] and "회 지각," not in r["late"]["reason"]
    ops.class_policy(conn, cls, {"late_avg_min": 30})
    assert "late" not in rules(conn, cfg, s)
    # and the policy is logged
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'class_policy'").fetchone()[0] == 2


def test_absence_one_is_a_notice_two_in_a_row_need_contact(conn, cfg, k):
    cls, studs = k
    s = studs[0]
    regular(conn, cls, studs[1:])
    regular(conn, cls, [s], skip={(s["slug"], LAST6[1])})
    r = rules(conn, cfg, s)
    assert r["absent"]["level"] == "notice" and "11/3" in r["absent"]["reason"]
    f = next(x for x in C.flags(conn, cfg, TODAY) if x["id"] == s["id"])
    assert f["level"] == "notice"
    conn.execute("DELETE FROM checkins WHERE student_id = ?", (s["id"],))
    conn.execute("DELETE FROM submissions WHERE student_id = ? AND day = ?", (s["id"], "2026-10-30"))
    r = rules(conn, cfg, s)
    assert r["absent"]["level"] == "need" and "연속 결석 2회 · 10/30, 11/3" == r["absent"]["reason"]
    C.log_contact(conn, s["id"], "message", "", ts=utc(date(2026, 11, 4), 10, 0))
    assert "absent" not in rules(conn, cfg, s)


def test_homework_three_class_days_in_a_row(conn, cfg, k):
    cls, studs = k
    s = studs[0]
    regular(conn, cls, studs[1:])
    for d in LAST6:
        attend(conn, cls, s, d, submit=d not in PAST5[:2])
    assert "homework" not in rules(conn, cfg, s)          # two in a row: not yet
    conn.execute("DELETE FROM submissions WHERE student_id = ? AND day = '2026-10-27'", (s["id"],))
    r = rules(conn, cfg, s)
    assert r["homework"]["level"] == "need" and "10/27, 10/30, 11/3" in r["homework"]["reason"]
    conn.execute("INSERT INTO submissions (student_id, day, ts, source) VALUES (?, '2026-11-03', ?, 'teacher')",
                 (s["id"], utc(date(2026, 11, 3), 22, 0)))
    assert "homework" not in rules(conn, cfg, s)            # a late paper copy clears it


def test_test_drop_vs_the_last_three_rounds(conn, cfg, k):
    cls, studs = k
    s = studs[0]
    regular(conn, cls, studs)
    for i, (d, eff) in enumerate([("2026-10-10", .8), ("2026-10-17", .75), ("2026-10-24", .85), ("2026-10-31", .62)]):
        conn.execute("INSERT INTO tests (student_id, date, test_name, efficiency) VALUES (?, ?, ?, ?)", (s["id"], d, f"r{i}", eff))
    r = rules(conn, cfg, s)
    assert r["test"]["level"] == "need" and "18점 하락" in r["test"]["reason"]
    conn.execute("UPDATE tests SET efficiency = .7 WHERE student_id = ? AND date = '2026-10-31'", (s["id"],))
    assert "test" not in rules(conn, cfg, s)                # 10 points: below the threshold


def test_stale_is_the_red_state_and_a_call_clears_it(conn, cfg, k):
    cls, studs = k
    regular(conn, cls, studs)
    s = studs[1]
    conn.execute("DELETE FROM contacts WHERE student_id = ?", (s["id"],))
    st = {x["id"]: x for x in C.status(conn, cfg, TODAY)}
    assert st[s["id"]]["level"] == "red" and st[s["id"]]["days"] == 60
    assert C.status(conn, cfg, TODAY)[0]["id"] == s["id"]                 # longest without contact first
    assert st[studs[0]["id"]]["level"] == "amber" and st[studs[0]["id"]]["days"] == 22
    assert "stale" in rules(conn, cfg, s)
    C.log_contact(conn, s["id"], "call", "", ts=utc(TODAY, 15, 0))
    assert rules(conn, cfg, s) == {}
    assert C.status(conn, cfg, TODAY, )[-1]["days"] == 0


def test_snooze_holds_for_seven_days_and_is_logged(conn, cfg, k):
    cls, studs = k
    s = studs[0]
    regular(conn, cls, studs[1:])
    regular(conn, cls, [s], skip={(s["slug"], LAST6[1]), (s["slug"], LAST6[2])})
    assert "absent" in rules(conn, cfg, s)
    until = C.snooze(conn, s["id"], "all", TODAY)
    assert until == "2026-11-13" and rules(conn, cfg, s) == {}
    ev = conn.execute("SELECT payload_json FROM events WHERE type = 'contact_snooze'").fetchone()
    assert json.loads(ev[0]) == {"student_id": s["id"], "rule": "all", "until": "2026-11-13"}
    assert "absent" in rules(conn, cfg, s, date(2026, 11, 13))          # back after 7 days


def test_contact_log_validates_and_goes_to_events(conn, k):
    _, studs = k
    with pytest.raises(C.ContactError):
        C.log_contact(conn, studs[0]["id"], "call", "가" * 121)
    with pytest.raises(C.ContactError):
        C.log_contact(conn, studs[0]["id"], "sms")
    with pytest.raises(C.ContactError):
        C.log_contact(conn, studs[0]["id"], "call", follow_up="next week")
    n = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'contact'").fetchone()[0]
    C.log_contact(conn, studs[0]["id"], "meeting", "  면담:  진로  ", direction="in", follow_up="2026-11-20")
    row = conn.execute("SELECT * FROM contacts ORDER BY id DESC LIMIT 1").fetchone()
    assert row["summary"] == "면담: 진로" and row["direction"] == "in" and row["follow_up"] == "2026-11-20"
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'contact'").fetchone()[0] == n + 1
    with pytest.raises(C.ContactError):
        C.set_profile(conn, studs[0]["id"], channel="sms")
    assert C.set_profile(conn, studs[0]["id"], channel="kakao", greeting="아버님") == \
        {"channel": "kakao", "style": "weekly", "greeting": "아버님"}


# ---------------------------------------------------------------- the planner

@pytest.fixture
def big(conn):
    """30 students in two classes (the teacher's roster is growing to about 30), half 매주, staggered history."""
    conn.execute("INSERT INTO classes (name, term, weekday_mask, start_time) VALUES ('가반', '2026-09-01..2027-02-28', 21, '18:00')")
    conn.execute("INSERT INTO classes (name, term, weekday_mask, start_time) VALUES ('나반', '2026-09-01..2027-02-28', 42, '18:00')")
    names = "가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허고노"
    studs = [add_student(conn, 1 + i % 2, "합성" + names[i], ext_id=f"X{i:02d}") for i in range(30)]
    start = date(2026, 11, 7)
    conn.execute("UPDATE students SET created_at = ?", (utc(start - timedelta(days=120), 12, 0),))
    for i, s in enumerate(studs):
        C.set_profile(conn, s["id"], style="weekly" if i % 2 else "needed", channel="kakao" if i % 5 == 0 else "call")
        C.log_contact(conn, s["id"], "call", "", ts=utc(start - timedelta(days=i % 27), 13, 0))
    conn.commit()
    return studs, start


def test_rotation_covers_every_parent_within_four_weeks_over_eight_weeks(conn, cfg, big):
    studs, start = big
    for w in range(8):
        sat = start + timedelta(days=7 * w)
        pw = C.plan_week(conn, cfg, sat)
        assert pw["total"] <= pw["budget"] or all(i["locked"] for i in pw["items"] if i["type"] != "line")
        for it in pw["items"]:
            if it["type"] in ("call", "message") and not it["done"]:
                C.log_contact(conn, it["id"], it["type"], "", ts=utc(sat, 14, 0))
        conn.commit()
        for x in C.status(conn, cfg, sat):
            assert x["days"] <= 28, (w, x)
    # every gap between two contacts (and to the end) stays within 4 weeks
    end = start + timedelta(days=7 * 7)
    for s in studs:
        days = sorted(C._kst_day(r[0]) for r in conn.execute("SELECT ts FROM contacts WHERE student_id = ?", (s["id"],)))
        days.append(end)
        assert max((b - a).days for a, b in zip(days, days[1:])) <= 28, s["slug"]
    cov = C.plan_week(conn, cfg, end)["coverage"]
    assert cov["within_pct"] == 100 and cov["text"].endswith("4주 내 연락 100%")


def test_the_planner_never_moves_red_or_need_items(conn, big):
    studs, start = big
    cfg = Config(extra={"weekly_contact_minutes": 20})
    conn.execute("DELETE FROM contacts WHERE student_id IN (?, ?, ?, ?, ?)", tuple(s["id"] for s in studs[:5]))
    conn.commit()                      # plan_week reads the weekly-line drafts, which roll back uncommitted work
    pw = C.plan_week(conn, cfg, start)
    red = {x["id"] for x in C.status(conn, cfg, start) if x["level"] == "red"}
    assert len(red) == 5
    kept = {i["id"] for i in pw["items"] if i["type"] != "line"}
    assert red <= kept and all(i["locked"] for i in pw["items"] if i["id"] in red and i["type"] != "line")
    assert not red & {i["id"] for i in pw["moved"] if i["type"] != "line"}
    assert pw["total"] > pw["budget"]                                     # red items exceed the budget rather than move
    assert pw["moved"] and all(not i["locked"] for i in pw["moved"])
    assert {"call": 8, "message": 2, "line": 1} == {i["type"]: i["minutes"] for i in pw["items"] + pw["moved"]}


# ---------------------------------------------------------------- tone (F13.6)

EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍❤♥]")
PLAYFUL = re.compile(r"[~ㅋㅎㅠㅜ♥♡]|\^\^|!!|\?\?|\.\.\.|ㅎㅎ|요~|(?:용|당)(?=[.!?~]|\s*$)")   # "-용." / "-당" endings


def test_parent_drafts_are_formal_without_emoji_or_playful_endings():
    pm = strings()["parent_msg"]
    assert pm["greeting"].startswith("안녕하세요, {g}. AMP 수학입니다.")
    for key, val in pm.items():
        for text in (val if isinstance(val, list) else [val]):      # G4: the letter's lists and headings too
            assert not EMOJI.search(text), key
            assert not PLAYFUL.search(text), key
            assert "!" not in text, key
        if isinstance(val, list) or key.endswith(("_h", "_title")):
            continue                                                 # headings and list items are noun phrases
        text = val
        if not key.startswith(("reason_", "why_", "coverage", "_")):
            assert text.rstrip().endswith(("다.", "요.", "다", "요", ")")) or "{" in text[-12:], key


def test_demo_late_star_tops_contact_needed_with_math_strength_first(conn):
    demo.seed(conn, today=TODAY)
    importers.import_screeners(conn, ROOT / "demo" / "screeners")
    cfg = Config()
    assert conn.execute("SELECT COUNT(*) FROM students WHERE active = 1").fetchone()[0] == 22
    assert [r[0] for r in conn.execute("SELECT name FROM classes ORDER BY id")] == ["고1A", "기초B", "심화C", "케어D"]
    fl = C.flags(conn, cfg, TODAY)
    top = fl[0]
    star = conn.execute("SELECT id FROM students WHERE ext_id = ?", (demo.LATE_STAR,)).fetchone()[0]
    assert top["id"] == star and top["level"] == "need" and {r["rule"] for r in top["rules"]} >= {"late", "stale"}
    assert "180일" in next(r["reason"] for r in top["rules"] if r["rule"] == "stale")
    assert "수학 실력" in top["script"][0] and "확보 효율" in top["script"][0]
    assert top["kakao"].startswith("안녕하세요, 어머님. AMP 수학입니다.\n")
    for f in fl:                                 # every draft: no emoji, formal
        assert not EMOJI.search(f["kakao"]) and not PLAYFUL.search(f["kakao"])
    basic = conn.execute("SELECT * FROM classes WHERE name = '기초B'").fetchone()
    assert C.policy(basic)["late_min"] == 30
    care = conn.execute("SELECT * FROM classes WHERE name = '케어D'").fetchone()
    assert (care["start_time"], care["end_time"], care["self_study_until"]) == ("17:30", "20:00", "21:00")
    # the planner puts the star first and locked
    pw = C.plan_week(conn, cfg, TODAY)
    assert pw["items"][0]["id"] == star and pw["items"][0]["locked"]


# ---------------------------------------------------------------- never published

def test_flags_never_reach_dist_the_taste_build_or_the_projected_board(conn, tmp_path):
    from console import board as boardmod
    demo.seed(conn, today=TODAY)
    importers.import_screeners(conn, ROOT / "demo" / "screeners")
    cfg = Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t",
                 videos_path=str(ROOT / "videos.json"))
    fl = C.flags(conn, cfg, TODAY)
    assert fl
    needles = {"AMP 수학입니다", "연락 기록 없음", "회 지각", "연속 결석", "과제 미제출", "연락 필요"}
    needles |= {r[0] for r in conn.execute("SELECT summary FROM contacts WHERE summary IS NOT NULL")}
    now = datetime.combine(TODAY, time(21, 0), KST)
    for taste in (False, True):
        out = tmp_path / ("taste" if taste else "dist")
        res = generate.publish(conn, cfg, out, today=TODAY, now=now, taste=taste)
        assert res.ok, [str(f) for f in res.findings]
        assert not (out / "static" / "flags.js").exists() and not (out / "static" / "teacher.js").exists()
        for f in out.rglob("*"):
            if f.is_file() and f.suffix in (".html", ".json", ".js", ".css", ".txt", ".webmanifest"):
                text = f.read_text(encoding="utf-8")
                hit = [n for n in needles if n in text]
                assert not hit, (f, hit)
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    conn.commit()
    b = boardmod.Board(db_path, Config(moments_dir=str(tmp_path / "m")), "심화C", TODAY, poll=False)
    html = b.page()
    assert "tv-toggle" in html and 'id="tv-flags" hidden></section>' in html
    for r in fl[0]["rules"]:
        assert r["reason"] not in html
    assert "회 지각" not in html and "연락 기록 없음" not in html
