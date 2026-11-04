"""F18.3 학생 리뷰 (console side): pull, 승인 / 숨김, the random vote moment, winners (ties keep all, fewer than 3 = no
vote), what is synced to the Worker, what pages show, and the phone filter = the Worker filter."""
import json
import random
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest

from console import generate, practice, reviews as RV
from console.config import Config, ROOT
from console.db import KST
from console.ops import class_key

from conftest import TODAY

NODE = shutil.which("node")


def _review(conn, sid, cid, n, status="approved", day=None, hide=0):
    rid = f"{n:012x}"
    conn.execute("INSERT INTO reviews (id, student_id, class_id, day, text, hide_name, status, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (rid, sid, cid, (day or TODAY).isoformat(), f"리뷰 {n}번 웃김", hide, status, f"2026-11-0{1 + n % 8}T10:00:00.000Z"))
    return rid


def _round(conn, cid, close="2026-11-01T00:00:00.000Z"):
    start, end = RV.period(TODAY)
    conn.execute("INSERT INTO review_rounds (id, class_id, period_from, period_to, open_at, close_at) VALUES (?, ?, ?, ?, ?, ?)",
                 ("r", cid, start.isoformat(), end.isoformat(), "2026-10-31T00:00:00.000Z", close))
    return "r"


def _vote(conn, sid, rid):
    conn.execute("INSERT INTO review_votes (round_id, student_id, review_id, ts) VALUES ('r', ?, ?, 't')", (sid, rid))


def test_period_is_two_weeks_from_a_monday():
    s, e = RV.period(TODAY)
    assert s.weekday() == 0 and (e - s).days == 13 and s <= TODAY <= e
    assert RV.period(e + timedelta(days=1))[0] == e + timedelta(days=1)
    assert RV.period_label(date(2026, 11, 2), date(2026, 11, 15)) == "11/2–11/15"


def test_pull_stores_pending_and_votes_by_token(conn, klass):
    cls, studs = klass
    body = {"reviews": [{"id": "aaaaaaaaaaaa", "token": studs[0]["student_token"], "day": "2026-11-06", "text": "판서 최고",
                         "hide": 1, "ts": "2026-11-06T10:00:00.000Z"},
                        {"id": "bbbbbbbbbbbb", "token": "x" * 26, "day": "2026-11-06", "text": "누구", "hide": 0, "ts": "t"}]}
    assert RV.apply_pull(conn, body) == (1, 0)
    r = conn.execute("SELECT * FROM reviews").fetchone()
    assert (r["status"], r["hide_name"], r["class_id"]) == ("pending", 1, cls["id"])
    assert RV.apply_pull(conn, body) == (0, 0)                                  # idempotent
    RV.decide(conn, "aaaaaaaaaaaa", "approved")
    RV.decide(conn, "aaaaaaaaaaaa", "hidden")
    kinds = [x[0] for x in conn.execute("SELECT type FROM events WHERE type LIKE 'review_%' ORDER BY id")]
    assert kinds == ["review_new", "review_approve", "review_hide"]            # every action is an event
    with pytest.raises(RV.ReviewError):
        RV.decide(conn, "nope", "approved")


def test_pending_is_never_synced_as_approved_nor_published(conn, klass, tmp_path):
    cls, studs = klass
    p = _review(conn, studs[0]["id"], cls["id"], 1, "pending")
    a = _review(conn, studs[1]["id"], cls["id"], 2, "approved")
    h = _review(conn, studs[2]["id"], cls["id"], 3, "hidden")
    pay = RV.sync_payload(conn, Config(teacher_token="t"))
    sent = {x["id"]: x["status"] for x in pay["reviews"]}
    assert p not in sent and sent == {a: "approved", h: "hidden"}
    assert all(len(x) == 16 for hs in pay["names"].values() for x in hs)        # hashes, never names
    assert not any(s["full_name"] in json.dumps(pay, ensure_ascii=False) for s in studs)
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY).ok
    site = "".join(f.read_text(encoding="utf-8") for f in (tmp_path / "d").rglob("*.html"))
    assert "리뷰 1번" not in site and "리뷰 2번" not in site                 # class reviews come from the Worker, not pages


def test_label_korea_display_name_unless_hidden_manitoba_always_anonymous(conn, klass):
    cls, studs = klass
    cfg = Config()
    shown = conn.execute("SELECT * FROM reviews WHERE id = ?", (_review(conn, studs[0]["id"], cls["id"], 1),)).fetchone()
    hid = conn.execute("SELECT * FROM reviews WHERE id = ?", (_review(conn, studs[1]["id"], cls["id"], 2, hide=1),)).fetchone()
    assert RV.label(conn, cfg, shown) == studs[0]["display_name"] and RV.label(conn, cfg, hid) == ""
    practice.set_mode(conn, "manitoba")
    assert RV.label(conn, cfg, shown) == ""
    assert all(x["label"] == "" for x in RV.sync_payload(conn, cfg)["reviews"])


def test_winner_ties_keep_all(conn, klass):
    cls, studs = klass
    r = [_review(conn, s["id"], cls["id"], i + 1) for i, s in enumerate(studs)]
    _round(conn, cls["id"])
    _vote(conn, studs[0]["id"], r[1]); _vote(conn, studs[1]["id"], r[0]); _vote(conn, studs[2]["id"], r[0])
    assert RV.close_due(conn) == ["r"]
    assert [w["id"] for w in RV.wins(conn)] == [r[0]]
    conn.execute("DELETE FROM review_wins"); conn.execute("UPDATE review_rounds SET status = 'planned'")
    conn.execute("DELETE FROM review_votes WHERE student_id = ?", (studs[2]["id"],))
    RV.close_due(conn)
    assert sorted(w["id"] for w in RV.wins(conn)) == sorted([r[0], r[1]])       # 1 : 1 — both win


def test_fewer_than_three_approved_means_no_vote(conn, klass):
    cls, studs = klass
    r = [_review(conn, s["id"], cls["id"], i + 1) for i, s in enumerate(studs[:2])]
    _review(conn, studs[2]["id"], cls["id"], 3, "pending")                    # pending does not count
    _round(conn, cls["id"])
    _vote(conn, studs[2]["id"], r[0])
    RV.close_due(conn)
    assert conn.execute("SELECT status FROM review_rounds").fetchone()[0] == "novote" and RV.wins(conn) == []
    rnd = conn.execute("SELECT * FROM review_rounds").fetchone()
    assert RV.close_due(conn) == [] and rnd["status"] == "novote"             # decided once


def test_open_round_is_not_closed_early(conn, klass):
    cls, studs = klass
    [_review(conn, s["id"], cls["id"], i + 1) for i, s in enumerate(studs)]
    _round(conn, cls["id"], close="2099-01-01T00:00:00.000Z")
    assert RV.close_due(conn) == []


def test_random_moment_is_a_class_day_between_15_and_2130_kst(conn, klass):
    cls, _ = klass
    d0 = date(2026, 11, 9)                       # a period start: 화 11/10, 금 11/13, 화 11/17, 금 11/20
    start, end = RV.period(d0)
    seen = set()
    for seed in range(60):
        conn.execute("DELETE FROM review_rounds")
        assert RV.plan(conn, d0, random.Random(seed)) == [f"c{cls['id']}-{start.isoformat()}"]
        r = conn.execute("SELECT * FROM review_rounds").fetchone()
        at = datetime.fromisoformat(r["open_at"].replace("Z", "+00:00")).astimezone(KST)
        assert at.date().weekday() in (1, 4) and start <= at.date() <= end           # 화, 금 (the class days)
        assert (15, 0) <= (at.hour, at.minute) <= (21, 30)                          # never in quiet hours (22-08)
        close = datetime.fromisoformat(r["close_at"].replace("Z", "+00:00"))
        assert close - at == timedelta(hours=24)
        seen.add(at.date())
    assert len(seen) == 4                                                           # any of the 4 class days
    assert RV.plan(conn, d0) == []                                                  # once per class per period


def test_open_now_replaces_the_planned_moment(conn, klass):
    cls, _ = klass
    RV.plan(conn, TODAY, random.Random(1))
    now = datetime(2026, 11, 6, 16, 0, tzinfo=KST)
    RV.open_now(conn, cls["id"], now)
    r = conn.execute("SELECT * FROM review_rounds").fetchone()
    assert r["open_at"] == "2026-11-06T07:00:00.000Z" and conn.execute("SELECT COUNT(*) FROM review_rounds").fetchone()[0] == 1


def test_sync_sends_open_rounds_and_winners(conn, klass):
    cls, studs = klass
    r = [_review(conn, s["id"], cls["id"], i + 1) for i, s in enumerate(studs)]
    _round(conn, cls["id"])
    _vote(conn, studs[1]["id"], r[0])
    RV.close_due(conn)
    RV.open_now(conn, cls["id"], datetime.now(KST))
    pay = RV.sync_payload(conn, Config())
    assert [x["class"] for x in pay["rounds"]] == [class_key(cls["id"])] and len(pay["rounds"]) == 1
    assert pay["best"][0]["review"] == r[0] and pay["best"][0]["label"] == studs[0]["display_name"]
    assert "시발" in pay["words"] and "!시발점" in pay["words"]


def test_parents_see_only_their_own_childs_win(conn, klass, tmp_path):
    cls, studs = klass
    r = [_review(conn, s["id"], cls["id"], i + 1, day=TODAY - timedelta(days=3)) for i, s in enumerate(studs)]
    _round(conn, cls["id"])
    _vote(conn, studs[1]["id"], r[0]); _vote(conn, studs[2]["id"], r[0])
    RV.close_due(conn)
    conn.commit()
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY).ok
    page = lambda s: (tmp_path / "d" / "p" / s["parent_token"] / "index.html").read_text(encoding="utf-8")   # noqa: E731
    assert "베스트 리뷰에 뽑혔어요" in page(studs[0]) and "리뷰 1번 웃김" in page(studs[0])
    for s in studs[1:]:
        assert "베스트 리뷰" not in page(s) and "리뷰 1번" not in page(s)
    conn.execute("UPDATE students SET consent = 'unknown' WHERE id = ?", (studs[0]["id"],))
    conn.commit()
    assert generate.publish(conn, Config(), tmp_path / "e", today=TODAY).ok
    assert not (tmp_path / "e" / "p" / studs[0]["parent_token"]).exists()       # no consent: no parent page at all
    assert RV.sync_payload(conn, Config())["best"][0]["review"] == r[0]          # classmates still see the win


def test_the_letter_names_the_review():
    from console.config import strings
    assert "학생이 쓴 수업 한 줄 리뷰 (선생님 확인 후 같은 반 친구들에게만 보여요)" in strings()["parent_msg"]["letter_records"]


@pytest.mark.skipif(not NODE, reason="node is needed for the parity check")
def test_published_filter_equals_the_worker_filter_and_hashes_match(conn, klass, tmp_path):
    cls, studs = klass
    js = generate.review_filter_js()
    assert "export " not in js and "window.DaybookFilter" in js and "kakao" not in js.lower()
    (tmp_path / "rf.js").write_text(js, encoding="utf-8")
    samples = ["시발 어려움", "ㅅㅂ", "ㅅ 1 ㅂ", "tlqkf", "f u c k", "010-1234-5678", "www.naver.com", "오늘 시발점 문제",
               "이번 시 발표 최고", "선생님 판서가 피카소급", "18문제 다 맞음", "김치녀", "병.신"]
    name = studs[0]["display_name"]
    script = f"""
      const fs = require('fs'), vm = require('vm'), crypto = require('crypto');
      const ctx = {{ window: {{}} }}; vm.createContext(ctx); vm.runInContext(fs.readFileSync({json.dumps(str(tmp_path / 'rf.js'))}, 'utf8'), ctx);
      const F = ctx.window.DaybookFilter;
      import({json.dumps((ROOT / 'worker' / 'src' / 'review_filter.js').as_uri())}).then((W) => {{
        const list = W.parseList(fs.readFileSync({json.dumps(str(ROOT / 'filters' / 'review_words.txt'))}, 'utf8'));
        const s = {json.dumps(samples, ensure_ascii=False)};
        const page = s.map((t) => F.check(t, F.list).ok), worker = s.map((t) => W.check(t, list).ok);
        const h = crypto.createHash('sha256').update({json.dumps(class_key(cls['id']))} + ':' + {json.dumps(name, ensure_ascii=False)}).digest('hex').slice(0, 16);
        console.log(JSON.stringify({{ page, worker, h, pieces: F.pieces(F.readings('{name}이 최고').syl) }}));
      }});
    """
    out = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert out["page"] == out["worker"]
    assert out["page"] == [False] * 7 + [True] * 4 + [False] * 2
    assert out["h"] in RV.page_name_hashes(conn, cls["id"]) and name in out["pieces"]   # the phone finds a classmate's name


def test_student_page_carries_hashes_not_names_and_the_header_pill(conn, klass, tmp_path):
    cls, studs = klass
    assert generate.publish(conn, Config(), tmp_path / "d", today=TODAY).ok
    html = (tmp_path / "d" / "s" / studs[0]["student_token"] / "index.html").read_text(encoding="utf-8")
    data = json.loads(html.split('id="data">')[1].split("</script>")[0])
    assert data["rv"]["key"] == class_key(cls["id"]) and len(data["rv"]["names"]) == 3 and data["rv"]["anon"] is False
    for s in studs[1:]:
        assert s["display_name"] not in html and s["full_name"] not in html
    head = html.split('<header class="sbar">')[1].split("</header>")[0]
    order = [head.index(x) for x in ('class="wordmark"', 'class="mslot"', 'id="rv-open"', 'id="today-date"', 'class="av ring sm"')]
    assert order == sorted(order) and 'class="mph"' in head                        # F18.4 order; the placeholder shape
    assert '<script src="/static/review_filter.js" defer>' in html and (tmp_path / "d" / "static" / "review_filter.js").exists()


def test_cli_review_list_approve_hide_open_vote(conn, klass, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from console.cli import cli
    cls, studs = klass
    rid = _review(conn, studs[0]["id"], cls["id"], 1, "pending")
    conn.commit()
    db = conn.execute("PRAGMA database_list").fetchone()["file"]
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(cli, ["--db", db, "review", "list"])
    assert r.exit_code == 0 and rid in r.output and "리뷰 1번" in r.output
    r = CliRunner().invoke(cli, ["--db", db, "review", "approve", rid])
    assert r.exit_code == 0 and conn.execute("SELECT status FROM reviews").fetchone()[0] == "approved"
    r = CliRunner().invoke(cli, ["--db", db, "review", "open-vote", "고1A"])
    assert r.exit_code == 0 and "fewer than 3" in r.output
