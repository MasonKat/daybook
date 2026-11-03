"""Synthetic demo tenant (BUILD_BRIEF §10). Every name here is invented; no phone numbers."""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import models as M
from . import ops
from .config import ROOT
from .db import KST, log_event
from .importers import add_student

CLASSES = [
    # name, weekdays, reward
    ("고1A", "화,금", "피자 파티"),
    ("기초B", "월,수", "간식 데이"),
]
# (full_name, ext_id) — invented names, synthetic IDs
STUDENTS = {
    "고1A": [("한가람", "S901"), ("윤도담", "S902"), ("서하늘", "S903"), ("임새벽", "S904"), ("차온유", "S905"),
            ("노별찬", "S906")],
    "기초B": [("류이든", "S907"), ("문가온", "S908"), ("방나래", "S909"), ("탁보미", "S910"), ("피솔빈", "S911"),
            ("엄라온", "S912")],
}
NOTES = [
    "풀이 과정을 끝까지 적는 습관이 자리 잡고 있어요.",
    "어려운 문항에도 먼저 손을 대 보는 모습이 좋아졌어요.",
    "검산 한 줄을 더하면 실수가 확 줄 거예요.",
    "질문이 구체적이 되었어요. 이번 주도 잘했어요.",
    "숙제를 빠짐없이 올리고 있어요.",
    "개념 영상을 보고 와서 수업 이해가 빨라졌어요.",
]
# F1: the demo student for the taste build. Display name 콩; the full name never reaches a page.
KONG = {"full_name": "강콩", "display": "콩", "ext_id": "S913", "class": "고1A", "slug": "콩"}
KONG_NOTES = [
    "콩 한 알이 반 누적을 끌어올렸어요. 오늘도 고마워요!",
    "질문이 콩콩 튀어나와요. 그 질문 덕에 반 전체가 한 번 더 이해했어요.",
    "풀이 과정을 콩알만큼도 안 빼먹었어요. 이대로면 다음 테스트도 기대돼요.",
]
SET_IDS = ["Adaptive_Screeners_3S_Oct17_G10", "Adaptive_Screeners_3S_Oct24_G10", "Adaptive_Screeners_3S_Oct31_G10"]
ROUND_DATES = ["2026-10-17", "2026-10-24", "2026-10-31"]


class DemoError(RuntimeError):
    pass


def seed(conn: sqlite3.Connection, today: date | None = None, force: bool = False, rng_seed: int = 20261106) -> None:
    today = today or M.kst_today()
    n = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    demo_flag = conn.execute("SELECT 1 FROM events WHERE type = 'demo_seed' LIMIT 1").fetchone()
    if n and not demo_flag:
        raise DemoError("this database has real (non-demo) students; run the demo on its own file: "
                        "daybook --db demo.sqlite demo")
    if n and not force:
        raise DemoError("demo data already present; use --force to re-seed (or delete the demo database)")
    if n:
        for t in ("attitude", "submissions", "tests", "notes", "effort", "sessions", "students", "classes"):
            conn.execute(f"DELETE FROM {t}")

    rng = random.Random(rng_seed)
    start = today - timedelta(days=21)
    term = f"{start.isoformat()}..{(today + timedelta(days=56)).isoformat()}"
    for cname, days, reward in CLASSES:
        cur = conn.execute(
            "INSERT INTO classes (name, term, weekday_mask, reward_label) VALUES (?, ?, ?, ?)",
            (cname, term, M.mask_from_days(days), reward))
        cid = cur.lastrowid
        cls = conn.execute("SELECT * FROM classes WHERE id = ?", (cid,)).fetchone()
        studs = [add_student(conn, cid, full, ext_id=ext) for full, ext in STUDENTS[cname]]
        cdays = sorted(M.scheduled_days(cls["weekday_mask"], start, today))
        sess = {d: ops.ensure_session(conn, cid, d, SET_IDS[min(2, (d - start).days // 7)]) for d in cdays}
        for sid_ in sess.values():
            conn.execute("UPDATE sessions SET start_time = '18:00', code = ? WHERE id = ?", (f"{rng.randint(0, 9999):04d}", sid_))
        for i, s in enumerate(studs):
            p = [0.92, 0.85, 0.7, 0.95, 0.6, 0.8][i]
            d = start
            while d <= today:
                # leave today open for the first student of each class so the e2e upload is a fresh one
                skip_today = d == today and i == 0
                if not skip_today and rng.random() < (p + (0.05 if d in sess else -0.1)):
                    at = datetime.combine(d, time(rng.randint(15, 22), rng.randint(0, 59)), KST)
                    now = M.kst_now()
                    if at > now:  # never in the future: earlier today instead
                        at = max(datetime.combine(d, time(0, 5), KST), now - timedelta(minutes=rng.randint(5, 300)))
                    ts = at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    conn.execute(
                        """INSERT INTO submissions (student_id, session_id, day, ts, photo_key, source, status)
                           VALUES (?, ?, ?, ?, ?, ?, 'received')""",
                        (s["id"], sess.get(d), d.isoformat(), ts, None, rng.choice(["phone"] * 9 + ["paper"])))
                d += timedelta(days=1)
            for d, sid in sess.items():
                if d < today:
                    ops.set_attitude(conn, s["id"], sid, rng.choice([1, 2, 2, 3, 3, 3, 0]))
                    if rng.random() < 0.85:
                        at = datetime.combine(d, time(17, rng.randint(40, 59)) if rng.random() < .8 else time(18, rng.randint(0, 9)), KST)
                        ops.record_checkin(conn, s["id"], sid, at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                           rng.choice(["phone"] * 6 + ["teacher"]))
            wk_now, wk_prev = M.iso_week(today), M.iso_week(today - timedelta(days=7))
            ops.add_note(conn, s["id"], wk_prev, NOTES[(i + 2) % len(NOTES)])
            ops.add_note(conn, s["id"], wk_now, NOTES[i % len(NOTES)])
        if cname == KONG["class"]:
            _seed_kong(conn, cid, sess, today, rng)
    log_event(conn, "demo_seed", {"today": today.isoformat()})
    conn.commit()


def _seed_kong(conn: sqlite3.Connection, class_id: int, sess: dict, today: date, rng: random.Random) -> None:
    """콩's rich month: a 21-day streak with one honest gap (a no-class day), five ♥ days, three notes.
    Today is left open so a visitor can do today's ritual in the taste build."""
    s = add_student(conn, class_id, KONG["full_name"], display_name=KONG["display"], ext_id=KONG["ext_id"])
    conn.execute("UPDATE students SET slug = ? WHERE id = ?", (KONG["slug"], s["id"]))
    cls = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
    first = today - timedelta(days=22)
    gap = next(d for d in (today - timedelta(days=k) for k in range(9, 15))
               if not (cls["weekday_mask"] & (1 << d.weekday())))          # one honest gap on a no-class day
    d = first
    while d < today:
        if d != gap:
            at = datetime.combine(d, time(rng.randint(19, 22), rng.randint(0, 59)), KST)
            conn.execute(
                """INSERT INTO submissions (student_id, session_id, day, ts, photo_key, source, status)
                   VALUES (?, ?, ?, ?, NULL, 'phone', 'received')""",
                (s["id"], sess.get(d), d.isoformat(), at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")))
        d += timedelta(days=1)
    past = sorted(x for x in sess if x < today)
    hearts = set(past[-5:]) if len(past) >= 5 else set(past)
    for x in past:
        ops.set_attitude(conn, s["id"], sess[x], 3 if x in hearts else 2)
        at = datetime.combine(x, time(17, 50 + rng.randint(0, 9)), KST)
        ops.record_checkin(conn, s["id"], sess[x], at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "phone")
    lines = [("ok", "인수분해 공식이 드디어 손에 붙었어요"), ("confused", "나머지정리 2번 유형이 아직 헷갈려요"),
             ("rewatch", "11월 1주 풀이 영상 다시 볼래요"), ("ok", "오늘은 실수 없이 다 풀었다!"), (None, "검산 한 줄 습관 들이는 중"),
             ("ok", "")]
    days_with = sorted((x for x in (today - timedelta(days=k) for k in range(1, 15)) if x != gap), reverse=True)
    for (chip, text), x in zip(lines, days_with[::2]):
        conn.execute("UPDATE submissions SET reflection = ?, chip = ? WHERE student_id = ? AND day = ?",
                     (text or None, chip, s["id"], x.isoformat()))
    for k, text in enumerate(KONG_NOTES):
        ops.add_note(conn, s["id"], M.iso_week(today - timedelta(days=7 * (2 - k))), text)


DEMO_VIDEOS = [
    # title, group, days ago, kind, yt_id (synthetic, 11 chars), thumb, duration
    ("이차함수 꼭짓점 30초 컷", "고1 · 쇼츠", 0, "short", "AMPshort001", "thumbs/s1.jpg", ""),
    ("삼각형 넓이 한 번에", "고1 · 쇼츠", 1, "short", "AMPshort002", "thumbs/s2.jpg", ""),
    ("나머지정리 빠른 확인", "고1 · 쇼츠", 2, "short", "AMPshort003", "thumbs/s3.jpg", ""),
    ("부호 실수 줄이는 법", "기초 · 쇼츠", 4, "short", "AMPshort004", "thumbs/s4.jpg", ""),
    ("11월 1주 프린트 풀이", "고1 · 풀이", 1, "long", "AMPdemo0001", "thumbs/l1.jpg", "18:42"),
    ("나머지정리 개념 정리", "고1 · 개념", 3, "long", "AMPdemo0002", "thumbs/l2.jpg", "12:05"),
    ("10월 4주 프린트 풀이", "고1 · 풀이", 9, "long", "AMPdemo0003", "thumbs/l3.jpg", "21:17"),
    ("다항식의 연산", "고1 · 개념", 14, "long", "AMPdemo0004", "thumbs/l4.jpg", "9:48"),
    ("항등식과 미정계수법", "고1 · 개념", 20, "long", "AMPdemo0005", "thumbs/l5.jpg", "14:30"),
    ("기초 연산 점검", "기초 · 개념", 25, "long", None, None, "7:12"),
]


def write_demo_videos(today: date | None = None, out: Path | None = None) -> Path:
    """demo/videos.demo.json with dates relative to today (gitignored); thumbs are the committed demo/thumbs/*.jpg."""
    today = today or M.kst_today()
    out = out or ROOT / "demo" / "videos.demo.json"
    items = []
    for i, (title, group, ago, kind, yt, thumb, dur) in enumerate(DEMO_VIDEOS, start=1):
        v = {"title": title, "url": f"https://example.com/q/demo/{i}/", "group": group, "kind": kind,
             "added": (today - timedelta(days=ago)).isoformat()}
        if dur:
            v["duration"] = dur
        if yt:
            v["yt_id"] = yt
        if thumb:
            v["thumb"] = thumb
        items.append(v)
    out.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def write_screeners(out: Path | None = None, rng_seed: int = 7) -> list[Path]:
    """(Re)generate demo/screeners/round_<set_id>_<date>.json in the §5.1 format. The files are committed."""
    out = out or ROOT / "demo" / "screeners"
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(rng_seed)
    everyone = [st for c in STUDENTS.values() for st in c]
    base = {ext: (rng.uniform(0.45, 0.8), rng.uniform(0.35, 0.75), rng.uniform(-0.06, 0.08)) for _, ext in everyone}
    paths = []
    for r, (set_id, d) in enumerate(zip(SET_IDS, ROUND_DATES)):
        nq = 12
        rows = []
        for full, ext in everyone:
            acc, reach, drift = base[ext]
            acc = min(0.97, max(0.2, acc + drift * r + rng.uniform(-0.03, 0.03)))
            reach_v = round(min(0.98, max(0.1, reach + drift * r * 0.8 + rng.uniform(-0.02, 0.02))), 3)
            blank = rng.randint(0, 3)
            attempted = nq - blank
            correct = round(attempted * acc)
            rows.append({
                # one student is matched by name only (the `student` field holds a name in some exports)
                "student": full if ext == "S902" else ext,
                "name": full, "correct": correct, "wrong": attempted - correct, "blank": blank,
                "blank_rate": round(blank / nq, 3), "commitment": round(rng.uniform(0.5, 1.0), 2),
                "reach_ceiling": reach_v, "archetype": rng.choice(["도전형", "안정형", "신중형"]),
                "top_reason": rng.choice(["시간 부족", "개념 혼동", "계산 실수"]),
            })
        # 콩: a rising trend (fixed values)
        kc, kw, kb = ((6, 5, 1), (8, 3, 1), (10, 2, 0))[r]
        rows.append({"student": KONG["ext_id"], "name": KONG["full_name"], "correct": kc, "wrong": kw, "blank": kb,
                     "blank_rate": round(kb / nq, 3), "commitment": 0.9, "reach_ceiling": (0.42, 0.55, 0.68)[r],
                     "archetype": "도전형", "top_reason": "시간 부족"})
        # a student who is not on the roster: must be reported, never invented
        rows.append({"student": "S999", "name": "가상전학", "correct": 6, "wrong": 4, "blank": 2, "blank_rate": 0.167,
                     "commitment": 0.7, "reach_ceiling": 0.5, "archetype": "안정형", "top_reason": "시간 부족"})
        data = {
            "set_id": set_id, "date": d, "n_students": len(rows), "n_quizzes": nq,
            "set_pressure_index": round(rng.uniform(0.3, 0.7), 2), "concept_difficulty": round(rng.uniform(0.4, 0.8), 2),
            "top_challenging": [f"Q{rng.randint(1, nq)}" for _ in range(3)],
            "reason_distribution": {"시간 부족": 0.4, "개념 혼동": 0.35, "계산 실수": 0.25},
            "students": rows,
        }
        p = out / f"round_{set_id}_{d}.json"
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        paths.append(p)
    return paths


if __name__ == "__main__":
    for p in write_screeners():
        print(p)
