"""Domain rules and derived values (BUILD_BRIEF §8). Pure functions where possible."""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .db import KST

WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]
REACH_THRESHOLD = 0.03
EFFICIENCY_THRESHOLD = 0.01
TREND_ROUNDS = 6


# ---------------------------------------------------------------- dates

def kst_now() -> datetime:
    return datetime.now(KST)


def kst_today() -> date:
    return kst_now().date()


def parse_day(s: str) -> date:
    return date.fromisoformat(s)


def week_bounds(d: date) -> tuple[date, date]:
    """Monday..Sunday containing d."""
    mon = d - timedelta(days=d.weekday())
    return mon, mon + timedelta(days=6)


def iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_monday(week: str) -> date:
    y, w = week.split("-W")
    return date.fromisocalendar(int(y), int(w), 1)


def utc_iso_to_kst(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST)


# ---------------------------------------------------------------- weekday mask / term

def mask_from_days(days: str) -> int:
    """'화,목' or 'tue,thu' or 'Tue Thu' -> bit mask (Mon=1 .. Sun=64)."""
    en = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    mask = 0
    for part in days.replace(" ", ",").split(","):
        p = part.strip().lower()
        if not p:
            continue
        if p in WEEKDAYS_KO:
            mask |= 1 << WEEKDAYS_KO.index(p)
        elif p[:3] in en:
            mask |= 1 << en.index(p[:3])
        else:
            raise ValueError(f"unknown weekday: {part!r}")
    return mask


def mask_days(mask: int) -> list[int]:
    return [i for i in range(7) if mask & (1 << i)]


def parse_term(term: str | None) -> tuple[date | None, date | None]:
    """'YYYY-MM-DD..YYYY-MM-DD' -> (start, end). Anything else -> (None, None)."""
    if not term or ".." not in term:
        return None, None
    a, b = term.split("..", 1)
    try:
        return date.fromisoformat(a.strip()), date.fromisoformat(b.strip())
    except ValueError:
        return None, None


def scheduled_days(mask: int, start: date, end: date) -> set[date]:
    out = set()
    d = start
    while d <= end:
        if mask & (1 << d.weekday()):
            out.add(d)
        d += timedelta(days=1)
    return out


def cancelled_days(conn: sqlite3.Connection, class_id: int) -> dict[date, str]:
    """F5: days the teacher cancelled (`daybook class cancel`), with the reason."""
    return {parse_day(r["date"]): r["cancelled"] for r in conn.execute(
        "SELECT date, cancelled FROM sessions WHERE class_id = ? AND cancelled IS NOT NULL", (class_id,))}


def class_days(conn: sqlite3.Connection, cls: sqlite3.Row, upto: date) -> set[date]:
    """Days that count as a class day: explicit `sessions` rows plus the weekly schedule inside the term,
    minus cancelled days."""
    days = {parse_day(r["date"]) for r in conn.execute(
        "SELECT date FROM sessions WHERE class_id = ? AND cancelled IS NULL", (cls["id"],))}
    t_start, t_end = parse_term(cls["term"])
    if t_start and cls["weekday_mask"]:
        days |= scheduled_days(cls["weekday_mask"], t_start, min(t_end, upto))
    days -= set(cancelled_days(conn, cls["id"]))
    return {d for d in days if d <= upto}


def remaining_sessions(cls: sqlite3.Row, today: date, cancelled: set[date] | None = None) -> int:
    _, t_end = parse_term(cls["term"])
    if not t_end or not cls["weekday_mask"]:
        return 0
    return len(scheduled_days(cls["weekday_mask"], today, t_end) - (cancelled or set()))


# ---------------------------------------------------------------- streak

def streak(sub_days: set[date], cls_days: set[date], today: date, floor: date | None = None,
           today_open: bool = True) -> int:
    """Consecutive-day streak ending today (§8).

    A day with a submission counts. A class day without a submission breaks the streak.
    A day with neither is skipped (does not break). If `today_open`, today without a
    submission is still in progress and neither counts nor breaks.
    """
    if floor is None:
        candidates = list(sub_days) + list(cls_days)
        if not candidates:
            return 0
        floor = min(candidates)
    n = 0
    d = today
    while d >= floor:
        if d in sub_days:
            n += 1
        elif d in cls_days and not (today_open and d == today):
            break
        d -= timedelta(days=1)
    return n


# ---------------------------------------------------------------- attitude / reward

def class_total(conn: sqlite3.Connection, class_id: int, since: date | None = None, until: date | None = None) -> int:
    """Attitude points of the active students + class-wide bonuses (F7 AMP Live, 반 전체 칭찬). Never decreases.
    F22.1: since / until (inclusive) limit it to one cycle (a month)."""
    lo, hi = (since or date(1970, 1, 1)).isoformat(), (until or date(9999, 12, 31)).isoformat()
    row = conn.execute(
        """SELECT COALESCE(SUM(a.points), 0) FROM attitude a
           JOIN students_public s ON s.id = a.student_id JOIN sessions se ON se.id = a.session_id
           WHERE s.class_id = ? AND s.active = 1 AND se.date BETWEEN ? AND ?""",
        (class_id, lo, hi),
    ).fetchone()
    # class_bonus.ts is UTC: a KST day starts at 15:00 UTC the day before
    t0 = (since - timedelta(days=1)).isoformat() + "T15:00" if since else ""
    t1 = until.isoformat() + "T15:00" if until else "9999"
    bonus = conn.execute("SELECT COALESCE(SUM(points), 0) FROM class_bonus WHERE class_id = ? AND ts >= ? AND ts < ?",
                         (class_id, t0, t1)).fetchone()
    return int(row[0]) + int(bonus[0])


def active_count(conn: sqlite3.Connection, class_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM students_public WHERE class_id = ? AND active = 1", (class_id,)
    ).fetchone()[0]


def compute_goal(active_students: int, remaining: int, coef: float) -> int:
    """goal = active_students x remaining_sessions x goal_coef (rounded up)."""
    return int(math.ceil(active_students * remaining * (coef if coef is not None else 2.4) - 1e-9))


def milestones(goal: int, fracs: str | None) -> list[int]:
    out = []
    for f in (fracs or "0.33,0.67").split(","):
        f = f.strip()
        if f:
            out.append(int(round(goal * float(f))))
    return out


@dataclass
class Reward:
    label: str
    total: int
    goal: int
    milestones: list[int]
    ladder: list = None          # F22.1: [{"pct", "label", "points", "reached"}] (the last one is the reward itself)
    cycle: str = "term"
    since: date | None = None

    @property
    def to_go(self) -> int:
        return max(0, self.goal - self.total)

    @property
    def pct(self) -> float:
        return 0.0 if self.goal <= 0 else min(100.0, 100.0 * self.total / self.goal)

    def milestone_pcts(self) -> list[float]:
        return [0.0 if self.goal <= 0 else min(100.0, 100.0 * m / self.goal) for m in self.milestones]


def ladder_of(cls, goal: int, total: int) -> list[dict]:
    """F22.1: up to 3 milestones, each with its own reward label (a JSON list on the class); else the old fractions with
    the class's reward label at 100 %."""
    try:
        ms = json.loads(cls["milestones"]) if cls["milestones"] else None
    except (ValueError, TypeError, IndexError, KeyError):
        ms = None
    if not ms:
        ms = [{"pct": round(100 * float(f)), "label": ""} for f in (cls["milestone_fracs"] or "0.33,0.67").split(",") if f.strip()]
        ms.append({"pct": 100, "label": cls["reward_label"] or ""})
    out = []
    for m in sorted(ms, key=lambda m: m["pct"])[:3 if cls["milestones"] else 4]:
        pts = int(math.ceil(goal * float(m["pct"]) / 100 - 1e-9)) if goal > 0 else 0
        out.append({"pct": int(m["pct"]), "label": str(m.get("label") or ""), "points": pts, "reached": goal > 0 and total >= pts})
    return out


def month_days(cls, start: date, cancelled: set[date]) -> int:
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    t0, t1 = parse_term(cls["term"])
    lo, hi = max(start, t0 or start), min(end, t1 or end)
    return len(scheduled_days(cls["weekday_mask"], lo, hi) - cancelled) if cls["weekday_mask"] and lo <= hi else 0


def reward_for(conn: sqlite3.Connection, cls: sqlite3.Row, today: date, freeze: bool = False) -> Reward:
    """Class reward bar. term: the goal is computed once and frozen into reward_goal_points (see DECISIONS). F22.1 month:
    the total and the goal restart on the 1st; each month's goal is frozen in goal_cycles (past months = history)."""
    from .db import log_event
    if (cls["cycle"] if "cycle" in cls.keys() else "term") == "month":
        start = today.replace(day=1)
        row = conn.execute("SELECT goal FROM goal_cycles WHERE class_id = ? AND start = ?", (cls["id"], start.isoformat())).fetchone()
        goal = row[0] if row else (cls["reward_goal_points"] or compute_goal(
            active_count(conn, cls["id"]), month_days(cls, start, set(cancelled_days(conn, cls["id"]))), cls["goal_coef"]))
        if freeze and not row and goal > 0:
            conn.execute("INSERT INTO goal_cycles (class_id, start, goal) VALUES (?, ?, ?)", (cls["id"], start.isoformat(), goal))
            log_event(conn, "reward_goal_frozen", {"class_id": cls["id"], "goal": goal, "cycle": start.isoformat()})
        total = class_total(conn, cls["id"], since=start)
        lad = ladder_of(cls, int(goal), total)
        return Reward(cls["reward_label"] or "", total, int(goal), [m["points"] for m in lad[:-1]], lad, "month", start)
    goal = cls["reward_goal_points"]
    if goal is None:
        goal = compute_goal(active_count(conn, cls["id"]),
                            remaining_sessions(cls, today, set(cancelled_days(conn, cls["id"]))), cls["goal_coef"])
        if freeze and goal > 0:
            conn.execute("UPDATE classes SET reward_goal_points = ? WHERE id = ?", (goal, cls["id"]))
            log_event(conn, "reward_goal_frozen", {"class_id": cls["id"], "goal": goal, "date": today.isoformat()})
    total = class_total(conn, cls["id"])
    lad = ladder_of(cls, int(goal), total) if "milestones" in cls.keys() else []
    ms = [m["points"] for m in lad[:-1]] if lad and cls["milestones"] else milestones(int(goal), cls["milestone_fracs"])
    return Reward(cls["reward_label"] or "", total, int(goal), ms, lad, "term", None)


def cycle_history(conn: sqlite3.Connection, cls: sqlite3.Row, today: date) -> list[dict]:
    """F22.1: the month cycles before this one (newest first): its goal, its final total, the milestones it reached."""
    out = []
    for r in conn.execute("SELECT * FROM goal_cycles WHERE class_id = ? AND start < ? ORDER BY start DESC", (cls["id"], today.replace(day=1).isoformat())):
        start = date.fromisoformat(r["start"])
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        total = class_total(conn, cls["id"], since=start, until=end)
        out.append({"start": r["start"], "goal": r["goal"], "total": total,
                    "reached": [m["label"] or f"{m['pct']}%" for m in ladder_of(cls, r["goal"], total) if m["reached"]]})
    return out


DEFAULT_RUBRIC = {"1": "출석하고 과제 사진을 올렸어요", "2": "1점 + 수업 중 질문·발표·풀이 설명을 1번 이상 했어요",
                  "3": "2점 + 오늘 문제를 끝까지 풀었거나, 오답을 스스로 고쳤거나, 친구를 도와줬어요"}


def rubric_of(cls) -> dict:
    try:
        r = json.loads(cls["rubric"]) if cls["rubric"] else {}
    except (ValueError, TypeError):
        r = {}
    return {k: str(r.get(k) or DEFAULT_RUBRIC[k]) for k in ("1", "2", "3")}


# ---------------------------------------------------------------- screener trend

def efficiency(correct: int, wrong: int) -> float:
    attempted = (correct or 0) + (wrong or 0)
    return 1.0 if attempted == 0 else (correct or 0) / attempted


def slope(values: list[float]) -> float:
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return 0.0
    n = len(pts)
    mx = sum(i for i, _ in pts) / n
    my = sum(v for _, v in pts) / n
    den = sum((i - mx) ** 2 for i, _ in pts)
    return 0.0 if den == 0 else sum((i - mx) * (v - my) for i, v in pts) / den


def direction(values: list[float], threshold: float) -> str:
    """'up' | 'keep' | 'adjust' from the least-squares slope per round."""
    s = slope(values)
    if s > threshold:
        return "up"
    if s < -threshold:
        return "adjust"
    return "keep"


# ---------------------------------------------------------------- names

def default_display_name(full_name: str) -> str:
    """The given name: the last 2 characters of a 3-character Korean name, else the whole name."""
    name = full_name.strip()
    if len(name) == 3 and all("가" <= ch <= "힣" for ch in name):
        return name[1:]
    return name


def unique_slug(conn: sqlite3.Connection, base: str) -> str:
    base = base.strip().replace(" ", "_") or "student"
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM students WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}{n}"
        n += 1
    return slug
