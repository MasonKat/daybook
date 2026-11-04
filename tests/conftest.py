import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from console import db as dbmod  # noqa: E402
from console import models as M  # noqa: E402
from console.config import Config  # noqa: E402
from console.importers import add_student  # noqa: E402

TODAY = date(2026, 11, 6)  # a Friday


@pytest.fixture
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.sqlite")
    dbmod.init(c)
    yield c
    c.close()


@pytest.fixture
def cfg():
    return Config(worker_url="https://w.example", pages_url="https://p.example", teacher_token="t")


@pytest.fixture
def klass(conn):
    """A class (Tue, Fri) with three synthetic students."""
    conn.execute("INSERT INTO classes (name, term, weekday_mask, reward_label) VALUES (?,?,?,?)",
                 ("고1A", "2026-10-01..2026-12-31", M.mask_from_days("화,금"), "피자"))
    cls = conn.execute("SELECT * FROM classes WHERE name='고1A'").fetchone()
    studs = [add_student(conn, cls["id"], n, ext_id=e) for n, e in (("한가람", "S901"), ("윤도담", "S902"), ("서하늘", "S903"))]
    conn.execute("UPDATE students SET consent = 'yes'")       # F17: the parents of the fixture class said yes
    conn.commit()
    studs = [conn.execute("SELECT * FROM students WHERE id = ?", (s["id"],)).fetchone() for s in studs]
    return cls, studs
