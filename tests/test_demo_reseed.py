"""`daybook demo --force` on an existing demo database: every table is cleared children-first (from the schema's own
foreign keys), so a table added later can never break the re-seed again."""
import pytest

from console import demo, importers, ops, rounds as R
from console.config import ROOT

from conftest import TODAY


def test_reseed_twice_with_every_table_filled(conn):
    conn.execute("PRAGMA foreign_keys = ON")
    demo.seed(conn, today=TODAY)
    importers.import_screeners(conn, ROOT / "demo" / "screeners")
    importers.import_insights(conn, demo.write_demo_insights(conn, TODAY, ROOT / "demo" / ".reseed-test.json"))
    (ROOT / "demo" / ".reseed-test.json").unlink()
    cls = conn.execute("SELECT * FROM classes WHERE name = '고1A'").fetchone()
    sid = conn.execute("SELECT id FROM students WHERE class_id = ? LIMIT 1", (cls["id"],)).fetchone()[0]
    sess = conn.execute("SELECT id FROM sessions WHERE class_id = ? LIMIT 1", (cls["id"],)).fetchone()[0]
    R.add(conn, "Adaptive_Screeners_1S_Nov06_G10", "1학년", "고1A", 20, "2026-11-06 20:00", "2026-11-08 23:59")
    conn.execute("""INSERT INTO test_reports (student_id, round_id, incorrect, missed, hardest, reasons, ts, updated)
                    VALUES (?, 'Adaptive_Screeners_1S_Nov06_G10', '[1]', '[]', '[]', '["시간부족"]', 't', 't')""", (sid,))
    conn.execute("INSERT INTO class_bonus (class_id, session_id, points, source, ref, ts) VALUES (?, ?, 5, 'live', 'x', 't')", (cls["id"], sess))
    conn.execute("INSERT INTO effort (student_id, set_id, attempted, correct, source, ts) VALUES (?, 's', 3, 2, 'live', 't')", (sid,))
    conn.execute("INSERT INTO moments (class_id, session_id, ts, text, image_key) VALUES (?, ?, 't', '장면', 'x.jpg')", (cls["id"], sess))
    conn.execute("INSERT INTO settings (key, value, ts) VALUES ('practice_mode', 'manitoba', 't')")
    conn.execute("INSERT INTO meetings (student_id, day, kind, ts) VALUES (?, '2026-11-05', 'slc', 't')", (sid,))
    conn.execute("INSERT INTO snoozes (student_id, rule, until, ts) VALUES (?, 'all', '2026-11-13', 't')", (sid,))
    conn.execute("INSERT INTO reviews (id, student_id, class_id, day, text, ts) VALUES ('abcdefabcdef', ?, ?, '2026-11-02', '판서 최고', 't')", (sid, cls["id"]))
    conn.execute("INSERT INTO review_rounds (id, class_id, period_from, period_to, open_at, close_at) VALUES ('r1', ?, 'a', 'b', 'c', 'd')", (cls["id"],))
    conn.execute("INSERT INTO review_votes (round_id, student_id, review_id, ts) VALUES ('r1', ?, 'abcdefabcdef', 't')", (sid,))
    conn.execute("INSERT INTO shoutouts (student_id, class_id, day, ts, points, text) VALUES (?, ?, '2026-11-05', 't', 1, '굿')", (sid, cls["id"]))
    conn.execute("INSERT INTO concept_notes (class_id, day, text, ts) VALUES (?, '2026-11-05', '개념', 't')", (cls["id"],))
    conn.execute("INSERT INTO moment_drafts (class_id, day, text, ts) VALUES (?, '2026-11-05', '장면', 't')", (cls["id"],))
    conn.execute("INSERT INTO class_shouts (class_id, day, ts, points, text) VALUES (?, '2026-11-05', 't', 1, '다 같이')", (cls["id"],))
    conn.execute("INSERT INTO video_ranks (period_from, pool, rank, video_id, score, watchers) VALUES ('2026-11-02', 'p', 1, 'v', 3, 1)")
    conn.execute("""INSERT INTO quiz_marks (student_id, class_id, day, label, quiz, mark, source, ts, updated)
                    VALUES (?, ?, '2026-11-05', '서술형 1', 1, 'p', 'essay', 't', 't')""", (sid, cls["id"]))
    conn.execute("INSERT INTO review_wins (round_id, review_id, votes) VALUES ('r1', 'abcdefabcdef', 1)")
    conn.execute("INSERT INTO avatar_uploads (student_id, mime, image, ts) VALUES (?, 'image/png', x'89', 't')", (sid,))
    conn.execute("INSERT INTO unlocks (student_id, item, ts) VALUES (?, 'sunset', 't')", (sid,))
    conn.commit()
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    empty = [t for t in tables if t != "events" and not conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()]
    assert empty == [], empty                                   # every table has rows before the re-seed
    for _ in range(2):
        demo.seed(conn, today=TODAY, force=True)
        assert conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 22
        assert conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM test_reports").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'demo_seed'").fetchone()[0] == 3    # events are kept


def test_clear_order_covers_the_whole_schema_children_first(conn):
    order = demo.clear_order(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    assert set(order) == tables - {"events"}
    pos = {t: i for i, t in enumerate(order)}
    for t in order:
        for fk in conn.execute(f"PRAGMA foreign_key_list({t})"):
            if fk[2] != t:
                assert pos[t] < pos[fk[2]], (t, fk[2])          # a child is cleared before its parent


def test_force_never_touches_a_real_database(conn, klass):
    with pytest.raises(demo.DemoError, match="real"):
        demo.seed(conn, today=TODAY, force=True)
