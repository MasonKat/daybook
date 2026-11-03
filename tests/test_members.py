"""F11: the teacher manages classes and members alone (Teacher tab), one test per action through the HTTP layer."""
import base64
import json
from datetime import datetime, time

from console import generate
from console.config import Config
from console.db import KST

from conftest import TODAY
from test_teacher import call, tserver  # noqa: F401  (the same local server fixture)


def test_add_student_shows_links_and_messages_and_dist_has_no_pii(tserver, conn, tmp_path):  # noqa: F811
    url, b, sent = tserver
    code, j = call(url + "/api/teacher/student-add", {"full_name": "도하윤", "class": "고1A", "track": "심화",
                                                      "ext_id": "S950", "parent_phone": "010-0000-1234"})
    assert code == 200 and j["ok"] and j["display_name"] == "하윤"
    assert j["student_link"].startswith("https://daybook.pages.dev/s/") and j["parent_link"].startswith("https://daybook.pages.dev/p/")
    assert j["parent"].startswith("안녕하세요, 보호자님. AMP 수학입니다.\n하윤 학생의 주간 기록 링크입니다.") and j["parent_link"] in j["parent"]
    assert j["student_link"] in j["student"] and j["parent_link"] not in j["student"]
    assert sent[-1][0] == "/tokmap" and len(sent[-1][1]["tokmap"]) == 4                 # the Worker accepts the new link
    row = conn.execute("SELECT * FROM students WHERE ext_id = 'S950'").fetchone()
    assert row["full_name"] == "도하윤" and row["parent_phone"] == "010-0000-1234" and row["track"] == "심화"
    assert call(url + "/api/teacher/student-add", {"full_name": "도하윤", "class": "고1A", "ext_id": "S950"})[0] == 400   # ID used
    assert call(url + "/api/teacher/student-add", {"full_name": "", "class": "고1A"})[0] == 400
    assert call(url + "/api/teacher/student-add", {"full_name": "남궁하늘별", "class": "고1A"})[0] == 400  # display = full name
    assert call(url + "/api/teacher/student-add", {"full_name": "도하늘", "class": "없는반"})[0] == 400
    # no-PII: the full name and the phone never reach dist/
    cfg = Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t")
    res = generate.publish(conn, cfg, tmp_path / "dist", today=TODAY, now=datetime.combine(TODAY, time(21), KST))
    assert res.ok, [str(f) for f in res.findings]
    for f in (tmp_path / "dist").rglob("*"):
        if f.is_file() and f.suffix in (".html", ".js", ".json", ".webmanifest"):
            t = f.read_text(encoding="utf-8")
            assert "도하윤" not in t and "0000-1234" not in t and "S950" not in t, f
    assert (tmp_path / "dist" / "s" / row["student_token"] / "index.html").exists()


def test_move_keeps_history(tserver, conn, klass):  # noqa: F811
    url, b, sent = tserver
    _, studs = klass
    conn.execute("INSERT INTO classes (name, term, weekday_mask) VALUES ('기초B', '2026-10-01..2026-12-31', 5)")
    conn.execute("INSERT INTO submissions (student_id, day, ts, source) VALUES (?, '2026-11-03', '2026-11-03T11:00:00.000Z', 'phone')",
                 (studs[0]["id"],))
    conn.commit()
    code, j = call(url + "/api/teacher/student-move", {"slug": studs[0]["slug"], "class": "기초B"})
    assert code == 200 and j["ok"]
    assert conn.execute("SELECT c.name FROM students s JOIN classes c ON c.id = s.class_id WHERE s.id = ?", (studs[0]["id"],)).fetchone()[0] == "기초B"
    assert conn.execute("SELECT COUNT(*) FROM submissions WHERE student_id = ?", (studs[0]["id"],)).fetchone()[0] == 1
    assert sent[-1][0] == "/tokmap"
    assert call(url + "/api/teacher/student-move", {"slug": studs[0]["slug"], "class": "없는반"})[0] == 400


def test_leave_with_a_date_and_undo(tserver, conn, klass, tmp_path):  # noqa: F811
    url, b, sent = tserver
    _, studs = klass
    code, j = call(url + "/api/teacher/student-leave", {"slug": studs[2]["slug"], "date": "2026-11-05"})
    assert j["ok"] and not j["active"] and j["left_on"] == "2026-11-05" and len(sent[-1][1]["tokmap"]) == 2
    code, r = call(url + "/api/teacher/roster")
    me = next(x for x in r["students"] if x["slug"] == studs[2]["slug"])
    assert (me["active"], me["left_on"]) == (False, "2026-11-05")
    cfg = Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t")
    generate.publish(conn, cfg, tmp_path / "d", today=TODAY, now=datetime.combine(TODAY, time(21), KST))
    assert not (tmp_path / "d" / "s" / studs[2]["student_token"]).exists()            # no new page renders
    code, j = call(url + "/api/teacher/student-leave", {"slug": studs[2]["slug"], "undo": True})
    assert j["active"] and j["left_on"] == "" and len(sent[-1][1]["tokmap"]) == 3
    assert call(url + "/api/teacher/student-leave", {"slug": studs[2]["slug"], "date": "5 Nov"})[0] == 400


def test_rotate_links(tserver, conn, klass):  # noqa: F811
    url, b, sent = tserver
    _, studs = klass
    old = conn.execute("SELECT student_token, parent_token FROM students WHERE id = ?", (studs[0]["id"],)).fetchone()
    code, j = call(url + "/api/teacher/student-rotate", {"slug": studs[0]["slug"], "which": "parent"})
    new = conn.execute("SELECT student_token, parent_token FROM students WHERE id = ?", (studs[0]["id"],)).fetchone()
    assert j["ok"] and new[0] == old[0] and new[1] != old[1] and j["parent_link"].endswith(f"/p/{new[1]}/")
    code, j = call(url + "/api/teacher/student-rotate", {"slug": studs[0]["slug"]})
    new2 = conn.execute("SELECT student_token FROM students WHERE id = ?", (studs[0]["id"],)).fetchone()[0]
    assert new2 != old[0] and new2 in sent[-1][1]["tokmap"] and old[0] not in sent[-1][1]["tokmap"]
    assert call(url + "/api/teacher/student-rotate", {"slug": studs[0]["slug"], "which": "x"})[0] == 400


def test_intro_messages(tserver, klass):  # noqa: F811
    url, b, sent = tserver
    _, studs = klass
    from urllib.parse import quote
    code, j = call(url + "/api/teacher/intro?slug=" + quote(studs[1]["slug"]))
    assert code == 200 and "도담 학생의 주간 기록 링크" in j["parent"] and "/p/" in j["parent"] and "/s/" in j["student"]
    assert call(url + "/api/teacher/intro?slug=nobody")[0] == 404


def test_class_create_and_edit(tserver, conn):  # noqa: F811
    url, b, sent = tserver
    body = {"create": True, "name": "고2A", "grade": "고2", "days": "월,수", "term": "2026-11-02..2027-02-26",
            "reward_label": "피자", "goal": "0", "start_time": "19:00"}
    code, j = call(url + "/api/teacher/class", body)
    assert code == 200 and j["name"] == "고2A"
    c = conn.execute("SELECT * FROM classes WHERE name = '고2A'").fetchone()
    assert (c["grade"], c["weekday_mask"], c["start_time"], c["reward_goal_points"]) == ("고2", 5, "19:00", None)
    assert call(url + "/api/teacher/class", body)[0] == 400                              # exists
    code, j = call(url + "/api/teacher/class", {"name": "고2A", "new_name": "고2B", "goal": 120, "days": "화,목"})
    c = conn.execute("SELECT * FROM classes WHERE name = '고2B'").fetchone()
    assert j["ok"] and c["reward_goal_points"] == 120 and c["weekday_mask"] == 10
    assert call(url + "/api/teacher/class", {"create": True, "name": "X", "days": "월"})[0] == 400   # no term
    assert call(url + "/api/teacher/class", {"name": "고2B", "start_time": "25:00"})[0] == 400
    code, r = call(url + "/api/teacher/roster")
    assert [x["name"] for x in r["classes"]] == ["고1A", "고2B"]


def test_roster_csv_preview_then_import(tserver, conn, klass):  # noqa: F811
    url, b, sent = tserver
    csv = ("Name,Student ID,Status,Phone Number\n한가람,S901,재원,010-1111-2222\n오세린,S960,재원,\n,S961,재원,\n").encode("utf-8")
    f = [{"name": "roster.csv", "data": "data:text/csv;base64," + base64.b64encode(csv).decode()}]
    n0 = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    code, j = call(url + "/api/teacher/roster-import", {"files": f, "class": "고1A"})
    assert code == 200 and not j["written"] and j["counts"] == {"new": 1, "updated": 1, "unchanged": 0, "skipped": 1}
    assert [r["status"] for r in j["rows"]] == ["updated", "new", "skipped"] and j["rows"][0]["why"] == "parent_phone"
    assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == n0                # a preview writes nothing
    assert conn.execute("SELECT parent_phone FROM students WHERE ext_id = 'S901'").fetchone()[0] is None
    assert not conn.execute("SELECT 1 FROM events WHERE type = 'import_roster'").fetchone()
    code, j = call(url + "/api/teacher/roster-import", {"files": f, "class": "고1A", "write": True})
    assert j["written"] and conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == n0 + 1
    assert conn.execute("SELECT parent_phone FROM students WHERE ext_id = 'S901'").fetchone()[0] == "010-1111-2222"
    assert sent[-1][0] == "/tokmap"
    assert call(url + "/api/teacher/roster-import", {"files": [{"name": "x.txt", "data": f[0]["data"]}]})[0] == 400


def test_doctor_button(tserver):  # noqa: F811
    """G1: the Teacher tab's 점검 runs the same checks as `daybook doctor`."""
    url, b, sent = tserver
    code, j = call(url + "/api/teacher/doctor", {})
    labels = [x["label"] for x in j["lines"]]
    assert code == 200 and "database integrity" in labels and "dist/ no-PII lint" in labels
    assert all(x["status"] in ("✓", "⚠", "✗") for x in j["lines"]) and j["ok"] in (True, False)
