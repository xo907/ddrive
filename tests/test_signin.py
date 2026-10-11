"""Signing in: the recovery phrase and passkeys (with a pretend authenticator that signs the way a
real one does). There are no names and passwords."""

import hashlib
import http.client
import json
import os
import struct
import unittest
import urllib.parse

import helpers
from discorddrive import passkeys as pk
from discorddrive import words
from discorddrive.crypto import parse_key


# ------------------------------------------------------------------ a pretend authenticator
def cbor_encode(v):
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
    if isinstance(v, bool):
        return bytes([0xF5 if v else 0xF4])
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        return head(3, len(v.encode())) + v.encode()
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor_encode(k) + cbor_encode(x) for k, x in v.items())
    raise TypeError(v)


def der(r, s):
    def integer(n):
        b = n.to_bytes((n.bit_length() + 8) // 8, "big")
        return b"\x02" + bytes([len(b)]) + b
    body = integer(r) + integer(s)
    return b"\x30" + bytes([len(body)]) + body


class Authenticator:
    """Holds one ES256 passkey for one address."""

    def __init__(self, rp, origin):
        self.rp, self.origin = rp, origin
        self.d = int.from_bytes(os.urandom(32), "big") % (pk.N - 1) + 1
        self.x, self.y = pk._mul(self.d, (pk.GX, pk.GY))
        self.id = os.urandom(20)
        self.count = 0

    def _auth_data(self, attested=False):
        self.count += 1
        data = hashlib.sha256(self.rp.encode()).digest() + bytes([0x45 if attested else 0x05]) + struct.pack(">I", self.count)
        if attested:
            key = {1: 2, 3: -7, -1: 1, -2: self.x.to_bytes(32, "big"), -3: self.y.to_bytes(32, "big")}
            data += bytes(16) + struct.pack(">H", len(self.id)) + self.id + cbor_encode(key)
        return data

    def _client(self, kind, challenge, origin=None):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin}).encode()

    def create(self, challenge):
        return {"clientDataJSON": pk.b64u(self._client("webauthn.create", challenge)),
                "attestationObject": pk.b64u(cbor_encode({"fmt": "none", "attStmt": {}, "authData": self._auth_data(True)}))}

    def get(self, challenge, origin=None, tamper=False):
        auth, client = self._auth_data(), self._client("webauthn.get", challenge, origin)
        e = int.from_bytes(hashlib.sha256(auth + hashlib.sha256(client).digest()).digest(), "big")
        k = int.from_bytes(os.urandom(32), "big") % (pk.N - 1) + 1
        r = pk._mul(k, (pk.GX, pk.GY))[0] % pk.N
        s = pow(k, -1, pk.N) * (e + r * self.d) % pk.N
        if tamper:
            s = (s + 1) % pk.N
        return {"id": pk.b64u(self.id), "clientDataJSON": pk.b64u(client), "authenticatorData": pk.b64u(auth),
                "signature": pk.b64u(der(r, s))}


class PhraseTest(unittest.TestCase):
    def test_phrase_is_the_key_in_words(self):
        self.assertEqual(words.key_to_phrase(bytes(32)), " ".join(["abandon"] * 23 + ["art"]))        # the standard's own examples
        self.assertEqual(words.key_to_phrase(b"\xff" * 32), " ".join(["zoo"] * 23 + ["vote"]))
        key = os.urandom(32)
        phrase = words.key_to_phrase(key)
        self.assertEqual(len(phrase.split()), 24)
        self.assertEqual(words.phrase_to_key("  " + phrase.upper().replace(" ", ",\n ")), key)        # however it is typed
        self.assertEqual(parse_key(phrase), key)
        self.assertEqual(parse_key(key.hex()), key)
        swapped = phrase.split()
        swapped[0], swapped[1] = swapped[1], swapped[0]
        if swapped != phrase.split():
            with self.assertRaisesRegex(ValueError, "don't fit together"):
                words.phrase_to_key(" ".join(swapped))
        with self.assertRaisesRegex(ValueError, "24 words"):
            words.phrase_to_key("abandon ability able")
        with self.assertRaisesRegex(ValueError, "word 2, 'abilty'.*did you mean 'ability'"):
            words.phrase_to_key("abandon abilty " + "abandon " * 22)

    def test_signature_checks(self):
        self.assertTrue(pk.on_curve(pk.GX, pk.GY))
        self.assertIsNone(pk._mul(pk.N, (pk.GX, pk.GY)))
        a = Authenticator("localhost", "http://localhost")
        got = a.get("c")
        signed = pk.unb64u(got["authenticatorData"]) + hashlib.sha256(pk.unb64u(got["clientDataJSON"])).digest()
        self.assertTrue(pk.verify_es256(a.x, a.y, signed, pk.unb64u(got["signature"])))
        self.assertFalse(pk.verify_es256(a.x, a.y, signed + b"x", pk.unb64u(got["signature"])))
        self.assertFalse(pk.verify_es256(a.x, a.y, signed, der(0, 5)))
        # RSA, as Windows Hello uses: a small textbook key pair is enough to check the padding rules
        p, q, e = (1 << 1279) - 1, (1 << 2203) - 1, 65537                                              # two known primes
        n, d = p * q, pow(e, -1, (p - 1) * (q - 1))
        k = (n.bit_length() + 7) // 8
        em = b"\x00\x01" + b"\xff" * (k - 3 - 19 - 32) + b"\x00" + pk._SHA256_INFO + hashlib.sha256(b"hello").digest()
        sig = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
        self.assertTrue(pk.verify_rs256(n, e, b"hello", sig))
        self.assertFalse(pk.verify_rs256(n, e, b"hellp", sig))
        self.assertEqual(pk.cbor(cbor_encode({"a": b"x", 3: -7, "t": True}))[0], {"a": b"x", 3: -7, "t": True})


class SignInTest(helpers.DriveTest):
    def setUp(self):
        super().setUp()
        self.d = self.drive(web_token="secret-token")
        self.web = self.d.start_web(port=0)
        self.cookie = None
        self.host = f"localhost:{self.web.port}"
        self.phrase = words.key_to_phrase(bytes.fromhex(self.d.cfg.encryption_key))

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def req(self, method, path, body=None, host=None, cookie=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)
        h = {"Host": host or self.host}
        if cookie and self.cookie:
            h["Cookie"] = self.cookie
        if method == "POST":
            h["X-DD"] = "1"
            if isinstance(body, str):
                body, h["Content-Type"] = body.encode(), "application/x-www-form-urlencoded"
            else:
                body, h["Content-Type"] = json.dumps(body or {}).encode(), "application/json"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        if r.status in (200, 303) and r.getheader("Set-Cookie"):
            self.cookie = r.getheader("Set-Cookie").split(";")[0]
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def sign_in_with_phrase(self, phrase=None):
        return self.req("POST", "/login", urllib.parse.urlencode({"phrase": phrase or self.phrase}), cookie=False)

    def test_recovery_phrase_signs_in(self):
        self.web.save_cfg(web_user="Dennis", web_password="", web_passkeys=[])
        status, page = self.req("GET", "/")
        self.assertIn(b'name="phrase"', page)
        self.assertNotIn(b'name="password"', page)                       # there is no such thing
        self.assertEqual(self.req("GET", "/api/status")[0], 401)
        other = words.key_to_phrase(os.urandom(32))
        status, page = self.sign_in_with_phrase(other)
        self.assertEqual(status, 401)
        self.assertIn(b"nobody here has that phrase", page)
        self.assertIn(b"word 1", self.sign_in_with_phrase("zzz " + " ".join(self.phrase.split()[1:]))[1])
        self.assertEqual(self.sign_in_with_phrase()[0], 303)
        status, st = self.req("GET", "/api/status")
        self.assertEqual((status, st["user"]), (200, "Dennis"))
        info = self.req("GET", "/api/signin")[1]
        self.assertEqual((info["encrypted"], info["dav_password"], info["passkeys"], info["rp"], info["can_passkey"]),
                         (True, False, [], "localhost", True))

    def test_phrase_is_shown_only_on_the_computer_itself(self):
        self.web.save_cfg(web_user="Dennis")
        self.sign_in_with_phrase()
        self.d.cfg.web_hosts = ["drive.example.com"]
        self.assertEqual(self.req("POST", "/api/signin/phrase", host="drive.example.com")[0], 403)
        status, r = self.req("POST", "/api/signin/phrase", host=f"127.0.0.1:{self.web.port}")
        self.assertEqual((status, r["phrase"]), (200, self.phrase))

    def test_passkey(self):
        self.web.save_cfg(web_user="Dennis")
        self.assertEqual(self.req("POST", "/passkey/options", cookie=False)[0], 404)          # none yet
        self.sign_in_with_phrase()
        key = Authenticator("localhost", f"http://{self.host}")
        status, opts = self.req("POST", "/api/signin/passkey/options")
        self.assertEqual((status, opts["rp"]["id"], opts["user"]["name"]), (200, "localhost", "Dennis"))
        status, r = self.req("POST", "/api/signin/passkey/register", dict(key.create(opts["challenge"]), name="Laptop fingerprint"))
        self.assertEqual(status, 200, r)
        again = self.req("POST", "/api/signin/passkey/options")[1]
        self.assertEqual(again["exclude"], [pk.b64u(key.id)])
        self.assertEqual(self.req("POST", "/api/signin/passkey/register", key.create(opts["challenge"]))[0], 400)   # a challenge works once
        info = self.req("GET", "/api/signin")[1]
        self.assertEqual([(p["name"], p["rp"], p["here"]) for p in info["passkeys"]], [("Laptop fingerprint", "localhost", True)])

        # a new browser signs in with it
        self.cookie = None
        self.assertIn(b"Sign in with a passkey", self.req("GET", "/")[1])
        status, opts = self.req("POST", "/passkey/options")
        self.assertEqual((status, opts["allow"]), (200, [pk.b64u(key.id)]))
        self.assertEqual(self.req("POST", "/passkey/login", key.get(opts["challenge"], tamper=True))[0], 401)
        opts = self.req("POST", "/passkey/options")[1]
        self.assertEqual(self.req("POST", "/passkey/login", key.get(opts["challenge"], origin="https://evil.example"))[0], 401)
        opts = self.req("POST", "/passkey/options")[1]
        self.assertEqual(self.req("POST", "/passkey/login", key.get("made-up-challenge"))[0], 401)
        self.assertIsNone(self.cookie)
        opts = self.req("POST", "/passkey/options")[1]
        answer = key.get(opts["challenge"])
        self.assertEqual(self.req("POST", "/passkey/login", answer)[0], 200)
        self.assertEqual(self.req("GET", "/api/status")[1]["user"], "Dennis")
        self.cookie = None
        self.assertEqual(self.req("POST", "/passkey/login", answer)[0], 401)                 # an answer can't be replayed

        # it belongs to this address: another one doesn't offer it
        self.d.cfg.web_hosts = ["drive.example.com"]
        self.assertEqual(self.req("POST", "/passkey/options", host="drive.example.com")[0], 404)
        self.assertNotIn(b"passkey", self.req("GET", "/", host="drive.example.com")[1])
        # and bare addresses can't have passkeys at all
        self.sign_in_with_phrase()
        self.assertEqual(self.req("POST", "/api/signin/passkey/options", host=f"127.0.0.1:{self.web.port}")[0], 400)
        self.assertEqual(self.req("POST", "/api/signin/passkey/remove", {"id": pk.b64u(key.id)})[0], 200)
        self.assertEqual(self.req("GET", "/api/signin")[1]["passkeys"], [])

    def test_requests_share_a_connection(self):
        # browsers send the passkey's two requests (and everything else) over one kept-open connection;
        # a body that a request didn't need must not spill into the next one
        self.web.save_cfg(web_user="Dennis")
        self.sign_in_with_phrase()
        key = Authenticator("localhost", f"http://{self.host}")
        conn = http.client.HTTPConnection("127.0.0.1", self.web.port, timeout=10)

        def post(path, body, cookie=True):
            h = {"Host": self.host, "X-DD": "1", "Content-Type": "application/json"}
            if cookie:
                h["Cookie"] = self.cookie
            conn.request("POST", path, body=json.dumps(body).encode(), headers=h)
            r = conn.getresponse()
            data = r.read()
            self.assertIn("json", r.getheader("Content-Type"), data[:80])
            return r.status, json.loads(data), r.getheader("Set-Cookie")

        try:
            _, opts, _ = post("/api/signin/passkey/options", {})
            self.assertEqual(post("/api/signin/passkey/register", key.create(opts["challenge"]))[0], 200)
            self.assertEqual(post("/api/locks/lock", {"unused": "x" * 500})[0], 200)
            self.assertEqual(post("/api/logout", {})[0], 200)
            status, opts, _ = post("/passkey/options", {}, cookie=False)                 # as the sign-in page does
            self.assertEqual(status, 200)
            status, _, cookie = post("/passkey/login", key.get(opts["challenge"]), cookie=False)
            self.assertEqual(status, 200)
            self.assertIn("dd_session=", cookie)
        finally:
            conn.close()

    def test_a_password_is_only_for_other_apps(self):
        self.sign_in_with_phrase()
        self.assertEqual(self.req("POST", "/api/webdav/password", {"user": "dennis", "password": "short"})[0], 400)
        self.assertEqual(self.req("POST", "/api/webdav/password", {"user": "dennis", "password": "correct horse"})[0], 200)
        self.assertEqual(self.req("GET", "/api/status")[0], 200)                              # this browser stays in
        info = self.req("GET", "/api/signin")[1]
        self.assertEqual((info["dav_user"], info["dav_password"]), ("dennis", True))
        form = urllib.parse.urlencode({"user": "dennis", "password": "correct horse"})
        self.assertEqual(self.req("POST", "/login", form, cookie=False)[0], 401)              # it never opens the dashboard
        self.assertNotIn(b'name="password"', self.req("GET", "/", cookie=False)[1])
        self.assertEqual(self.req("POST", "/api/webdav/password", {"password": ""})[0], 200)  # remove it
        self.assertFalse(self.req("GET", "/api/signin")[1]["dav_password"])

    def test_a_drive_without_encryption_still_has_words(self):
        self.d.cfg.encryption_enabled = False                                                 # no key to be the phrase
        self.assertEqual(self.sign_in_with_phrase()[0], 401)
        local = f"127.0.0.1:{self.web.port}"
        made = words.key_to_phrase(bytes.fromhex(self.web.owner_key()))
        self.assertNotEqual(made, self.phrase)
        self.assertEqual(self.sign_in_with_phrase(made)[0], 303)
        self.assertEqual(self.req("POST", "/api/signin/phrase", host=local)[1]["phrase"], made)

    def test_the_first_visit_asks_for_the_phrase_too(self):
        local = f"127.0.0.1:{self.web.port}"
        page = self.req("GET", "/", host=local)[1]
        self.assertIn(b'name="phrase"', page)
        self.assertIn(b"recovery-phrase", page)                                               # where the owner finds the words
        self.d.cfg.web_hosts = ["drive.example.com"]
        self.assertNotIn(b"recovery-phrase", self.req("GET", "/", host="drive.example.com")[1])   # not for other computers
        self.assertEqual(self.req("POST", "/setup", urllib.parse.urlencode({"user": "Mallory"}), host=local)[0], 401)
        self.assertEqual(self.req("GET", "/api/status", host=local)[0], 401)


if __name__ == "__main__":
    unittest.main()
