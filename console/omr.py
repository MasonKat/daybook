"""F8 spike: reading a photographed answer card (20 items × ①–⑤). Pure Pillow, no new dependency.

Pipeline: four corner fiducials (dark squares) -> perspective warp to the canonical sheet -> each bubble's interior
brightness against the paper right next to it -> filled / empty -> per item: blank, one choice, or several.
`synthetic_card()` makes phone-like test images (perspective, blur, shadow, JPEG). Behind `daybook omr --spike`.
"""
from __future__ import annotations

import io
import math
import random
from collections import deque

from PIL import Image, ImageChops, ImageDraw, ImageFilter

W, H = 1240, 1754                      # canonical sheet (A4 at 150 dpi)
FID = 70                               # fiducial square side
FID_C = [(95, 95), (W - 95, 95), (95, H - 95), (W - 95, H - 95)]   # centres: TL, TR, BL, BR
COLS, ROWS, CHOICES = (300, 800), 10, 5
ROW_Y0, ROW_DY, BUB_DX, R = 360, 120, 85, 26
FILLED_RATIO = 0.62                    # interior / local paper brightness below this = filled


def bubble_centres() -> list[list[tuple[int, int]]]:
    """[item][choice] -> (x, y) on the canonical sheet; items 1-10 left column, 11-20 right column."""
    out = []
    for x0 in COLS:
        for i in range(ROWS):
            y = ROW_Y0 + i * ROW_DY
            out.append([(x0 + j * BUB_DX, y) for j in range(CHOICES)])
    return out


def blank_sheet() -> Image.Image:
    im = Image.new("L", (W, H), 255)
    dr = ImageDraw.Draw(im)
    for cx, cy in FID_C:
        dr.rectangle([cx - FID // 2, cy - FID // 2, cx + FID // 2, cy + FID // 2], fill=0)
    dr.text((W // 2 - 60, 170), "AMP ANSWER CARD", fill=40)
    for n, row in enumerate(bubble_centres(), 1):
        dr.text((row[0][0] - 95, row[0][1] - 8), f"{n:2d}", fill=60)
        for j, (x, y) in enumerate(row):
            dr.ellipse([x - R, y - R, x + R, y + R], outline=110, width=3)
            dr.text((x - 4, y - 7), str(j + 1), fill=185)
    return im


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            raise ValueError("degenerate fiducials")
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def perspective_coeffs(dst: list[tuple[float, float]], src: list[tuple[float, float]]) -> list[float]:
    """Coefficients for Image.transform(PERSPECTIVE): maps an output point (dst) to the input point (src)."""
    a, b = [], []
    for (x, y), (u, v) in zip(dst, src):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    return _solve(a, b)


def synthetic_card(rng: random.Random) -> tuple[Image.Image, list[frozenset]]:
    """A filled card photographed by a phone: perspective, small rotation, shadow, blur, JPEG. Returns (image, truth)."""
    sheet = blank_sheet()
    dr = ImageDraw.Draw(sheet)
    truth = []
    light = rng.random() < 0.2                 # a light pencil on some cards
    for row in bubble_centres():
        r = rng.random()
        picks = frozenset() if r < 0.08 else frozenset(rng.sample(range(1, 6), 2)) if r < 0.12 else frozenset([rng.randint(1, 5)])
        for j in picks:
            x, y = row[j - 1]
            ox, oy, rr = rng.randint(-4, 4), rng.randint(-4, 4), rng.randint(17, 23)
            dr.ellipse([x + ox - rr, y + oy - rr, x + ox + rr, y + oy + rr],
                       fill=rng.randint(95, 135) if light else rng.randint(25, 85))
        truth.append(picks)
    cw, ch = 1500, 2000
    bg = rng.randint(70, 150)
    m = 0.06
    corners = [(cw * (0.06 + rng.uniform(-m, m) * 0.6), ch * (0.06 + rng.uniform(-m, m) * 0.6)),
               (cw * (0.94 + rng.uniform(-m, m) * 0.6), ch * (0.06 + rng.uniform(-m, m) * 0.6)),
               (cw * (0.06 + rng.uniform(-m, m) * 0.6), ch * (0.94 + rng.uniform(-m, m) * 0.6)),
               (cw * (0.94 + rng.uniform(-m, m) * 0.6), ch * (0.94 + rng.uniform(-m, m) * 0.6))]
    coeffs = perspective_coeffs(corners, [(0, 0), (W, 0), (0, H), (W, H)])
    photo = sheet.transform((cw, ch), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=bg)
    # a soft shadow from one side, and uneven light
    turn = rng.choice([0, 90, 180, 270])
    grad = Image.linear_gradient("L").resize((ch, cw) if turn in (90, 270) else (cw, ch)).rotate(turn, expand=True)
    lo = rng.randint(150, 215)
    shade = grad.point(lambda p: lo + (255 - lo) * p // 255)
    photo = ImageChops.multiply(photo, shade)
    if rng.random() < 0.25:                    # a hard-edged shadow (a hand or the phone) over part of the card
        hard = Image.new("L", (cw, ch), 255)
        x = rng.randint(cw // 3, cw * 2 // 3)
        ImageDraw.Draw(hard).polygon([(x, 0), (cw, 0), (cw, ch), (x + rng.randint(-200, 200), ch)], fill=rng.randint(120, 170))
        photo = ImageChops.multiply(photo, hard)
    photo = photo.filter(ImageFilter.GaussianBlur(rng.uniform(0.6, 1.8)))
    photo = photo.resize((cw * 2 // 3, ch * 2 // 3), Image.BILINEAR)
    buf = io.BytesIO()
    photo.convert("RGB").save(buf, "JPEG", quality=rng.randint(55, 85))
    return Image.open(io.BytesIO(buf.getvalue())), truth


def _otsu(hist: list[int]) -> int:
    total = sum(hist)
    s_all = sum(i * h for i, h in enumerate(hist))
    w0 = s0 = 0
    best, thr = -1.0, 128
    for t in range(256):
        w0 += hist[t]
        if w0 == 0 or w0 == total:
            continue
        s0 += t * hist[t]
        m0, m1 = s0 / w0, (s_all - s0) / (total - w0)
        between = w0 * (total - w0) * (m0 - m1) ** 2
        if between > best:
            best, thr = between, t
    return thr


def find_fiducials(gray: Image.Image) -> list[tuple[float, float]]:
    """Centres of the four corner squares (TL, TR, BL, BR) in image coordinates."""
    sw = 500
    scale = gray.width / sw
    small = gray.resize((sw, max(1, round(gray.height / scale))), Image.BILINEAR)
    w, h = small.size
    # local (adaptive) threshold: darker than its neighbourhood by a clear margin. A global threshold fails under
    # hard shadows; squares stay far darker than the paper right around them, shadow or not.
    local = small.filter(ImageFilter.BoxBlur(14))
    diff = ImageChops.subtract(local, small).tobytes()            # how much darker than the neighbourhood
    px = small.tobytes()
    lv = local.tobytes()
    dark = bytearray(1 if (d > 35 and v < 0.62 * m) else 0 for d, v, m in zip(diff, px, lv))
    seen = bytearray(w * h)
    comps = []
    for start in range(w * h):
        if not dark[start] or seen[start]:
            continue
        q = deque([start]); seen[start] = 1
        n = sx = sy = 0
        x0 = y0 = 10 ** 9; x1 = y1 = -1
        while q:
            i = q.popleft()
            x, y = i % w, i // w
            n += 1; sx += x; sy += y
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
            for j in (i - 1, i + 1, i - w, i + w):
                if 0 <= j < w * h and not seen[j] and dark[j] and abs(j % w - x) <= 1:
                    seen[j] = 1; q.append(j)
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if x0 == 0 or y0 == 0 or x1 == w - 1 or y1 == h - 1:
            continue                                   # the table around the paper
        if not (6 <= bw <= 60 and 6 <= bh <= 60 and 0.6 <= bw / bh <= 1.6 and n / (bw * bh) >= 0.6):
            continue
        comps.append((sx / n, sy / n, n))
    if len(comps) < 4:
        raise ValueError("fiducials not found")
    big = max(c[2] for c in comps)
    cands = [c for c in comps if c[2] >= 0.45 * big] or comps     # the squares are the largest solid blobs
    out = []
    for cx, cy in ((0, 0), (w, 0), (0, h), (w, h)):
        best = min(cands, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)
        out.append((best[0] * scale, best[1] * scale))
    if len({(round(x), round(y)) for x, y in out}) < 4:
        raise ValueError("fiducials not found")
    return out


def _mean(img: Image.Image, x: float, y: float, r: int) -> float:
    box = img.crop((int(x - r), int(y - r), int(x + r), int(y + r)))
    hist = box.histogram()
    n = sum(hist)
    return sum(i * c for i, c in enumerate(hist)) / n if n else 255.0


def read_card(img: Image.Image) -> list[frozenset]:
    """Per item the set of filled choices (empty = blank, 2+ = several marked)."""
    gray = img.convert("L")
    found = find_fiducials(gray)
    warped = gray.transform((W, H), Image.PERSPECTIVE, perspective_coeffs(FID_C, found), Image.BILINEAR)
    out = []
    for row in bubble_centres():
        picks = set()
        for j, (x, y) in enumerate(row):
            inside = _mean(warped, x, y, 14)
            paper = max(_mean(warped, x + BUB_DX / 2, y, 6), _mean(warped, x, y + ROW_DY / 2, 6), 1.0)
            if inside / paper < FILLED_RATIO:
                picks.add(j + 1)
        out.append(frozenset(picks))
    return out


def evaluate(n: int = 200, seed: int = 20261104) -> dict:
    """Accuracy on n synthetic phone photos: per item (exact set), per card (all 20 right), failures to locate."""
    rng = random.Random(seed)
    items = right = cards = cards_ok = lost = 0
    kinds = {"blank": [0, 0], "single": [0, 0], "multi": [0, 0]}
    for _ in range(n):
        img, truth = synthetic_card(rng)
        cards += 1
        try:
            got = read_card(img)
        except ValueError:
            lost += 1
            items += len(truth)
            continue
        ok = 0
        for t, g in zip(truth, got):
            k = "blank" if not t else "single" if len(t) == 1 else "multi"
            kinds[k][1] += 1
            if t == g:
                kinds[k][0] += 1
                ok += 1
        items += len(truth); right += ok
        cards_ok += ok == len(truth)
    return {"cards": cards, "items": items, "item_accuracy": right / items if items else 0.0,
            "card_accuracy": cards_ok / cards if cards else 0.0, "not_located": lost,
            "by_kind": {k: (v[0] / v[1] if v[1] else None, v[1]) for k, v in kinds.items()}}


def to_effort(conn, student_id: int, set_id: str, read: list[frozenset], key: list[int] | None = None) -> int:
    """One `effort` row per item, source 'card': attempted = any bubble filled; correct only with an answer key."""
    from .db import log_event, now_utc_iso
    ts = now_utc_iso()
    for i, picks in enumerate(read, 1):
        correct = None
        if key and i <= len(key):
            correct = 1 if picks == frozenset([key[i - 1]]) else 0
        conn.execute("""INSERT INTO effort (student_id, set_id, item_id, attempted, correct, source, ts)
                        VALUES (?, ?, ?, ?, ?, 'card', ?)""", (student_id, set_id, str(i), 1 if picks else 0, correct, ts))
    log_event(conn, "omr_card", {"student_id": student_id, "set_id": set_id, "items": len(read)}, ts)
    conn.commit()
    return len(read)


if __name__ == "__main__":
    import json
    print(json.dumps(evaluate(int(__import__("sys").argv[1]) if len(__import__("sys").argv) > 1 else 20), indent=1))
