"""Video thumbnails: shrink a local image to dist/t/<id>.jpg (480 px wide, JPEG q70, <= 60 KB). Needs Pillow."""
from __future__ import annotations

from pathlib import Path

MAX_W = 480
MAX_BYTES = 60 * 1024


def make_thumb(src: Path, dst: Path) -> None:
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover - Pillow is a declared dependency
        raise RuntimeError("Pillow is needed for video thumbnails: .venv/bin/pip install pillow") from e
    with Image.open(src) as im:
        im = im.convert("RGB")
        if im.width > MAX_W:
            im = im.resize((MAX_W, max(1, round(im.height * MAX_W / im.width))), Image.LANCZOS)
        for q in (70, 60, 50, 40):
            im.save(dst, "JPEG", quality=q, optimize=True, progressive=True)
            if dst.stat().st_size <= MAX_BYTES:
                return
        im.thumbnail((360, 360))
        im.save(dst, "JPEG", quality=40, optimize=True)


def make_demo_thumbs(out: Path, n: int = 6) -> list[Path]:
    """Synthetic thumbnails for the demo (abstract shapes, no text, no real content)."""
    from PIL import Image, ImageDraw
    out.mkdir(parents=True, exist_ok=True)
    palettes = [("#1e3a8a", "#7c3aed"), ("#0f766e", "#2563eb"), ("#4c1d95", "#14b8a6"),
                ("#1e40af", "#0ea5e9"), ("#312e81", "#22c55e"), ("#0b0f17", "#6366f1")]
    paths = []
    for i in range(n):
        a, b = palettes[i % len(palettes)]
        im = Image.new("RGB", (960, 540), a)
        dr = ImageDraw.Draw(im)
        for k in range(14):
            dr.ellipse([60 * k - 80, 300 - 18 * k - 40 * (i % 3), 60 * k + 220, 600 - 18 * k], outline=b, width=6)
        dr.rounded_rectangle([90, 120, 520, 150], radius=14, fill=b)
        dr.rounded_rectangle([90, 180, 400, 204], radius=12, fill="#e5e7eb")
        dr.text((90, 230), f"{i + 1:02d}", fill="#ffffff")
        p = out / f"v{i + 1}.jpg"
        im.save(p, "JPEG", quality=80)
        paths.append(p)
    return paths


if __name__ == "__main__":
    import sys
    for p in make_demo_thumbs(Path(sys.argv[1] if len(sys.argv) > 1 else "demo/thumbs")):
        print(p)
