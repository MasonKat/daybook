"""F19.2 영상 반응 and F19.3 the 2-week video rank.

Reactions: 5 options per video (labels in daybook.json "video_reactions"; defaults 🔥 최고예요 · 👍 좋아요 · 🤔 궁금해요 ·
😵 어려워요 · 💪 도전해볼래요), one per student per video, changeable. Students see only their own choice and the pool's
emoji totals once 3 different students reacted; 어려워요 totals are teacher-only (Teacher tab → 영상). The Worker pools
totals by `rank_scope` (grade by default: ~10 students per grade, while a class has 5-6): the console sends class key ->
pool key with every token-map push.

Rank: videos, never students. Per pool and 2-week period (the 베스트 리뷰 periods): score = students who watched it
(>= 10 s) + reactions weighted (최고 3, 좋아요 2, 도전 2, 궁금해요 1; 어려워요 0). Recomputed at each publish while the period
runs; once it is over, its list is frozen. The student-side "열심히 본 친구" stays per class, positive-only: top 3 names in
korea mode (by different videos watched in the period), nothing in manitoba mode, never a bottom list.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta

from . import models as M
from .config import Config

KEYS = ("fire", "good", "curious", "hard", "try")
SHOWN = ("fire", "good", "curious", "try")
WEIGHTS = {"fire": 3, "good": 2, "try": 2, "curious": 1, "hard": 0}
SCOPES = ("grade", "class", "all")
MIN_SEC = 10
TOP_N = 10


def labels(cfg: Config, S: dict) -> list[dict]:
    """[{key, emoji, text}] in the fixed order; daybook.json "video_reactions": {"fire": "🔥 최고예요", …} overrides."""
    conf = cfg.extra.get("video_reactions") if isinstance(cfg.extra.get("video_reactions"), dict) else {}
    out = []
    for k in KEYS:
        lab = str(conf.get(k) or S["vr_" + k]).strip()
        emoji, _, text = lab.partition(" ")
        out.append({"key": k, "emoji": emoji, "text": text.strip() or emoji})
    return out


def scope(cfg: Config) -> str:
    s = str(cfg.extra.get("rank_scope") or "grade")
    return s if s in SCOPES else "grade"


def pool_key(cls, sc: str) -> str:
    from .ops import class_key
    if sc == "all":
        return "all"
    if sc == "grade" and cls["grade"]:
        return "g" + hashlib.sha256(f"grade:{cls['grade']}".encode()).hexdigest()[:11]
    return class_key(cls["id"])


def pools(conn: sqlite3.Connection, cfg: Config) -> dict[str, str]:
    """class key -> pool key, for the Worker (reaction totals) and the rank."""
    from .ops import class_key
    sc = scope(cfg)
    return {class_key(c["id"]): pool_key(c, sc) for c in conn.execute("SELECT * FROM classes")}


def pool_classes(conn: sqlite3.Connection, cfg: Config) -> dict[str, list[int]]:
    sc = scope(cfg)
    out: dict[str, list[int]] = {}
    for c in conn.execute("SELECT * FROM classes ORDER BY id"):
        out.setdefault(pool_key(c, sc), []).append(c["id"])
    return out


def apply_pull(conn: sqlite3.Connection, items: list[dict]) -> int:
    from .pull import _token_map
    tokens = _token_map(conn)
    n = 0
    for it in items:
        sid = tokens.get(it.get("token", ""))
        v, r, ts = str(it.get("v", "")), str(it.get("r", "")), str(it.get("ts", ""))
        if sid is None or not v or not ts:
            continue
        if r == "":
            n += conn.execute("DELETE FROM video_reactions WHERE student_id = ? AND video_id = ?", (sid, v)).rowcount
        elif r in KEYS:
            day = M.utc_iso_to_kst(ts).date().isoformat()
            n += conn.execute("""INSERT INTO video_reactions (student_id, video_id, r, day, ts) VALUES (?, ?, ?, ?, ?)
                                 ON CONFLICT (student_id, video_id) DO UPDATE SET r = excluded.r, day = excluded.day, ts = excluded.ts
                                 WHERE excluded.ts >= video_reactions.ts""", (sid, v, r, day, ts)).rowcount
    return n


def reaction_counts(conn: sqlite3.Connection, class_ids: list[int], start: date, end: date) -> dict[str, dict]:
    """video -> {fire, good, curious, hard, try} for reactions of these classes' active students made in [start, end]."""
    if not class_ids:
        return {}
    q = f"""SELECT x.video_id, x.r, COUNT(*) AS n FROM video_reactions x JOIN students_public s ON s.id = x.student_id
            WHERE s.class_id IN ({','.join('?' * len(class_ids))}) AND s.active = 1 AND x.day BETWEEN ? AND ? GROUP BY x.video_id, x.r"""
    out: dict[str, dict] = {}
    for r in conn.execute(q, (*class_ids, start.isoformat(), end.isoformat())):
        out.setdefault(r["video_id"], {k: 0 for k in KEYS})[r["r"]] = r["n"]
    return out


def watchers(conn: sqlite3.Connection, class_ids: list[int], start: date, end: date) -> dict[str, int]:
    if not class_ids:
        return {}
    q = f"""SELECT w.video_id, COUNT(DISTINCT w.student_id) AS n FROM watches w JOIN students_public s ON s.id = w.student_id
            WHERE s.class_id IN ({','.join('?' * len(class_ids))}) AND s.active = 1 AND w.sec >= ? AND w.day BETWEEN ? AND ?
            GROUP BY w.video_id"""
    return {r["video_id"]: r["n"] for r in conn.execute(q, (*class_ids, MIN_SEC, start.isoformat(), end.isoformat()))}


def score_pool(conn: sqlite3.Connection, class_ids: list[int], start: date, end: date, known: set[str]) -> list[dict]:
    w = watchers(conn, class_ids, start, end)
    rx = reaction_counts(conn, class_ids, start, end)
    rows = []
    for vid in (set(w) | set(rx)) & known:
        sc = w.get(vid, 0) + sum(WEIGHTS[k] * n for k, n in rx.get(vid, {}).items())
        if sc > 0:
            rows.append({"video_id": vid, "score": sc, "watchers": w.get(vid, 0)})
    rows.sort(key=lambda r: (-r["score"], -r["watchers"], r["video_id"]))
    return rows[:TOP_N]


def update_ranks(conn: sqlite3.Connection, cfg: Config, today: date, videos: list[dict]) -> None:
    """At publish: recompute the running period's lists; a finished period keeps what it had (frozen)."""
    from .reviews import period
    start, end = period(today)
    known = {v["id"] for v in videos}
    for pool, cids in pool_classes(conn, cfg).items():
        conn.execute("DELETE FROM video_ranks WHERE period_from = ? AND pool = ?", (start.isoformat(), pool))
        for i, r in enumerate(score_pool(conn, cids, start, end, known), 1):
            conn.execute("INSERT INTO video_ranks (period_from, pool, rank, video_id, score, watchers) VALUES (?, ?, ?, ?, ?, ?)",
                         (start.isoformat(), pool, i, r["video_id"], r["score"], r["watchers"]))
        prev = start - timedelta(days=14)            # the period just over: frozen once written (never recomputed)
        if not conn.execute("SELECT 1 FROM video_ranks WHERE period_from = ? AND pool = ?", (prev.isoformat(), pool)).fetchone():
            for i, r in enumerate(score_pool(conn, cids, prev, start - timedelta(days=1), known), 1):
                conn.execute("INSERT INTO video_ranks (period_from, pool, rank, video_id, score, watchers) VALUES (?, ?, ?, ?, ?, ?)",
                             (prev.isoformat(), pool, i, r["video_id"], r["score"], r["watchers"]))


def ranks_for(conn: sqlite3.Connection, cfg: Config, cls, today: date, visible: set[str]) -> dict | None:
    """The list a student of `cls` sees: this period's, or the last frozen one while this one has nothing yet.
    Only videos this student can see are listed (F10 tags)."""
    from .reviews import period, period_label
    pool = pool_key(cls, scope(cfg))
    start, end = period(today)
    for s0, e0 in ((start, end), (start - timedelta(days=14), start - timedelta(days=1))):
        rows = [dict(r) for r in conn.execute("SELECT * FROM video_ranks WHERE period_from = ? AND pool = ? ORDER BY rank",
                                              (s0.isoformat(), pool)) if r["video_id"] in visible]
        if rows:
            return {"label": period_label(s0, e0), "rows": rows, "current": s0 == start}
    return None


def top_watchers(conn: sqlite3.Connection, class_id: int, start: date, end: date, n: int = 3) -> list[str]:
    """"열심히 본 친구" (korea mode only): display names of the class's top watchers by different videos (>= 10 s) in the
    period; only students who watched at least one; no counts, never a bottom list."""
    rows = conn.execute("""SELECT s.display_name, COUNT(DISTINCT w.video_id) AS n, MIN(w.ts) AS first FROM watches w
                           JOIN students_public s ON s.id = w.student_id
                           WHERE s.class_id = ? AND s.active = 1 AND w.sec >= ? AND w.day BETWEEN ? AND ?
                           GROUP BY s.id HAVING n > 0 ORDER BY n DESC, first LIMIT ?""",
                        (class_id, MIN_SEC, start.isoformat(), end.isoformat(), n)).fetchall()
    return [r["display_name"] for r in rows]
