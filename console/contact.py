"""F13: the teacher's parent-contact desk. The system notices, drafts and remembers; the teacher calls.

Everything here stays on the laptop (Teacher tab, board "선생님 보기", CLI). Nothing is sent automatically and none of it
is ever published. Parent-facing wording lives in strings.ko.json under "parent_msg" (warm, formal, no emoji).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import models as M
from .config import Config, strings
from .db import log_event, now_utc_iso

KINDS = ("call", "message", "meeting")
CHANNELS = ("call", "kakao", "both")
STYLES = ("weekly", "needed")
GREETINGS = ("어머님", "아버님", "보호자님")
RULES = ("late", "absent", "homework", "test", "stale")
WINDOW = 6                       # the last 6 class days


class ContactError(ValueError):
    pass


def settings(cfg: Config) -> dict:
    x = cfg.extra or {}
    mins = {"call": 8, "message": 2, "line": 1, **(x.get("contact_minutes") or {})}
    return {"amber": int(x.get("contact_amber_days", 21)), "red": int(x.get("contact_red_days", 35)),
            "rotation_days": 7 * int(x.get("rotation_weeks", 4)), "minutes": mins,
            "budget": int(x.get("weekly_contact_minutes", 100))}


def P() -> dict:
    return strings()["parent_msg"]


# ---------------------------------------------------------------- F13.1 profile + log

def profile(conn: sqlite3.Connection, student_id: int) -> dict:
    r = conn.execute("SELECT * FROM parent_profiles WHERE student_id = ?", (student_id,)).fetchone()
    return {"channel": r["channel"], "style": r["style"], "greeting": r["greeting"]} if r else \
        {"channel": "call", "style": "weekly", "greeting": "보호자님"}


def set_profile(conn: sqlite3.Connection, student_id: int, channel: str | None = None, style: str | None = None,
                greeting: str | None = None) -> dict:
    cur = profile(conn, student_id)
    new = {"channel": channel or cur["channel"], "style": style or cur["style"], "greeting": greeting or cur["greeting"]}
    if new["channel"] not in CHANNELS or new["style"] not in STYLES or new["greeting"] not in GREETINGS:
        raise ContactError("channel: call|kakao|both, style: weekly|needed, greeting: 어머님|아버님|보호자님")
    conn.execute("""INSERT INTO parent_profiles (student_id, channel, style, greeting) VALUES (?, ?, ?, ?)
                    ON CONFLICT (student_id) DO UPDATE SET channel = excluded.channel, style = excluded.style,
                    greeting = excluded.greeting""", (student_id, new["channel"], new["style"], new["greeting"]))
    log_event(conn, "parent_profile", {"student_id": student_id, **new})
    return new


def log_contact(conn: sqlite3.Connection, student_id: int, kind: str, summary: str = "", direction: str = "out",
                follow_up: str | None = None, ts: str | None = None) -> int:
    if kind not in KINDS or direction not in ("out", "in"):
        raise ContactError("kind: call|message|meeting, direction: out|in")
    summary = " ".join((summary or "").split())
    if len(summary) > 120:
        raise ContactError("the summary is longer than 120 characters")
    if follow_up:
        try:
            date.fromisoformat(follow_up)
        except ValueError as e:
            raise ContactError("follow-up must be YYYY-MM-DD") from e
    ts = ts or now_utc_iso()
    cur = conn.execute("INSERT INTO contacts (student_id, ts, kind, direction, summary, follow_up) VALUES (?, ?, ?, ?, ?, ?)",
                       (student_id, ts, kind, direction, summary or None, follow_up or None))
    log_event(conn, "contact", {"student_id": student_id, "kind": kind, "direction": direction}, ts)
    return cur.lastrowid


def snooze(conn: sqlite3.Connection, student_id: int, rule: str, today: date, days: int = 7) -> str:
    if rule not in RULES and rule != "all":
        raise ContactError("unknown rule")
    until = (today + timedelta(days=days)).isoformat()
    conn.execute("INSERT INTO snoozes (student_id, rule, until, ts) VALUES (?, ?, ?, ?)", (student_id, rule, until, now_utc_iso()))
    log_event(conn, "contact_snooze", {"student_id": student_id, "rule": rule, "until": until})
    return until


def _kst_day(ts: str) -> date:
    return M.utc_iso_to_kst(ts).date()


def last_contact(conn: sqlite3.Connection, student_id: int) -> date | None:
    r = conn.execute("SELECT ts FROM contacts WHERE student_id = ? ORDER BY ts DESC LIMIT 1", (student_id,)).fetchone()
    return _kst_day(r["ts"]) if r else None


def days_since(conn: sqlite3.Connection, st: sqlite3.Row, today: date) -> int:
    """Days since the last contact, counted from the student's start when there was none (a fresh roster is not 'red')."""
    ref = last_contact(conn, st["id"]) or date.fromisoformat(st["created_at"][:10])
    return max(0, (today - ref).days)


def students(conn: sqlite3.Connection, class_id: int | None = None) -> list[sqlite3.Row]:
    q = """SELECT s.*, c.name AS class_name FROM students_public s LEFT JOIN classes c ON c.id = s.class_id
           WHERE s.active = 1""" + (" AND s.class_id = ?" if class_id else "") + " ORDER BY s.class_id, s.id"
    return conn.execute(q, (class_id,) if class_id else ()).fetchall()


def status(conn: sqlite3.Connection, cfg: Config, today: date, class_id: int | None = None) -> list[dict]:
    """연락 현황: every active student, longest without contact first."""
    st = settings(cfg)
    out = []
    for s in students(conn, class_id):
        d = days_since(conn, s, today)
        last = last_contact(conn, s["id"])
        out.append({"id": s["id"], "slug": s["slug"], "display_name": s["display_name"], "class_name": s["class_name"],
                    "days": d, "level": "red" if d >= st["red"] else "amber" if d >= st["amber"] else "ok",
                    "last": last.isoformat() if last else None, **profile(conn, s["id"])})
    out.sort(key=lambda x: (-x["days"], x["class_name"] or "", x["display_name"]))
    return out


# ---------------------------------------------------------------- F13.2 rules

def policy(cls: sqlite3.Row) -> dict:
    def g(k, d):
        try:
            v = cls[k]
        except (IndexError, KeyError):
            v = None
        return d if v in (None, "") else v
    return {"late_min": int(g("late_min", 10)), "late_count": int(g("late_count", 3)), "late_avg_min": int(g("late_avg_min", 15)),
            "start": g("start_time", None), "end": g("end_time", None), "self_study_until": g("self_study_until", None)}


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def class_record(conn: sqlite3.Connection, cls: sqlite3.Row, student_id: int, today: date, n: int = WINDOW) -> list[dict]:
    """The student's last n class days up to today: start time, 출석 time and minutes late, submitted or not."""
    pol = policy(cls)
    days = sorted((d for d in M.class_days(conn, cls, today) if d <= today), reverse=True)[:n]
    out = []
    for d in days:
        sess = conn.execute("SELECT * FROM sessions WHERE class_id = ? AND date = ?", (cls["id"], d.isoformat())).fetchone()
        start = (sess["start_time"] if sess and sess["start_time"] else None) or pol["start"]
        ck = conn.execute("""SELECT c.ts FROM checkins c JOIN sessions se ON se.id = c.session_id
                             WHERE c.student_id = ? AND se.class_id = ? AND se.date = ?""",
                          (student_id, cls["id"], d.isoformat())).fetchone()
        sub = conn.execute("SELECT 1 FROM submissions WHERE student_id = ? AND day = ? AND status != 'deleted'",
                           (student_id, d.isoformat())).fetchone()
        t_in = M.utc_iso_to_kst(ck["ts"]).strftime("%H:%M") if ck else None
        late = max(0, _mins(t_in) - _mins(start)) if (t_in and start) else None
        out.append({"date": d, "start": start, "in": t_in, "late": late, "submitted": bool(sub), "today": d == today})
    return out


def _md(d: date) -> str:
    return f"{d.month}/{d.day}"


def rules_for(conn: sqlite3.Connection, cfg: Config, s: sqlite3.Row, cls: sqlite3.Row | None, today: date) -> list[dict]:
    """All rules that fire for one student (before snoozes / contacts are applied)."""
    S = P()
    pol = policy(cls) if cls else policy({})
    flags = []
    rec = class_record(conn, cls, s["id"], today) if cls else []
    # 지각: later than start + late_min on >= late_count of the last 6, or an average >= late_avg_min
    timed = [r for r in rec if r["late"] is not None]
    lates = [r for r in timed if r["late"] > pol["late_min"]]
    avg = round(sum(r["late"] for r in timed) / len(timed)) if timed else 0
    if timed and (len(lates) >= pol["late_count"] or avg >= pol["late_avg_min"]):
        by_count = len(lates) >= pol["late_count"]
        shown = sorted(lates if by_count else timed, key=lambda r: r["date"])[-4:]
        times = ", ".join(f"{_md(r['date'])} {r['in']}" for r in shown)
        sfx = "" if by_count else "_avg"
        flags.append({"rule": "late", "level": "need", "evidence": max(r["date"] for r in (lates if by_count else timed)),
                      "reason": S["reason_late" + sfx].format(k=len(lates), n=len(timed), avg=avg, times=times),
                      "fact": S["fact_late" + sfx].format(k=len(lates), n=len(timed), avg=avg, times=times),
                      "proposal": S["prop_late"].format(start=(rec[0]["start"] if rec else None) or pol["start"] or "")})
    past = [r for r in rec if not r["today"]]
    # 결석: a past class day with neither 출석 nor a submission; 1 = notice, 2 in a row = 연락 필요
    run_abs = 0
    for r in past:
        if r["in"] is None and not r["submitted"]:
            run_abs += 1
        else:
            break
    if run_abs:
        missed = [r["date"] for r in past[:run_abs]]
        flags.append({"rule": "absent", "level": "need" if run_abs >= 2 else "notice", "evidence": max(missed),
                      "reason": S["reason_absent"].format(n=run_abs, dates=", ".join(_md(d) for d in sorted(missed))),
                      "fact": S["fact_absent"].format(n=run_abs, dates=", ".join(_md(d) for d in sorted(missed))),
                      "proposal": S["prop_absent"]})
    # 과제: no submission on >= 3 class days in a row
    run_hw = 0
    for r in past:
        if not r["submitted"]:
            run_hw += 1
        else:
            break
    if run_hw >= 3:
        missed = [r["date"] for r in past[:run_hw]]
        flags.append({"rule": "homework", "level": "need", "evidence": max(missed),
                      "reason": S["reason_homework"].format(n=run_hw, dates=", ".join(_md(d) for d in sorted(missed))),
                      "fact": S["fact_homework"].format(n=run_hw, dates=", ".join(_md(d) for d in sorted(missed))),
                      "proposal": S["prop_homework"]})
    # 테스트: a drop of >= 15 points in 확보 효율 versus the student's own last-3-round mean
    tests = conn.execute("SELECT date, efficiency FROM tests WHERE student_id = ? AND efficiency IS NOT NULL ORDER BY date",
                         (s["id"],)).fetchall()
    if len(tests) >= 4:
        prev = [t["efficiency"] for t in tests[-4:-1]]
        last = tests[-1]
        drop = round(100 * (sum(prev) / 3 - last["efficiency"]))
        if drop >= 15:
            flags.append({"rule": "test", "level": "need", "evidence": date.fromisoformat(last["date"]),
                          "reason": S["reason_test"].format(drop=drop, now=round(100 * last["efficiency"]),
                                                            mean=round(100 * sum(prev) / 3), d=_md(date.fromisoformat(last["date"]))),
                          "fact": S["fact_test"].format(drop=drop, now=round(100 * last["efficiency"]),
                                                        mean=round(100 * sum(prev) / 3)),
                          "proposal": S["prop_test"]})
    # 오래 연락 없음: the red state of 연락 현황
    st = settings(cfg)
    d = days_since(conn, s, today)
    if d >= st["red"]:
        flags.append({"rule": "stale", "level": "need", "evidence": today - timedelta(days=d),
                      "reason": S["reason_stale"].format(n=d), "fact": S["fact_stale"], "proposal": S["prop_stale"]})
    return flags


def _cleared(conn: sqlite3.Connection, student_id: int, flag: dict, today: date) -> bool:
    """A contact on/after the flag's latest evidence clears it until new data; a snooze holds it until its date."""
    if flag["rule"] != "stale":
        last = last_contact(conn, student_id)
        if last and last >= flag["evidence"]:
            return True
    sn = conn.execute("SELECT 1 FROM snoozes WHERE student_id = ? AND (rule = ? OR rule = 'all') AND until > ?",
                      (student_id, flag["rule"], today.isoformat())).fetchone()
    return bool(sn)


def good_facts(conn: sqlite3.Connection, s: sqlite3.Row, cls: sqlite3.Row | None, today: date, since: date | None = None) -> list[str]:
    """Only positive facts, most convincing first: test strength/rise, streak, ♥ days, the class reward, a moment."""
    S = P()
    since = since or today - timedelta(days=28)
    out = []
    tests = conn.execute("SELECT date, efficiency FROM tests WHERE student_id = ? AND efficiency IS NOT NULL ORDER BY date",
                         (s["id"],)).fetchall()
    if tests:
        last = tests[-1]["efficiency"]
        if last >= 0.8:
            out.append(S["good_test_high"].format(p=round(100 * last)))
        elif len(tests) >= 2 and last - tests[-2]["efficiency"] >= 0.05:
            out.append(S["good_test_up"].format(p=round(100 * last), d=round(100 * (last - tests[-2]["efficiency"]))))
    subs = {date.fromisoformat(r["day"]) for r in conn.execute(
        "SELECT day FROM submissions WHERE student_id = ? AND status != 'deleted'", (s["id"],))}
    if cls:
        streak = M.streak({d for d in subs if d <= today}, {d for d in M.class_days(conn, cls, today)}, today)
        if streak >= 3:
            out.append(S["good_streak"].format(n=streak))
    hearts = conn.execute("""SELECT COUNT(*) FROM attitude a JOIN sessions se ON se.id = a.session_id
                             WHERE a.student_id = ? AND a.points = 3 AND se.date BETWEEN ? AND ?""",
                          (s["id"], since.isoformat(), today.isoformat())).fetchone()[0]
    if hearts:
        out.append(S["good_hearts"].format(n=hearts))
    if cls:
        mo = conn.execute("""SELECT m.text, se.date FROM moments m JOIN sessions se ON se.id = m.session_id
                             WHERE m.class_id = ? AND se.date BETWEEN ? AND ? ORDER BY se.date DESC LIMIT 1""",
                          (cls["id"], since.isoformat(), today.isoformat())).fetchone()
        if mo and mo["text"]:
            out.append(S["good_moment"].format(d=_md(date.fromisoformat(mo["date"])), text=mo["text"]))
    return out


def flags(conn: sqlite3.Connection, cfg: Config, today: date, class_id: int | None = None) -> list[dict]:
    """연락 필요 and notices, per student, with the reason, a 3-line call script and a KakaoTalk draft."""
    S = P()
    out = []
    for s in students(conn, class_id):
        cls = conn.execute("SELECT * FROM classes WHERE id = ?", (s["class_id"],)).fetchone() if s["class_id"] else None
        live = [f for f in rules_for(conn, cfg, s, cls, today) if not _cleared(conn, s["id"], f, today)]
        if not live:
            continue
        prof = profile(conn, s["id"])
        good = good_facts(conn, s, cls, today)
        main = sorted(live, key=lambda f: (f["level"] != "need", RULES.index(f["rule"])))[0]
        opener = good[0] if good else S["good_default"]
        script = [opener, main["fact"], main["proposal"]]
        kakao = "\n".join([S["greeting"].format(g=prof["greeting"]), opener, main["fact"], main["proposal"], S["closing"]])
        out.append({"id": s["id"], "slug": s["slug"], "display_name": s["display_name"], "class_name": s["class_name"],
                    "level": "need" if any(f["level"] == "need" for f in live) else "notice",
                    "rules": [{"rule": f["rule"], "level": f["level"], "reason": f["reason"]} for f in live],
                    "script": script, "kakao": kakao, "channel": prof["channel"], "days": days_since(conn, s, today)})
    out.sort(key=lambda x: (x["level"] != "need", -len(x["rules"]), -x["days"]))
    return out


# ---------------------------------------------------------------- F13.3 the weekly 100-minute planner

def plan_week(conn: sqlite3.Connection, cfg: Config, today: date) -> dict:
    """이번 주 할 일: 연락 필요 first (never moved), then the 4-week rotation (oldest first), then the weekly lines.
    Read-only; commit pending writes first (the weekly-line drafts come from report.suggest_notes, which rolls back)."""
    st = settings(cfg)
    mins = st["minutes"]
    mon = M.week_bounds(today)[0]
    week = M.iso_week(today)
    flagged = {f["id"]: f for f in flags(conn, cfg, today) if f["level"] == "need"}
    stat = {x["id"]: x for x in status(conn, cfg, today)}

    def done_contact(sid):
        last = last_contact(conn, sid)
        return bool(last and last >= mon)

    items, moved, total = [], [], 0
    for sid, f in flagged.items():                                   # locked: never moves
        kind = "message" if f["channel"] == "kakao" else "call"
        items.append({"id": sid, "slug": f["slug"], "display_name": f["display_name"], "class_name": f["class_name"],
                      "type": kind, "minutes": mins[kind], "locked": True, "why": f["rules"][0]["reason"],
                      "done": done_contact(sid)})
        total += mins[kind]
    due = sorted((x for x in stat.values() if x["id"] not in flagged and x["days"] >= st["rotation_days"] - 7),
                 key=lambda x: -x["days"])
    for x in due:
        kind = "message" if x["channel"] == "kakao" else "call"
        locked = x["level"] == "red"
        it = {"id": x["id"], "slug": x["slug"], "display_name": x["display_name"], "class_name": x["class_name"], "type": kind,
              "minutes": mins[kind], "locked": locked, "why": P()["why_rotation"].format(n=x["days"]), "done": done_contact(x["id"])}
        if locked or total + it["minutes"] <= st["budget"] or it["done"]:
            items.append(it); total += it["minutes"]
        else:
            moved.append(it)
    from .report import suggest_notes
    for cls in conn.execute("SELECT * FROM classes ORDER BY id").fetchall():
        drafts = {slug: d for slug, _, d in suggest_notes(conn, cfg, cls, today, include_existing=True)}
        for s in students(conn, cls["id"]):
            if profile(conn, s["id"])["style"] != "weekly":
                continue
            has = conn.execute("SELECT 1 FROM notes WHERE student_id = ? AND week = ?", (s["id"], week)).fetchone()
            it = {"id": s["id"], "slug": s["slug"], "display_name": s["display_name"], "class_name": cls["name"], "type": "line",
                  "minutes": mins["line"], "locked": False, "why": drafts.get(s["slug"], ""), "done": bool(has)}
            if total + it["minutes"] <= st["budget"] or it["done"]:
                items.append(it); total += it["minutes"]
            else:
                moved.append(it)
    act = list(stat.values())
    contacted_week = sum(1 for x in act if x["last"] and date.fromisoformat(x["last"]) >= mon)
    within = sum(1 for x in act if x["days"] <= st["rotation_days"])
    return {"week": week, "items": items, "moved": moved, "total": total, "budget": st["budget"],
            "coverage": {"week": contacted_week, "within_pct": round(100 * within / len(act)) if act else 100,
                         "text": P()["coverage"].format(n=contacted_week, p=round(100 * within / len(act)) if act else 100,
                                                        w=st["rotation_days"] // 7)}}


# ---------------------------------------------------------------- F13.4 monthly good news

def last_week_of_month(d: date) -> bool:
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (nxt - d).days <= 7


def good_news(conn: sqlite3.Connection, cfg: Config, today: date) -> list[dict]:
    """One draft per active student with only positive facts from this month (all parents, hands-off ones too)."""
    S = P()
    first = today.replace(day=1)
    out = []
    for s in students(conn):
        cls = conn.execute("SELECT * FROM classes WHERE id = ?", (s["class_id"],)).fetchone() if s["class_id"] else None
        facts = good_facts(conn, s, cls, today, since=first)
        if cls:
            r = M.reward_for(conn, cls, today)
            if r.goal > 0:
                facts.append(S["good_reward"].format(p=round(r.pct), label=r.label))
        prof = profile(conn, s["id"])
        text = "\n".join([S["greeting"].format(g=prof["greeting"]), S["month_intro"].format(m=today.month)] +
                         (facts or [S["good_default"]]) + [S["closing"]])
        out.append({"id": s["id"], "slug": s["slug"], "display_name": s["display_name"], "class_name": s["class_name"],
                    "text": text, "facts": len(facts)})
    return out
