"""video-library: the in-page player and library catalog fields (videos.json items, all optional; old items stay valid).

  key           a safe slug, the item's page /v/<key>/ (e.g. 11-2-2-574, cv-10-2-014)
  chapters      [{"t": seconds, "label": "..."}]  타임스탬프 (sorted by t)
  book, no, level, unit                            workbook, quiz number, level, 단원 (library grouping and search)
  concept_key, next_key, related_keys              follow-up links: 개념 영상, 다음 문제, 같은 단원 문제
  feed          false keeps an item in the 라이브러리 only (not in the 영상 feed)
"""
from __future__ import annotations

import re

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


def fields(v: dict) -> tuple[dict, list[str]]:
    """The library fields of one raw item, normalized, and warnings for anything dropped."""
    out, warn = {}, []
    key = str(v.get("key") or "")
    if key:
        if KEY_RE.match(key):
            out["key"] = key
        else:
            warn.append(f"key {key!r}: lowercase letters, digits and - only")
    ch = []
    for c in v.get("chapters") or []:
        try:
            t, label = int(c["t"]), str(c["label"]).strip()[:40]
        except (KeyError, TypeError, ValueError):
            warn.append(f"{key or v.get('title')}: a chapter needs t (seconds) and label")
            continue
        if t >= 0 and label:
            ch.append({"t": t, "label": label})
    out["chapters"] = sorted(ch, key=lambda c: c["t"])
    for f in ("book", "no", "level", "unit"):
        out[f] = str(v.get(f) or "").strip()[:40]
    for f in ("concept_key", "next_key"):
        k = str(v.get(f) or "")
        out[f] = k if KEY_RE.match(k) else ""
    out["related_keys"] = [str(k) for k in (v.get("related_keys") or []) if KEY_RE.match(str(k))][:6]
    out["feed"] = v.get("feed") is not False
    return out, warn


def check(items: list[dict]) -> list[str]:
    """Catalog-wide warnings: duplicate keys, links to unknown keys, a key without a YouTube id (no page)."""
    warn, keys = [], {}
    for v in items:
        if v.get("key"):
            if v["key"] in keys:
                warn.append(f"duplicate key {v['key']}")
            keys[v["key"]] = v
    for v in items:
        if v.get("key") and not v.get("yt_id"):
            warn.append(f"{v['key']}: no yt_id, so no /v/ page")
        for f in ("concept_key", "next_key"):
            if v.get(f) and v[f] not in keys:
                warn.append(f"{v.get('key')}: {f} {v[f]} is not in the catalog")
        for k in v.get("related_keys") or []:
            if k not in keys:
                warn.append(f"{v.get('key')}: related {k} is not in the catalog")
    return warn


def links(v: dict, by_key: dict) -> dict:
    """The follow-up links of one item's page (only to items that have pages)."""
    def ref(k):
        x = by_key.get(k)
        return {"key": k, "title": x["title"]} if x else None
    return {"concept": ref(v.get("concept_key")), "next": ref(v.get("next_key")),
            "related": [r for r in (ref(k) for k in v.get("related_keys") or []) if r]}


def library(videos: list[dict]) -> dict:
    """The 영상 tab's 라이브러리: groups (book · level), the concept roadmap (by unit), every item for the number search."""
    items = [v for v in videos if v.get("key") and v.get("yt_id")]
    concepts = {v.get("concept_key") for v in items if v.get("concept_key")}
    groups: dict[str, list] = {}
    for v in items:
        if v["key"] in concepts:
            continue
        g = " · ".join(x for x in (v.get("book"), v.get("level")) if x) or "기타"
        groups.setdefault(g, []).append(v)
    road = sorted((v for v in items if v["key"] in concepts or v["key"].startswith("cv-")), key=lambda v: (v.get("unit", ""), v["key"]))
    pick = lambda v: {"key": v["key"], "title": v["title"], "no": v.get("no", ""), "unit": v.get("unit", "")}   # noqa: E731
    return {"groups": [{"name": g, "items": [pick(v) for v in sorted(vs, key=lambda v: (v.get("no", ""), v["key"]))]} for g, vs in sorted(groups.items())],
            "roadmap": [pick(v) for v in road], "any": bool(items)}
