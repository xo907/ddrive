import itertools
import os
import random
import unittest

import helpers  # noqa: F401  (puts the project on sys.path)
from ddrive import rs


class ReedSolomonTest(unittest.TestCase):
    def check_all_losses(self, k, m):
        rnd = random.Random(k * 100 + m)
        data = [os.urandom(rnd.randint(1, 500)) for _ in range(k)]
        size = max(map(len, data))
        parity = rs.encode(data, m)
        self.assertEqual([len(p) for p in parity], [size] * m)
        every = {i: d for i, d in enumerate(data)}
        every.update({k + j: p for j, p in enumerate(parity)})
        for lost in itertools.chain.from_iterable(itertools.combinations(range(k + m), n) for n in range(1, m + 1)):
            available = {p: v for p, v in every.items() if p not in lost}
            out = rs.decode(k, m, available, size)
            for i in lost:
                if i < k:
                    self.assertEqual(out[i][:len(data[i])], data[i], f"k={k} m={m} lost={lost}")

    def test_every_combination_of_losses(self):
        for k, m in [(1, 1), (1, 2), (3, 1), (4, 2), (10, 2), (7, 3)]:
            self.check_all_losses(k, m)

    def test_too_many_losses(self):
        data = [os.urandom(100) for _ in range(5)]
        parity = rs.encode(data, 2)
        available = {0: data[0], 1: data[1], 5: parity[0]}
        with self.assertRaises(ValueError):
            rs.decode(5, 2, available, 100)

    def test_encoder_streams(self):
        data = [os.urandom(1000), os.urandom(700), os.urandom(1000)]
        enc = rs.Encoder(3, 2)
        for i, d in enumerate(data):
            enc.add(i, d)
        self.assertEqual(enc.parity(), rs.encode(data, 2))

    def test_field(self):
        for a in range(1, 256):
            self.assertEqual(rs.mul(a, rs.inv(a)), 1)


if __name__ == "__main__":
    unittest.main()
