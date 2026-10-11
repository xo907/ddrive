import hashlib
import os
import time
import unittest

import helpers
from ddrive import codec
from ddrive.crypto import AuthenticationError, KeyRing, generate_key, password_keys


class CodecTest(unittest.TestCase):
    def setUp(self):
        self.ring = KeyRing.from_hex(helpers.KEY)

    def tearDown(self):
        self.ring.close()

    def roundtrip(self, data, crypto):
        payload, packed = codec.encode(data, crypto)
        self.assertEqual(codec.decode(payload, crypto, hashlib.sha256(data).hexdigest()), data)
        return payload, packed

    def test_compressible_encrypted(self):
        data = b"hello world, " * 10000
        payload, packed = self.roundtrip(data, self.ring)
        self.assertTrue(packed)
        self.assertTrue(payload.startswith(codec.ENC_Z))
        self.assertLess(len(payload), len(data) // 10)

    def test_random_data_is_not_compressed(self):
        data = os.urandom(200_000)
        payload, packed = self.roundtrip(data, self.ring)
        self.assertFalse(packed)
        self.assertTrue(payload.startswith(codec.ENC))

    def test_unencrypted(self):
        self.roundtrip(b"abc" * 5000, None)
        self.roundtrip(os.urandom(5000), None)
        raw = codec.PLAIN_Z + b"not really compressed"
        self.assertEqual(codec.decode(raw, None, hashlib.sha256(raw).hexdigest()), raw)

    def test_flipped_marker_is_detected(self):
        payload, _ = codec.encode(b"x" * 50_000, self.ring)
        with self.assertRaises(AuthenticationError):
            codec.decode(codec.ENC + payload[4:], self.ring)
        plain, _ = codec.encode(os.urandom(5000), self.ring)
        with self.assertRaises(AuthenticationError):
            codec.decode(codec.ENC_Z + plain[4:], self.ring)

    def test_pieces_from_older_versions_still_read(self):
        data = b"old piece " * 1000
        legacy = self.ring.encrypt(data)        # what versions before compression stored
        self.assertEqual(codec.decode(legacy, self.ring), data)

    def test_needs_key(self):
        payload, _ = codec.encode(b"secret", self.ring)
        with self.assertRaises(AuthenticationError):
            codec.decode(payload, None)

    def test_old_key_still_reads(self):
        old = generate_key()
        payload = KeyRing(old).encrypt(b"from before")
        ring = KeyRing(generate_key(), [old])
        self.assertEqual(codec.decode(payload, ring), b"from before")


class PasswordTest(unittest.TestCase):
    def test_password_keys(self):
        t = time.time()
        a = password_keys("correct horse", "123")
        b = password_keys("correct horse", "123")
        self.assertEqual(a, b)                                   # same on every device
        self.assertEqual([m for m, _ in a], ["scrypt", "pbkdf2"])
        self.assertNotEqual(a[0][1], a[1][1])
        self.assertNotEqual(password_keys("correct horse", "456")[0][1], a[0][1])   # salted per channel
        self.assertLess(time.time() - t, 30)


if __name__ == "__main__":
    unittest.main()
