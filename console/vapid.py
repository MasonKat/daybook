"""F15.3: VAPID keys (P-256) for Web Push, generated locally with the standard library only.

The private key goes into daybook.json (gitignored) and the Worker secret VAPID_PRIVATE; it is never written to the
repo. The arithmetic below is plain P-256 point multiplication, used once to make a key pair.
"""
from __future__ import annotations

import base64
import secrets

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def public_point(d: int) -> tuple[int, int]:
    if not 1 <= d < N:
        raise ValueError("private key out of range")
    result, addend = None, G
    while d:
        if d & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        d >>= 1
    return result


def on_curve(pt: tuple[int, int]) -> bool:
    x, y = pt
    return (y * y - (x * x * x + A * x + B)) % P == 0


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def public_from_private(private_b64u: str) -> str:
    """The uncompressed public point (0x04 || x || y) for a base64url private scalar."""
    x, y = public_point(int.from_bytes(unb64u(private_b64u), "big"))
    return b64u(b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big"))


def generate() -> tuple[str, str]:
    """(public, private) as base64url: the forms the Worker (VAPID_PUBLIC / VAPID_PRIVATE) and the browser expect."""
    d = secrets.randbelow(N - 1) + 1
    priv = b64u(d.to_bytes(32, "big"))
    return public_from_private(priv), priv
