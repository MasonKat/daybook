from pathlib import Path

from console import importers

ROOT = Path(__file__).resolve().parent.parent
DEMO_ROUNDS = ROOT / "demo" / "screeners"


def test_import_screeners_demo_rounds(conn, klass):
    _, studs = klass
    rep = importers.import_screeners(conn, DEMO_ROUNDS)
    assert rep.files == 3
    # 3 class students matched in each of 3 rounds (S902 by name, the others by ext_id)
    assert rep.imported == 9
    # the 9 demo students not in this roster + S999 are reported, never invented
    assert len(rep.unmatched) == 3 * 10
    assert any("S999" in u for u in rep.unmatched)
    assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 3
    row = conn.execute("SELECT * FROM tests WHERE student_id = ? ORDER BY date LIMIT 1", (studs[1]["id"],)).fetchone()
    assert row["test_name"] == "Adaptive_Screeners_3S_Oct17_G10" and row["date"] == "2026-10-17"
    assert row["n_quizzes"] == 12
    assert abs(row["efficiency"] - row["correct"] / (row["correct"] + row["wrong"])) < 1e-9
    assert row["persona"] in ("도전형", "안정형", "신중형") and row["reach"] is not None
    assert row["source"].startswith("screener:")


def test_import_screeners_is_idempotent(conn, klass):
    importers.import_screeners(conn, DEMO_ROUNDS)
    importers.import_screeners(conn, DEMO_ROUNDS)
    assert conn.execute("SELECT COUNT(*) FROM tests").fetchone()[0] == 9


def test_screener_bad_file_name_and_ambiguous_name(conn, klass, tmp_path):
    cls, _ = klass
    importers.add_student(conn, cls["id"], "한가람")  # a second 한가람 without ext_id
    (tmp_path / "round_X_2026-11-01.json").write_text(
        '{"set_id": "X", "date": "2026-11-01", "n_quizzes": 4, "students": ['
        '{"student": "", "name": "한가람", "correct": 1, "wrong": 1, "blank": 2, "reach_ceiling": 0.4}]}',
        encoding="utf-8")
    (tmp_path / "round_bad.json").write_text("{}", encoding="utf-8")
    rep = importers.import_screeners(conn, tmp_path)
    assert rep.imported == 0
    assert rep.unmatched and "ambiguous" in rep.unmatched[0]
    assert rep.errors and "round_bad.json" in rep.errors[0]


def test_roster_import_with_mapping(conn):
    conn.execute("INSERT INTO classes (name, term, weekday_mask) VALUES ('고1A', '2026-10-01..2026-12-31', 18)")
    cid = conn.execute("SELECT id FROM classes").fetchone()[0]
    mapping = importers.load_mapping(ROOT / "mappings" / "roster.json")
    rep = importers.import_roster(conn, ROOT / "mappings" / "roster.example.csv", mapping, cid)
    assert len(rep.added) == 3 and not rep.skipped
    rows = {r["ext_id"]: r for r in conn.execute("SELECT * FROM students")}
    assert rows["S901"]["full_name"] == "한가람" and rows["S901"]["display_name"] == "가람"
    assert rows["S901"]["active"] == 1 and rows["S903"]["active"] == 0   # 휴원 -> inactive
    assert rows["S902"]["parent_phone"] is None
    assert rows["S901"]["slug"] == "가람"
    # re-import: nothing duplicated, tokens unchanged
    tok = rows["S901"]["student_token"]
    rep2 = importers.import_roster(conn, ROOT / "mappings" / "roster.example.csv", mapping, cid)
    assert not rep2.added and len(rep2.unchanged) == 3
    assert conn.execute("SELECT student_token FROM students WHERE ext_id='S901'").fetchone()[0] == tok


def test_roster_import_updates_status_and_warns_on_full_name_display(conn, tmp_path):
    conn.execute("INSERT INTO classes (name, term, weekday_mask) VALUES ('B', '2026-10-01..2026-12-31', 1)")
    mapping = importers.load_mapping(ROOT / "mappings" / "roster.json")
    csv1 = tmp_path / "r.csv"
    csv1.write_text("Name,Student ID,Status,Number,Phone Number,School\n가람솔,S1,재원,1,,가상고\n이솔,S2,재원,2,,가상고\n",
                    encoding="utf-8")
    rep = importers.import_roster(conn, csv1, mapping, 1)
    assert len(rep.added) == 2
    assert any("이솔" in w for w in rep.warnings)  # 2-character name: display == full name
    csv1.write_text("Name,Student ID,Status,Number,Phone Number,School\n가람솔,S1,퇴원,1,,가상고\n", encoding="utf-8")
    rep = importers.import_roster(conn, csv1, mapping, 1)
    assert rep.updated == ["람솔"]
    assert conn.execute("SELECT active FROM students WHERE ext_id='S1'").fetchone()[0] == 0


def test_import_tests_csv(conn, klass):
    rep = importers.import_tests_csv(conn, ROOT / "mappings" / "tests.example.csv")
    assert rep.imported == 1 and len(rep.unmatched) == 1   # 도윤 is not in this roster
    row = conn.execute("SELECT * FROM tests").fetchone()
    assert row["efficiency"] == 7 / 9 and row["reach"] is None
