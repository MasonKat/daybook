"""Local console configuration (`daybook.json`, gitignored). Environment variables override the file."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    worker_url: str = ""         # https://daybook-worker.<account>.workers.dev
    pages_url: str = ""          # https://<project>.pages.dev  (used for printed links and QR URLs)
    teacher_token: str = ""      # same value as the Worker secret TEACHER_TOKEN
    videos_path: str = "videos.json"
    assignments_path: str = "assignments.json"   # optional {set_id: label}; students never see a raw set id
    moments_dir: str = "moments"                  # local store of 오늘의 장면 images (gitignored)
    board_poll_seconds: int = 60
    extra: dict = field(default_factory=dict)
    path: str = ""               # G1: the daybook.json this came from ("" = none found)

    @property
    def worker(self) -> str:
        return self.worker_url.rstrip("/")

    @property
    def pages(self) -> str:
        return self.pages_url.rstrip("/")


def load(path: str | Path | None = None) -> Config:
    p = Path(path or os.environ.get("DAYBOOK_CONFIG") or "daybook.json")
    data: dict = {}
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    cfg = Config(
        worker_url=data.get("worker_url", ""),
        pages_url=data.get("pages_url", ""),
        teacher_token=data.get("teacher_token", ""),
        videos_path=data.get("videos_path", "videos.json"),
        assignments_path=data.get("assignments_path", "assignments.json"),
        moments_dir=data.get("moments_dir", "moments"),
        board_poll_seconds=int(data.get("board_poll_seconds", 60)),
        extra={k: v for k, v in data.items()},
        path=str(p.resolve()) if p.exists() else "",
    )
    cfg.worker_url = os.environ.get("DAYBOOK_WORKER_URL", cfg.worker_url)
    cfg.pages_url = os.environ.get("DAYBOOK_PAGES_URL", cfg.pages_url)
    cfg.teacher_token = os.environ.get("DAYBOOK_TEACHER_TOKEN", cfg.teacher_token)
    return cfg


def strings() -> dict:
    return json.loads((ROOT / "strings.ko.json").read_text(encoding="utf-8"))
