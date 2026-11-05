"""수학 용어 찾기: the grid generator."""
import pytest

from console import wordsearch as WS


def read(grid, cells):
    return "".join(grid[r][c] for r, c in cells)


@pytest.mark.parametrize("name", list(WS.load_terms()["rounds"]))
@pytest.mark.parametrize("seed", range(12))
def test_every_word_is_placed_once_and_reads_back(name, seed):
    g = WS.round_grid(name, seed)
    words = WS.load_terms()["rounds"][name]
    assert len(g["grid"]) == WS.SIZE and all(len(row) == WS.SIZE for row in g["grid"])
    assert [p["w"] for p in g["words"]] == words
    grid = [list(row) for row in g["grid"]]
    for p in g["words"]:
        assert read(grid, p["cells"]) == p["w"]                                   # no crossing broke a word
        steps = {(b[0] - a[0], b[1] - a[1]) for a, b in zip(p["cells"], p["cells"][1:])}
        assert len(steps) == 1 and steps.pop() in WS.DIRS                          # a straight line: across, down, diagonal
        assert WS.count(grid, p["w"]) == 1                                         # no accidental second copy


def test_deterministic_with_a_seed_and_different_across_seeds():
    a, b, c = (WS.round_grid("함수", s) for s in (7, 7, 8))
    assert a == b and a["grid"] != c["grid"]


def test_crossings_share_a_syllable_and_bad_lists_are_refused():
    g = WS.generate(["합성함수", "역함수", "치역", "정의역"], seed=3)
    cells = {}
    for p in g["words"]:
        for (r, c), ch in zip(p["cells"], p["w"]):
            assert cells.setdefault((r, c), ch) == ch
    for bad in ([], ["역"], ["가나다라마바사아자"], ["명제", "명제"], ["함수", "역함수"]):
        with pytest.raises(WS.WordSearchError):
            WS.generate(bad, seed=1)


def test_open_and_close_award_the_class_bonus_once(conn, klass):
    import json as _json
    from console.config import Config
    cls, _ = klass
    sent = []

    def post(cfg, path, body):
        sent.append((path, body))
        return {"ok": True, "room": "4321", "ends": "2026-11-06T09:00:00Z"} if path == "/live/open" else {"ok": True}
    r = WS.open_room(conn, Config(), cls["name"], "함수", minutes=8, seed=5, post=post)
    assert r["room"] == "4321" and r["words"][0] == "합성함수"
    g = sent[0][1]["game"]
    assert g["minutes"] == 8 and g["grid"] == WS.round_grid("함수", 5)["grid"] and "name" not in _json.dumps(sent[0][1])
    state = {"open": True, "n": 8, "found": [{"i": i} for i in range(8)]}
    fetch = lambda path: _json.dumps(state).encode()                                  # noqa: E731
    assert WS.finish(conn, Config(), "4321", fetch=fetch, post=post)["bonus"] == 5
    assert WS.finish(conn, Config(), "4321", fetch=fetch, post=post)["bonus"] == 0    # once per room and day
    state["found"] = state["found"][:5]
    WS.open_room(conn, Config(), cls["name"], "함수", seed=6, post=lambda c, p, b: {"ok": True, "room": "1111"})
    assert WS.finish(conn, Config(), "1111", fetch=fetch, post=post) == {"found": 5, "n": 8, "bonus": 0}
    assert conn.execute("SELECT COUNT(*) FROM class_bonus WHERE source = 'game'").fetchone()[0] == 1
    with pytest.raises(WS.WordSearchError):
        WS.finish(conn, Config(), "9999", fetch=fetch, post=post)
