"""F22.3: varied sentence endings — hand-written variants, a deterministic pick per (token, date, key), no two neighbours
with the same ending on a screen, warm-formal only for parents, and every variant passes the word filter and the lint."""
import json
import re
from datetime import date, timedelta

from console import comments, copy as CP, generate, wordfilter
from console.config import ROOT, Config
from console.reviews import words_text

from conftest import TODAY

S = json.loads((ROOT / "strings.ko.json").read_text(encoding="utf-8"))
V = CP.variants()
TOKS = [f"{i:026d}".replace("0", "a")[:20] + "bcdefg" for i in range(40)]


def test_every_key_has_three_hand_written_variants():
    for aud, pool in V.items():
        assert len(pool) >= (40 if aud == "student" else 5)
        for k, vs in pool.items():
            assert len(vs) >= 3 and vs[0] == S[k] and len(set(vs)) == len(vs), k
            assert len({CP.family(v) for v in vs}) >= 2, k                      # a real mix of endings
            assert all(sorted(re.findall(r"\{\w+\}", v)) == sorted(re.findall(r"\{\w+\}", vs[0])) for v in vs), k
    listed = [k for keys in CP.SCREENS.values() for k in keys]
    assert sorted(listed) == sorted(list(V["student"]) + list(V["parent"])) and len(listed) == len(set(listed))


def test_same_student_and_day_same_text_another_day_can_differ():
    a = CP.picks(TOKS[0], "2026-11-06")
    assert a == CP.picks(TOKS[0], "2026-11-06")
    days = [(TODAY + timedelta(days=i)).isoformat() for i in range(14)]
    for k in V["student"]:
        assert len({CP.picks(TOKS[0], d)[k] for d in days} | {CP.picks(t, days[0])[k] for t in TOKS}) >= 2, k


def test_no_two_neighbours_on_a_screen_share_an_ending():
    for t in TOKS:
        for i in range(10):
            p = CP.picks(t, (TODAY + timedelta(days=i)).isoformat())
            for screen in ("today", "review", "tour", "rules", "settings", "me"):
                fams = [CP.family(p[k]) for k in CP.SCREENS[screen]]
                assert all(a != b for a, b in zip(fams, fams[1:])), (screen, fams)


def test_parents_get_warm_formal_endings_only():
    for k, vs in V["parent"].items():
        for v in vs:
            assert CP.family(v) in CP.PARENT_OK, v
            assert not re.search(r"[유쥬](?=[\s.!?,)]|$)", v), v
    for t in TOKS:
        assert all(not re.search(r"[유쥬](?=[\s.!?,)]|$)", x) for x in CP.picks(t, "2026-11-06", "parent").values())


def test_every_variant_passes_the_word_filter_and_the_lint(conn, klass):
    lst = wordfilter.parse_list(words_text())
    for pool in V.values():
        for vs in pool.values():
            for v in vs:
                assert wordfilter.check(v, lst) == (True, ""), v
                assert comments.lint(conn, Config(), v, None, "korea") == [], v


def test_pages_carry_the_days_variants(conn, klass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, studs = klass
    conn.execute("UPDATE students SET consent = 'yes'")
    conn.commit()
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY).ok
    for s in studs:
        html = (tmp_path / "d" / "s" / s["student_token"] / "index.html").read_text(encoding="utf-8")
        mine = CP.picks(s["student_token"], TODAY.isoformat())
        data = json.loads(html.split('id="data">')[1].split("</script>")[0])
        assert data["S"]["wt_todo"] == mine["wt_todo"] and [x["text"] for x in data["tour"]["steps"]][0] == mine["tour_s1"]
        par = (tmp_path / "d" / "p" / s["parent_token"] / "index.html").read_text(encoding="utf-8")
        tour = par.split('id="tour-data">')[1].split("</script>")[0]
        assert CP.picks(s["parent_token"], TODAY.isoformat(), "parent")["tour_p1"] in json.loads(tour)["steps"][0]["text"]
        assert not re.search(r"[유쥬](?=[\s.!?,)]|$)", tour)
