"""Generate static/icon-{180,192,512}.png (blue square, white check). Stdlib only; run once."""
import math
import struct
import zlib
from pathlib import Path

BLUE = (37, 99, 235)
WHITE = (255, 255, 255)


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def icon(n: int) -> bytes:
    pts = [(0.27, 0.53), (0.43, 0.69), (0.74, 0.35)]
    w = 0.075 * n
    rows = []
    for y in range(n):
        row = bytearray([0])
        for x in range(n):
            px, py = x + 0.5, y + 0.5
            d = min(seg_dist(px, py, pts[i][0] * n, pts[i][1] * n, pts[i + 1][0] * n, pts[i + 1][1] * n)
                    for i in range(2))
            a = max(0.0, min(1.0, w - d + 0.5))
            row += bytes(round(BLUE[k] * (1 - a) + WHITE[k] * a) for k in range(3))
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "static"
    for n in (180, 192, 512):
        (out / f"icon-{n}.png").write_bytes(icon(n))
        print(out / f"icon-{n}.png")
