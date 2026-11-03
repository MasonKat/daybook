"""G2: `daybook backup`: a consistent copy of the SQLite database while the board may be running.

Uses SQLite's online backup API (no file copy of a live database), checks the copy with PRAGMA integrity_check and keeps
the newest N. No encryption library: point `backup_dir` (daybook.json) at a BitLocker-encrypted USB drive or folder.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import models as M
from .config import Config

NAME_PREFIX = "daybook-"
MAX_AGE_DAYS = 7              # doctor warns when the newest backup is older than this


class BackupError(RuntimeError):
    pass


@dataclass
class BackupResult:
    path: Path
    integrity: str
    removed: list[Path]


def backup_dir(cfg: Config, db_path: str | Path) -> Path:
    """`backup_dir` from daybook.json; relative paths (and the default backups/) sit next to the database."""
    d = Path(str(cfg.extra.get("backup_dir") or "backups"))
    return d if d.is_absolute() else Path(db_path).resolve().parent / d


def existing(folder: Path) -> list[Path]:
    """Backups in the folder, newest first (by modification time, then name)."""
    if not folder.is_dir():
        return []
    files = [p for p in folder.glob(f"{NAME_PREFIX}*.sqlite") if p.is_file()]
    return sorted(files, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def newest_age_days(folder: Path, now: datetime | None = None) -> float | None:
    files = existing(folder)
    if not files:
        return None
    now = now or datetime.now()
    return (now.timestamp() - files[0].stat().st_mtime) / 86400


def is_removable(folder: Path) -> bool | None:
    """True / False on Windows (GetDriveTypeW: DRIVE_REMOVABLE = 2); None elsewhere (unknown)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        root = os.path.splitdrive(str(folder.resolve()))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(root) == 2
    except Exception:            # noqa: BLE001 (a check must never crash the doctor)
        return None


def run(db_path: str | Path, cfg: Config, to: str | Path | None = None, keep: int = 30) -> BackupResult:
    src_path = Path(db_path)
    if not src_path.exists():
        raise BackupError(f"no database at {src_path}")
    if keep < 1:
        raise BackupError("--keep must be at least 1")
    folder = Path(to) if to else backup_dir(cfg, src_path)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = M.kst_now().strftime("%Y%m%d-%H%M")
    target = folder / f"{NAME_PREFIX}{stamp}.sqlite"
    n = 2
    while target.exists():
        target = folder / f"{NAME_PREFIX}{stamp}-{n}.sqlite"
        n += 1
    tmp = target.with_suffix(".partial")
    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)                              # the online backup API: safe while the board writes
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    if integrity != "ok":
        tmp.unlink(missing_ok=True)
        raise BackupError(f"the copy failed integrity_check: {integrity}")
    tmp.replace(target)
    removed = []
    for old in existing(folder)[keep:]:
        old.unlink()
        removed.append(old)
    return BackupResult(target, integrity, removed)
