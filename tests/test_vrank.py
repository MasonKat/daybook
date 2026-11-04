"""F19.2 영상 반응 (5 options, pooled totals, 어려워요 teacher-only) and F19.3 the 2-week video rank per grade."""
import json
from datetime import date, timedelta

from console import generate, practice, pull as pullmod, vrank, watch as W
from console.config import Config, strings
from console.importers import add_student
from console.ops import class_key
from console.reviews import period

from conftest import TODAY
from test_watch import cfg_with, put, vid

S = strings()


def rx(conn, sid, v, r, day=TODAY):
    conn.execute("INSERT OR REPLACE INTO video_reactions (student_id, video_id, r, day, ts) VALUES (?, ?, ?, ?, ?)",
                 (sid, v, r, day.isoformat(), f"{day.isoformat()}T10:00:00.000Z"))


def second_class(conn, grade="고1"):
    conn.execute("UPDATE classes SET grade = ?", (grade,))
    conn.execute("INSERT INTO classes (name, term, weekday_mask, reward_label, grade) VALUES ('고1B', '2026-10-01..2026-12-31', 9, 'x', ?)", (grade,))
    cid = conn.execute("SELECT id FROM classes WHERE name = '고1B'").fetchone()[0]
    s = add_student(conn, cid, "임보라", ext_id="S911")
    conn.execute("UPDATE students SET consent = 'yes'")
    conn.commit()
    return cid, conn.execute("SELECT * FROM students WHERE id = ?", (s["id"],)).fetchone()


def test_labels_default_and_override():
    assert [(l["key"], l["emoji"], l["text"]) for l in vrank.labels(Config(), S)] == [
        ("fire", "🔥", "최고예요"), ("good", "👍", "좋아요"), ("curious", "🤔", "궁금해요"), ("hard", "😵", "어려워요"), ("try", "💪", "도전해볼래요")]
    cfg = Config(extra={"video_reactions": {"good": "😀 굿"}})
    assert vrank.labels(cfg, S)[1] == {"key": "good", "emoji": "😀", "text": "굿"}


def test_pools_follow_rank_scope(conn, klass):
    cls, _ = klass
    cid2, _ = second_class(conn)
    k1, k2 = class_key(cls["id"]), class_key(cid2)
    g = vrank.pools(conn, Config())
    assert g[k1] == g[k2] and g[k1].startswith("g")                               # default: one pool per grade
    c = vrank.pools(conn, Config(extra={"rank_scope": "class"}))
    assert c == {k1: k1, k2: k2}
    assert set(vrank.pools(conn, Config(extra={"rank_scope": "all"})).values()) == {"all"}
    conn.execute("UPDATE classes SET grade = NULL WHERE id = ?", (cid2,))
    assert vrank.pools(conn, Config())[k2] == k2                                  # no grade: its own pool


def test_push_tokmap_sends_the_pools(conn, klass, monkeypatch):
    sent = {}
    monkeypatch.setattr(pullmod, "http_put_json", lambda cfg, path, payload: sent.setdefault(path, payload) or {"n": 3})
    pullmod.push_tokmap(conn, Config(worker_url="https://w", teacher_token="t"))
    assert sent["/tokmap"]["pools"] == vrank.pools(conn, Config())


def test_pull_keeps_the_latest_reaction_and_removals(conn, klass):
    _, studs = klass
    t0, t1 = studs[0]["student_token"], studs[1]["student_token"]
    items = [{"token": t0, "v": "AMPvideo001", "r": "fire", "ts": "2026-11-05T10:00:00.000Z"},
             {"token": t0, "v": "AMPvideo001", "r": "hard", "ts": "2026-11-05T11:00:00.000Z"},
             {"token": t0, "v": "AMPvideo001", "r": "good", "ts": "2026-11-05T09:00:00.000Z"},    # older: ignored
             {"token": t1, "v": "AMPvideo001", "r": "try", "ts": "2026-11-05T16:00:00.000Z"},     # 01:00 KST next day
             {"token": "x" * 26, "v": "AMPvideo001", "r": "fire", "ts": "2026-11-05T10:00:00.000Z"},
             {"token": t1, "v": "AMPvideo002", "r": "bad", "ts": "2026-11-05T10:00:00.000Z"}]
    vrank.apply_pull(conn, items)
    rows = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute("SELECT student_id, video_id, r, day FROM video_reactions")}
    assert rows == {(studs[0]["id"], "AMPvideo001"): ("hard", "2026-11-05"), (studs[1]["id"], "AMPvideo001"): ("try", "2026-11-06")}
    vrank.apply_pull(conn, [{"token": t0, "v": "AMPvideo001", "r": "", "ts": "2026-11-05T12:00:00.000Z"}])
    assert conn.execute("SELECT COUNT(*) FROM video_reactions").fetchone()[0] == 1


def test_teacher_view_has_hard_counts_pages_never_do(conn, klass, tmp_path):
    _, studs = klass
    for s in studs:
        rx(conn, s["id"], "AMPvideo001", "hard")
    conn.commit()
    cfg = cfg_with(tmp_path, [vid(1), vid(2, "long")])
    view = W.teacher_view(conn, generate.load_videos(cfg), TODAY, 7)
    assert view["classes"][0]["videos"][0]["rx"]["hard"] == 3                     # the Teacher tab knows 어려워요
    assert generate.publish(conn, cfg, tmp_path / "d", today=TODAY).ok
    html = (tmp_path / "d" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert html.count('class="vrx') == 2 and html.count('class="vrb"') == 10 and "어려워요" in html   # the buttons
    assert '"hard"' not in html.split('id="data">')[1]                            # no reaction data baked into the page


def test_rank_score_frozen_period_and_visibility(conn, klass, tmp_path):
    cls, studs = klass
    cid2, other = second_class(conn)
    cls = conn.execute("SELECT * FROM classes WHERE id = ?", (cls["id"],)).fetchone()     # now with its grade
    start, end = period(TODAY)
    for s in studs:
        put(conn, s["id"], "AMPvideo001", TODAY)                                 # 3 watchers
    put(conn, other["id"], "AMPvideo002", TODAY)                                 # 1 watcher in 고1B (same grade pool)
    put(conn, studs[0]["id"], "AMPvideo003", TODAY, sec=5)                       # < 10 s: not a watcher
    rx(conn, other["id"], "AMPvideo002", "fire"); rx(conn, studs[0]["id"], "AMPvideo002", "good")   # 1 + 3 + 2 = 6
    rx(conn, studs[1]["id"], "AMPvideo001", "hard")                              # 어려워요 weighs 0
    conn.commit()
    videos = generate.load_videos(cfg_with(tmp_path, [vid(1), vid(2), vid(3)]))
    vrank.update_ranks(conn, Config(), TODAY, videos)
    pool = vrank.pool_key(cls, "grade")
    got = [(r[0], r[1]) for r in conn.execute("SELECT video_id, score FROM video_ranks WHERE period_from = ? AND pool = ? ORDER BY rank",
                                              (start.isoformat(), pool))]
    assert got == [("AMPvideo002", 6), ("AMPvideo001", 3)]
    r = vrank.ranks_for(conn, Config(), cls, TODAY, {"AMPvideo001"})            # only what this student can see
    assert [x["video_id"] for x in r["rows"]] == ["AMPvideo001"] and r["current"] and r["label"] == f"{start.month}/{start.day}–{end.month}/{end.day}"
    # the next period: the finished one is written once and then frozen, even if late data arrives
    nxt = end + timedelta(days=1)
    vrank.update_ranks(conn, Config(), nxt, videos)
    put(conn, studs[0]["id"], "AMPvideo003", TODAY, sec=300)
    vrank.update_ranks(conn, Config(), nxt + timedelta(days=1), videos)
    assert [x[0] for x in conn.execute("SELECT video_id FROM video_ranks WHERE period_from = ? AND pool = ? ORDER BY rank",
                                       (start.isoformat(), pool))] == ["AMPvideo002", "AMPvideo001"]
    r2 = vrank.ranks_for(conn, Config(), cls, nxt + timedelta(days=1), {"AMPvideo001", "AMPvideo002"})
    assert not r2["current"] and r2["rows"][0]["video_id"] == "AMPvideo002"    # nothing yet this period: the last one


def test_page_ticker_list_and_friends_korea_only(conn, klass, tmp_path):
    cls, studs = klass
    for i, s in enumerate(studs):
        for k in range(i + 1):
            put(conn, s["id"], f"AMPvideo00{k + 1}", TODAY)
    conn.commit()
    cfg = cfg_with(tmp_path, [vid(1), vid(2), vid(3, "long")])
    res = generate.publish(conn, cfg, tmp_path / "d", today=TODAY)
    assert res.ok and not [f for f in res.findings if f.kind == "other_name"]    # the marked korea-mode list only
    html = (tmp_path / "d" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    head = html.split('<header class="sbar">')[1].split("</header>")[0]
    assert 'id="rk-open"' in head and "TOP 5 · " in head and "🥇 1위 · 영상1" in head and "today-date" not in head
    top = json.loads(head.split('data-top="')[1].split('"')[0].replace("&#34;", '"').replace("&quot;", '"'))
    assert top == ["🥇 1위 · 영상1", "🥈 2위 · 영상2", "🥉 3위 · 영상3"]                     # fewer than 5: only those
    lst = html.split('id="rk"')[1].split('id="rv"')[0]
    assert lst.count("<li>") == 3 and "명" not in lst.split("rk-friends")[0]    # videos only, no counts
    names = lst.split('class="rk-friends"')[1]
    assert [n for n in (s["display_name"] for s in reversed(studs)) if n in names] == [s["display_name"] for s in reversed(studs)]
    practice.set_mode(conn, "manitoba")
    assert generate.publish(conn, cfg, tmp_path / "m", today=TODAY).ok
    html = (tmp_path / "m" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    assert 'id="rk-open"' in html and "rk-friends" not in html                   # manitoba: no names
    assert all(s["display_name"] not in html.split('id="rk"')[1].split('id="rv"')[0] for s in studs[1:])


def test_top_watchers_positive_only(conn, klass):
    cls, studs = klass
    put(conn, studs[2]["id"], "AMPvideo001", TODAY); put(conn, studs[2]["id"], "AMPvideo002", TODAY)
    put(conn, studs[0]["id"], "AMPvideo001", TODAY)
    put(conn, studs[1]["id"], "AMPvideo001", TODAY, sec=3)                       # too short: not counted, not listed
    start, end = period(TODAY)
    assert vrank.top_watchers(conn, cls["id"], start, end) == [studs[2]["display_name"], studs[0]["display_name"]]
