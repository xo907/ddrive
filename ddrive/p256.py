"""ECDH on NIST P-256, in plain Python: what a browser does with crypto.subtle (ECDH, "P-256", raw
public keys), for the reference client of end-to-end accounts (vault.py) when moving one account into
another (transfer.py). Uses the `cryptography` package when it is there, which is faster.

Only what the key exchange needs: a key pair, the public key as 65 bytes (0x04 || x || y), and the
shared secret (the x coordinate, 32 bytes), with the other side's point checked to be on the curve.
"""

import secrets

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


class BadKey(ValueError):
    pass


def _on_curve(x, y):
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


# Jacobian coordinates (X, Y, Z): x = X/Z^2, y = Y/Z^3; Z = 0 is the point at infinity.
def _double(p):
    x, y, z = p
    if z == 0 or y == 0:
        return (0, 1, 0)
    yy = y * y % P
    s = 4 * x * yy % P
    zz = z * z % P
    m = 3 * (x - zz) * (x + zz) % P
    x3 = (m * m - 2 * s) % P
    return (x3, (m * (s - x3) - 8 * yy * yy) % P, 2 * y * z % P)


def _add(p, q):
    if p[2] == 0:
        return q
    if q[2] == 0:
        return p
    x1, y1, z1 = p
    x2, y2, z2 = q
    z1z1, z2z2 = z1 * z1 % P, z2 * z2 % P
    u1, u2 = x1 * z2z2 % P, x2 * z1z1 % P
    s1, s2 = y1 * z2 * z2z2 % P, y2 * z1 * z1z1 % P
    h, r = (u2 - u1) % P, (s2 - s1) % P
    if h == 0:
        return _double(p) if r == 0 else (0, 1, 0)
    hh = h * h % P
    hhh = h * hh % P
    v = u1 * hh % P
    x3 = (r * r - hhh - 2 * v) % P
    return (x3, (r * (v - x3) - s1 * hhh) % P, h * z1 * z2 % P)


def _mul(k, point):
    acc, add = (0, 1, 0), (point[0], point[1], 1)
    while k:
        if k & 1:
            acc = _add(acc, add)
        add = _double(add)
        k >>= 1
    x, y, z = acc
    if z == 0:
        raise BadKey("the point at infinity")
    zi = pow(z, -1, P)
    zi2 = zi * zi % P
    return (x * zi2 % P, y * zi2 * zi % P)


def _point(raw: bytes):
    if len(raw) != 65 or raw[0] != 4:
        raise BadKey("not an uncompressed P-256 public key")
    x, y = int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big")
    if not _on_curve(x, y):
        raise BadKey("not a point on P-256")
    return x, y


def _raw(pt) -> bytes:
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


try:                                             # pragma: no cover - depends on what is installed
    from cryptography.hazmat.primitives.asymmetric import ec as _ec

    def keypair():
        priv = _ec.generate_private_key(_ec.SECP256R1())
        pub = priv.public_key().public_numbers()
        return priv, _raw((pub.x, pub.y))

    def shared(priv, their_raw: bytes) -> bytes:
        x, y = _point(their_raw)
        return priv.exchange(_ec.ECDH(), _ec.EllipticCurvePublicNumbers(x, y, _ec.SECP256R1()).public_key())
except ImportError:
    def keypair():
        """(a private key, its public key as 65 raw bytes)."""
        d = secrets.randbelow(N - 1) + 1
        return d, _raw(_mul(d, G))

    def shared(priv, their_raw: bytes) -> bytes:
        """The shared secret with the other side's raw public key: 32 bytes, as WebCrypto's deriveBits(256)."""
        return _mul(priv, _point(their_raw))[0].to_bytes(32, "big")


def pure_keypair():
    d = secrets.randbelow(N - 1) + 1
    return d, _raw(_mul(d, G))


def pure_shared(d, their_raw: bytes) -> bytes:
    return _mul(d, _point(their_raw))[0].to_bytes(32, "big")
