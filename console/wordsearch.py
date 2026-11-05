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


def _lines_unused(grid: list[list[str]]):
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
    return len(occurrences(grid, word))


def occurrences(grid: list[list[str]], word: str) -> list[frozenset]:
    """Every reading of `word` (across, down, the two diagonals), as its set of cells."""
    n, out = len(grid), []
    if len(word) < 2:
        return out
    for dr, dc in DIRS:
        for r in range(n):
            for c in range(n):
                cells = [(r + i * dr, c + i * dc) for i in range(len(word))]
                if all(0 <= rr < n and 0 <= cc < n and grid[rr][cc] == word[i] for i, (rr, cc) in enumerate(cells)):
                    out.append(frozenset(cells))
    return out


def free_count(grid, word: str, placed: list[dict]) -> int:
    """Readings of `word` that do not lie inside a longer placed word (정적분 inside 부정적분 does not count)."""
    hosts = [frozenset(map(tuple, p["cells"])) for p in placed if len(p["w"]) > len(word) and word in p["w"]]
    return sum(1 for o in occurrences(grid, word) if not any(o <= h for h in hosts))


def _norm(entries) -> tuple[list[str], dict]:
    """A round is a list of terms, each a string or {"w": term, "m": one-line meaning}."""
    words, means = [], {}
    for e in entries:
        w = (e.get("w") if isinstance(e, dict) else e) or ""
        w = str(w).strip()
        if w:
            words.append(w)
            if isinstance(e, dict) and e.get("m"):
                means[w] = str(e["m"]).strip()[:60]
    return words, means


def generate(words: list, seed: int, size: int | None = None, pool: str = "", tries: int = 300) -> dict:
    """{"grid": [rows], "words": [{"w", "cells", "m"?}], "seed", "size"}: every word placed (crossings only on the same
    syllable), filled from `pool`; each word readable exactly once outside the longer words that contain it."""
    words, means = _norm(words)
    if not words or len(words) > 16 or len(set(words)) != len(words):
        raise WordSearchError("1-16 different words")
    size = size or (SIZE if len(words) <= 10 and max(map(len, words)) <= SIZE else 10)
    if any(len(w) < 2 or len(w) > size for w in words):
        raise WordSearchError(f"every word needs 2-{size} syllables")
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
                    if any(set(cells) <= set(map(tuple, p["cells"])) for p in placed):
                        continue                         # never hidden entirely inside another word's letters
                    for i, (rr, cc) in enumerate(cells):
                        grid[rr][cc] = w[i]
                    placed.append({"w": w, "cells": [list(x) for x in cells], **({"m": means[w]} if w in means else {})})
                    break
            else:
                ok = False
                break
        if not ok:
            continue
        for _fill in range(20):
            g = [[ch or rng.choice(pool) for ch in row] for row in grid]
            if all(free_count(g, w, placed) == 1 for w in words):
                order = {w: i for i, w in enumerate(words)}
                return {"grid": ["".join(row) for row in g], "words": sorted(placed, key=lambda p: order[p["w"]]), "seed": seed,
                        "size": size}
    raise WordSearchError("could not place every word: fewer or shorter words")


def round_grid(name: str, seed: int, path: Path | None = None) -> dict:
    t = load_terms(path)
    if name not in t["rounds"]:
        raise WordSearchError(f"no round {name!r} (rounds: {', '.join(t['rounds'])})")
    r = t["rounds"][name]
    if isinstance(r, dict):
        return generate(r.get("words") or [], seed, size=r.get("size"), pool=r.get("distractors") or t.get("distractors", ""))
    return generate(r, seed, pool=t.get("distractors", ""))


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
