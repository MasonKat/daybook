"""Video thumbnails: shrink a local image to dist/t/<id>.jpg (480 px wide, JPEG q70, <= 60 KB). Needs Pillow."""
from __future__ import annotations

from pathlib import Path

MAX_W = 480
MAX_BYTES = 60 * 1024


def make_thumb(src: Path, dst: Path, max_w: int = MAX_W) -> None:
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover - Pillow is a declared dependency
        raise RuntimeError("Pillow is needed for video thumbnails: .venv/bin/pip install pillow") from e
    with Image.open(src) as im:
        im = im.convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, max(1, round(im.height * max_w / im.width))), Image.LANCZOS)
        for q in (70, 60, 50, 40):
            im.save(dst, "JPEG", quality=q, optimize=True, progressive=True)
            if dst.stat().st_size <= MAX_BYTES:
                return
        im.thumbnail((360, 360))
        im.save(dst, "JPEG", quality=40, optimize=True)


def make_demo_thumbs(out: Path, n: int = 0) -> list[Path]:
    """Varied synthetic thumbnails for the demo: math-flavoured abstract scenes, no text, no real content.
    Shorts are 9:16 (s1..s4.jpg), long videos 16:9 (l1..l5.jpg). `n` > 0 makes only that many long ones (tests)."""
    import math
    from PIL import Image, ImageDraw
    out.mkdir(parents=True, exist_ok=True)

    def paper(w, h, bg, line, step):
        im = Image.new("RGB", (w, h), bg)
        dr = ImageDraw.Draw(im)
        for x in range(0, w, step):
            dr.line([(x, 0), (x, h)], fill=line, width=1)
        for y in range(0, h, step):
            dr.line([(0, y), (w, y)], fill=line, width=1)
        return im, dr

    def curve(dr, w, h, f, color, width):
        pts = [(x, h / 2 - f((x - w / 2) / (w / 6)) * h / 6) for x in range(0, w, 4)]
        dr.line(pts, fill=color, width=width, joint="curve")

    scenes = [
        # (name, size, painter)
        ("s1", (540, 960), lambda dr, w, h: (curve(dr, w, h, lambda t: t * t / 2 - 1.5, "#f59e0b", 10),
                                             dr.ellipse([w / 2 - 18, h / 2 + h / 4 - 18, w / 2 + 18, h / 2 + h / 4 + 18], fill="#f43f5e"))),
        ("s2", (540, 960), lambda dr, w, h: [dr.polygon([(w * .2, h * .7), (w * .8, h * .7), (w * .35, h * .3)], outline="#38bdf8", width=9),
                                             dr.arc([w * .14, h * .64, w * .26, h * .76], 300, 360, fill="#facc15", width=6)]),
        ("s3", (540, 960), lambda dr, w, h: [dr.rounded_rectangle([w * .12 + i * w * .2, h * (.75 - .1 * (i + 1) ** 1.1), w * .26 + i * w * .2, h * .75],
                                                                  radius=10, fill=c) for i, c in enumerate(["#22c55e", "#14b8a6", "#0ea5e9", "#6366f1"])]),
        ("s4", (540, 960), lambda dr, w, h: curve(dr, w, h, lambda t: 1.6 * math.sin(2 * t), "#e879f9", 10)),
        ("l1", (960, 540), lambda dr, w, h: (curve(dr, w, h, lambda t: t ** 3 / 6 - t / 2, "#60a5fa", 9),
                                             curve(dr, w, h, lambda t: -t / 3, "#fbbf24", 5))),
        ("l2", (960, 540), lambda dr, w, h: [dr.ellipse([w / 2 - 170, h / 2 - 170, w / 2 + 170, h / 2 + 170], outline="#f472b6", width=8),
                                             dr.line([(w / 2, h / 2), (w / 2 + 120, h / 2 - 120)], fill="#f472b6", width=6),
                                             dr.line([(w / 2 - 170, h / 2), (w / 2 + 170, h / 2)], fill="#94a3b8", width=3)]),
        ("l3", (960, 540), lambda dr, w, h: [dr.rectangle([140 + 70 * i, 380 - 40 * ((i * 7) % 5) - 30, 190 + 70 * i, 380], fill=c)
                                             for i, c in enumerate(["#34d399", "#2dd4bf", "#38bdf8", "#818cf8", "#a78bfa", "#f472b6", "#fb7185", "#fbbf24", "#a3e635", "#22d3ee"])]),
        ("l4", (960, 540), lambda dr, w, h: [dr.regular_polygon((w / 2, h / 2, 190), 6, outline="#2dd4bf", width=8),
                                             dr.regular_polygon((w / 2, h / 2, 110), 3, rotation=30, outline="#facc15", width=7)]),
        ("l5", (960, 540), lambda dr, w, h: [curve(dr, w, h, lambda t, k=k: k * 0.4 * t * t - 1, c, 6)
                                             for k, c in ((1, "#fb923c"), (2, "#f43f5e"), (3, "#c084fc"))]),
    ]
    bgs = [("#0f172a", "#1e293b"), ("#14213d", "#22335a"), ("#052e2b", "#0f4a44"), ("#1e1b4b", "#312e81"),
           ("#0b1324", "#1c2a4a"), ("#2a0f2e", "#45204c"), ("#0f1f17", "#1f3a2b"), ("#13171f", "#262d3a"), ("#1f1206", "#3a2510")]
    paths = []
    picked = [sc for sc in scenes if sc[0].startswith("l")][:n] if n else scenes
    for i, (name, (w, h), paint) in enumerate(picked):
        bg, line = bgs[i % len(bgs)]
        im, dr = paper(w, h, bg, line, 48 if w > h else 54)
        paint(dr, w, h)
        p = out / f"{name}.jpg"
        im.save(p, "JPEG", quality=72)
        paths.append(p)
    return paths


if __name__ == "__main__":
    import sys
    for p in make_demo_thumbs(Path(sys.argv[1] if len(sys.argv) > 1 else "demo/thumbs")):
        print(p)
