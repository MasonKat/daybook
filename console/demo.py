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
        for i, s in enumerate(studs):
            p = [0.92, 0.85, 0.7, 0.95, 0.6, 0.8][i]
            d = start
            while d <= today:
                # leave today open for the first student of each class so the e2e upload is a fresh one
                skip_today = d == today and i == 0
                if not skip_today and rng.random() < (p + (0.05 if d in sess else -0.1)):
                    t = time(rng.randint(15, 22), rng.randint(0, 59))
                    ts = datetime.combine(d, t, KST).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    conn.execute(
                        """INSERT INTO submissions (student_id, session_id, day, ts, photo_key, source, status)
                           VALUES (?, ?, ?, ?, ?, ?, 'received')""",
                        (s["id"], sess.get(d), d.isoformat(), ts, None, rng.choice(["phone"] * 9 + ["paper"])))
                d += timedelta(days=1)
            for d, sid in sess.items():
                if d < today:
                    ops.set_attitude(conn, s["id"], sid, rng.choice([1, 2, 2, 3, 3, 3, 0]))
            wk_now, wk_prev = M.iso_week(today), M.iso_week(today - timedelta(days=7))
            ops.add_note(conn, s["id"], wk_prev, NOTES[(i + 2) % len(NOTES)])
            ops.add_note(conn, s["id"], wk_now, NOTES[i % len(NOTES)])
    log_event(conn, "demo_seed", {"today": today.isoformat()})
    conn.commit()


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
