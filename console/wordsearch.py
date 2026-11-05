"""수학 용어 찾기: a cooperative break-time word search (games-v1).

The laptop builds the grid (a fixed seed gives the same grid) and opens it as an AMP Live room in game mode: phones get the
grid and the word list, the room (Durable Object) checks every find against the placements, and every phone sees
"찾은 단어 n/N" and the time left. No names, no ranking; a finished board earns the class bonus.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .config import ROOT

TERMS = ROOT / "games" / "wordsearch_terms.json"
DIRS = ((0, 1), (1, 0), (1, 1), (-1, 1))          # across, down, diagonal down-right, diagonal up-right
SIZE = 8


class WordSearchError(ValueError):
    pass


def load_terms(path: Path | None = None) -> dict:
    return json.loads(Path(path or TERMS).read_text(encoding="utf-8"))


def _lines(grid: list[list[str]]):
    n = len(grid)
    for dr, dc in DIRS:
        for r in range(n):
            for c in range(n):
                if 0 <= r - dr < n and 0 <= c - dc < n:
                    continue                         # not a line start
                line, rr, cc = [], r, c
                while 0 <= rr < n and 0 <= cc < n:
                    line.append(grid[rr][cc]); rr += dr; cc += dc
                yield "".join(line)


def count(grid: list[list[str]], word: str) -> int:
    """How many times a word can be read along the four directions."""
    return sum(line.count(word) for line in _lines(grid)) if len(word) > 1 else 0


def generate(words: list[str], seed: int, size: int = SIZE, pool: str = "", tries: int = 200) -> dict:
    """{"grid": [rows], "words": [{"w", "cells": [[r, c], …]}], "seed"}: every word placed (crossings only on the same
    syllable), filled from `pool`, each word readable exactly once."""
    words = [w.strip() for w in words if w.strip()]
    if not words or len(words) > 10 or len(set(words)) != len(words):
        raise WordSearchError("1-10 different words")
    if any(len(w) < 2 or len(w) > size for w in words):
        raise WordSearchError(f"every word needs 2-{size} syllables")
    inner = [(a, b) for a in words for b in words if a != b and a in b]
    if inner:
        raise WordSearchError(f"{inner[0][0]!r} is inside {inner[0][1]!r}: it could never be found just once")
    pool = pool or "".join(sorted(set("".join(words))))
    rng = random.Random(seed)
    for _ in range(tries):
        grid = [[""] * size for _ in range(size)]
        placed = []
        ok = True
        for w in sorted(words, key=len, reverse=True):
            spots = [(r, c, d) for r in range(size) for c in range(size) for d in DIRS]
            rng.shuffle(spots)
            for r, c, (dr, dc) in spots:
                cells = [(r + i * dr, c + i * dc) for i in range(len(w))]
                if all(0 <= rr < size and 0 <= cc < size and grid[rr][cc] in ("", w[i]) for i, (rr, cc) in enumerate(cells)):
                    for i, (rr, cc) in enumerate(cells):
                        grid[rr][cc] = w[i]
                    placed.append({"w": w, "cells": [list(x) for x in cells]})
                    break
            else:
                ok = False
                break
        if not ok:
            continue
        for _fill in range(20):
            g = [[ch or rng.choice(pool) for ch in row] for row in grid]
            if all(count(g, w) == 1 for w in words):
                order = {w: i for i, w in enumerate(words)}
                return {"grid": ["".join(row) for row in g], "words": sorted(placed, key=lambda p: order[p["w"]]), "seed": seed}
    raise WordSearchError("could not place every word: fewer or shorter words")


def round_grid(name: str, seed: int, path: Path | None = None) -> dict:
    t = load_terms(path)
    if name not in t["rounds"]:
        raise WordSearchError(f"no round {name!r} (rounds: {', '.join(t['rounds'])})")
    return generate(t["rounds"][name], seed, pool=t.get("distractors", ""))


# ---------------------------------------------------------------- the room (via the Worker) and the class bonus
def open_room(conn, cfg, class_name: str, round_name: str, minutes: int = 8, seed: int | None = None, post=None) -> dict:
    """Builds the grid on the laptop and opens it as a Live room in game mode. Returns {room, ends, words, seed}."""
    from . import ops
    from .db import log_event
    from .pull import PullError, http_post_json
    if not 3 <= minutes <= 15:
        raise WordSearchError("minutes: 3-15")
    cls = ops.get_class(conn, class_name)
    seed = random.randrange(1, 10**6) if seed is None else seed
    g = round_grid(round_name, seed)
    try:
        r = (post or http_post_json)(cfg, "/live/open", {"class": ops.class_key(cls["id"]),
                                                         "game": {"grid": g["grid"], "words": g["words"], "minutes": minutes}})
    except PullError as e:
        raise WordSearchError(str(e)) from e
    if not r.get("ok"):
        raise WordSearchError(f"the Worker refused the game: {r.get('error')}")
    log_event(conn, "game_open", {"room": r["room"], "class_id": cls["id"], "round": round_name, "seed": seed, "n": len(g["words"])})
    conn.commit()
    return {"room": r["room"], "ends": r.get("ends"), "words": [p["w"] for p in g["words"]], "seed": seed}


def finish(conn, cfg, room: str, fetch=None, post=None, today=None) -> dict:
    """Reads 찾은 단어 n/N, closes the room; a finished board (all found before the timer) gets the class bonus once."""
    from datetime import date as _date
    from .db import log_event, now_utc_iso
    from .pull import http_fetch, http_post_json
    ev = None
    for r in conn.execute("SELECT payload_json FROM events WHERE type = 'game_open' ORDER BY id DESC"):
        p = json.loads(r[0])
        if p.get("room") == room:
            ev = p
            break
    if not ev:
        raise WordSearchError(f"no game room {room} opened from this laptop")
    st = json.loads((fetch or http_fetch(cfg))(f"/live/state?room={room}"))
    found, n = len(st.get("found") or []), int(st.get("n") or ev["n"])
    (post or http_post_json)(cfg, "/live/close", {"room": room})
    bonus = 0
    if st.get("open") and found == n:
        day = (today or _date.today()).isoformat()
        bonus = int(cfg.extra.get("game_bonus", cfg.extra.get("live_bonus", 5)))
        ref = f"game:{day}:{room}"
        cur = conn.execute("INSERT OR IGNORE INTO class_bonus (class_id, session_id, points, source, ref, ts) VALUES (?, NULL, ?, 'game', ?, ?)",
                           (ev["class_id"], bonus, ref, now_utc_iso()))
        if cur.rowcount:
            log_event(conn, "class_bonus", {"class_id": ev["class_id"], "points": bonus, "ref": ref})
        else:
            bonus = 0
    log_event(conn, "game_close", {"room": room, "found": found, "n": n, "bonus": bonus})
    conn.commit()
    return {"found": found, "n": n, "bonus": bonus}
