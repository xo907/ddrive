"""Reed-Solomon erasure coding over GF(256): the spare ("parity") pieces that let a file rebuild
pieces Discord lost.

A group of k data pieces gets m parity pieces; any k of the k + m pieces are enough to rebuild the
rest. The code is systematic (the data pieces are stored unchanged) and uses a Cauchy matrix, so
every choice of k pieces is solvable.

Only the standard library is used, and the heavy lifting runs in C: multiplying a whole piece by a
constant is one `bytes.translate` with a 256-byte table, and adding pieces (XOR) is done on Python
integers built with `int.from_bytes`. A shorter piece is treated as zero-padded to the group's
largest piece, which little-endian integers do by themselves.
"""

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]

MAX_PIECES = 256      # k + m must fit in GF(256)


def mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def inv(a):
    if a == 0:
        raise ZeroDivisionError("0 has no inverse in GF(256)")
    return _EXP[255 - _LOG[a]]


_TABLES = {}


def _table(c):
    t = _TABLES.get(c)
    if t is None:
        t = _TABLES[c] = bytes(mul(c, v) for v in range(256))
    return t


def coef(k, j, i):
    """Weight of data piece i in parity piece j (Cauchy: 1 / (x_j + y_i), x_j = k + j, y_i = i)."""
    return inv((k + j) ^ i)


def _scaled(c, data):
    """c * data as an integer (little-endian), ready to be XORed into an accumulator."""
    if c == 1:
        return int.from_bytes(data, "little")
    return int.from_bytes(data.translate(_table(c)), "little")


def check_shape(k, m):
    if k < 1 or m < 0 or k + m > MAX_PIECES:
        raise ValueError(f"unsupported group shape: {k} data + {m} parity pieces")


class Encoder:
    """Builds the m parity pieces of a group while its k data pieces are fed in one at a time,
    so only one data piece and the m running sums are in memory."""

    def __init__(self, k, m):
        check_shape(k, m)
        self.k, self.m = k, m
        self.size = 0
        self._acc = [0] * m

    def add(self, i, data):
        if not 0 <= i < self.k:
            raise IndexError(f"data piece {i} is outside a group of {self.k}")
        self.size = max(self.size, len(data))
        for j in range(self.m):
            self._acc[j] ^= _scaled(coef(self.k, j, i), data)

    def parity(self):
        return [a.to_bytes(self.size, "little") for a in self._acc]


def encode(pieces, m):
    enc = Encoder(len(pieces), m)
    for i, p in enumerate(pieces):
        enc.add(i, p)
    return enc.parity()


def _invert(matrix):
    """Inverse of a square matrix over GF(256) (Gauss-Jordan)."""
    n = len(matrix)
    a = [row[:] + [1 if i == j else 0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if a[r][col]), None)
        if pivot is None:
            raise ValueError("matrix is singular")
        a[col], a[pivot] = a[pivot], a[col]
        p = inv(a[col][col])
        a[col] = [mul(p, v) for v in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [v ^ mul(f, w) for v, w in zip(a[r], a[col])]
    return [row[n:] for row in a]


def decode(k, m, available, size):
    """Rebuild the missing data pieces of a group.

    available: {position: bytes} where positions 0..k-1 are data pieces and k..k+m-1 parity pieces.
    size: the group's piece size (its largest data piece). Returns {position: bytes} for every
    missing data position, each `size` bytes long (trim to the piece's real size afterwards).
    """
    check_shape(k, m)
    missing = [i for i in range(k) if i not in available]
    if not missing:
        return {}
    use = [p for p in range(k) if p in available] + [p for p in range(k, k + m) if p in available]
    if len(use) < k:
        raise ValueError(f"cannot rebuild: {len(use)} of the {k} pieces needed are available")
    use = use[:k]
    matrix = [[1 if p == i else 0 for i in range(k)] if p < k else [coef(k, p - k, i) for i in range(k)]
              for p in use]
    inverse = _invert(matrix)
    out = {}
    for i in missing:
        acc = 0
        for t, p in enumerate(use):
            c = inverse[i][t]
            if c:
                acc ^= _scaled(c, available[p])
        out[i] = acc.to_bytes(size, "little")
    return out
