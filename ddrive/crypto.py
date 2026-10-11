"""Hardware-accelerated zero-knowledge AES-256-GCM encryption.

Supports:
- Windows: Native Windows Cryptography API: Next Generation (bcrypt.dll) with hardware AES-NI.
- Linux/macOS: Python cryptography package (python3-cryptography / pip install cryptography).

Both produce the same format, so data written on one platform reads on the other.
"""

import ctypes
import hashlib
import logging
import os
import secrets
import sys

log = logging.getLogger("ddrive.crypto")

MAGIC = b"DENC"  # DDrive Encrypted
NONCE_SIZE = 12
TAG_SIZE = 16
KEY_SIZE = 32     # 256 bits


class CryptoError(Exception):
    pass


class AuthenticationError(CryptoError):
    pass


def derive_key(passphrase: str, salt: bytes = None, iterations: int = 200_000) -> tuple[bytes, bytes]:
    """Derives a 256-bit AES key from a passphrase using PBKDF2-HMAC-SHA256."""
    if salt is None:
        salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=KEY_SIZE)
    return key, salt


SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1    # 128 MiB of memory per guess


def derive_key_scrypt(passphrase: str, salt: bytes) -> bytes:
    """Derives a 256-bit key with scrypt, which is memory-hard: each password guess costs an
    attacker 128 MiB of memory as well as time, so guessing on GPUs is far slower than with PBKDF2."""
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                          maxmem=256 * 2 ** 20, dklen=KEY_SIZE)


def password_keys(passphrase: str, channel_id: str):
    """Keys a password can stand for, newest method first: [(method, key)].

    Drives set up with this version use scrypt; drives set up before it used PBKDF2. Setup tries
    each against the drive in the channel and keeps the one that opens it."""
    salt = channel_salt(channel_id)
    out = []
    if hasattr(hashlib, "scrypt"):
        try:
            out.append(("scrypt", derive_key_scrypt(passphrase, salt)))
        except (ValueError, MemoryError) as e:     # not enough free memory for 128 MiB right now
            log.warning("Could not use scrypt (%s); using PBKDF2 for this password.", e)
    out.append(("pbkdf2", derive_key(passphrase, salt)[0]))
    return out


def hash_password(password: str) -> str:
    """A salted scrypt hash for storing a sign-in password (never the password itself)."""
    import base64
    salt = os.urandom(16)
    n, r, p = 2 ** 14, 8, 1
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 2 ** 20, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return f"scrypt${n}${r}${p}${b64(salt)}${b64(h)}"


def check_password(password: str, stored: str) -> bool:
    import base64
    import hmac
    try:
        kind, n, r, p, salt, want = (stored or "").split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 2 ** 20, dklen=32)
        return hmac.compare_digest(got, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


def channel_salt(channel_id: str) -> bytes:
    """Deterministic per-channel salt, so the same passphrase yields the same key on every machine."""
    return hashlib.sha256(f"DiscordDrive:{channel_id}".encode("utf-8")).digest()[:16]


def generate_key() -> bytes:
    """Generates a cryptographically secure 256-bit key."""
    return secrets.token_bytes(KEY_SIZE)


def parse_key(key_hex: str) -> bytes:
    """A 256-bit key from what a person gives: its 24-word recovery phrase, or 64 hex characters."""
    from . import words
    if words.looks_like_phrase(key_hex):
        return words.phrase_to_key(key_hex)
    try:
        key = bytes.fromhex((key_hex or "").strip())
    except ValueError:
        raise ValueError("encryption key is not valid hex") from None
    if len(key) != KEY_SIZE:
        raise ValueError(f"encryption key must be {KEY_SIZE * 2} hex characters, got {len(key) * 2}")
    return key


def key_fingerprint(key_hex: str) -> str:
    """Short, non-secret identifier for a key (safe to print or log)."""
    return hashlib.sha256(b"DiscordDrive-fp:" + bytes.fromhex(key_hex)).hexdigest()[:12]


_USE_BCRYPT = sys.platform == "win32" and hasattr(ctypes, "windll")

if _USE_BCRYPT:
    from ctypes import wintypes
    bcrypt = ctypes.windll.bcrypt

    class BCRYPT_AUTH_MODE_INFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.ULONG),
            ("dwInfoVersion", wintypes.ULONG),
            ("pbNonce", ctypes.c_char_p),
            ("cbNonce", wintypes.ULONG),
            ("pbAuthData", ctypes.c_char_p),
            ("cbAuthData", wintypes.ULONG),
            ("pbTag", ctypes.c_char_p),
            ("cbTag", wintypes.ULONG),
            ("pbMacContext", ctypes.c_char_p),
            ("cbMacContext", wintypes.ULONG),
            ("cbAAD", wintypes.ULONG),
            ("cbData", wintypes.ULARGE_INTEGER),
            ("dwFlags", wintypes.ULONG),
        ]
else:
    bcrypt = None


class CryptoEngine:
    def __init__(self, key: bytes):
        if len(key) != KEY_SIZE:
            raise ValueError(f"Key must be exactly {KEY_SIZE} bytes (256 bits), got {len(key)}")
        self.key = key
        self._aesgcm = None

        if _USE_BCRYPT:
            self._hAlg = wintypes.HANDLE()
            self._hKey = wintypes.HANDLE()
            self._init_bcrypt()
        else:
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                self._aesgcm = AESGCM(self.key)
            except ImportError:
                raise ImportError(
                    "AES-256-GCM encryption on this platform requires the 'cryptography' package. "
                    "Install it via: sudo apt install python3-cryptography  (or: pip install cryptography)"
                ) from None

    def _init_bcrypt(self):
        st = bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(self._hAlg), "AES", None, 0)
        if st != 0:
            raise CryptoError(f"BCryptOpenAlgorithmProvider failed with status {hex(st)}")

        chain_mode = "ChainingModeGCM"
        st = bcrypt.BCryptSetProperty(
            self._hAlg, "ChainingMode", chain_mode, (len(chain_mode) + 1) * 2, 0
        )
        if st != 0:
            bcrypt.BCryptCloseAlgorithmProvider(self._hAlg, 0)
            raise CryptoError(f"BCryptSetProperty(ChainingModeGCM) failed with status {hex(st)}")

        st = bcrypt.BCryptGenerateSymmetricKey(
            self._hAlg, ctypes.byref(self._hKey), None, 0, self.key, len(self.key), 0
        )
        if st != 0:
            bcrypt.BCryptCloseAlgorithmProvider(self._hAlg, 0)
            raise CryptoError(f"BCryptGenerateSymmetricKey failed with status {hex(st)}")

    def close(self):
        if _USE_BCRYPT:
            if getattr(self, "_hKey", None):
                bcrypt.BCryptDestroyKey(self._hKey)
                self._hKey = wintypes.HANDLE()
            if getattr(self, "_hAlg", None):
                bcrypt.BCryptCloseAlgorithmProvider(self._hAlg, 0)
                self._hAlg = wintypes.HANDLE()

    def __del__(self):
        self.close()

    def encrypt(self, data: bytes, aad: bytes = b"") -> bytes:
        """Encrypts data with AES-256-GCM.
        Returns: [MAGIC (4B)] + [NONCE (12B)] + [TAG (16B)] + [CIPHERTEXT]
        """
        if self._aesgcm is not None:
            nonce = os.urandom(NONCE_SIZE)
            ct_and_tag = self._aesgcm.encrypt(nonce, data, aad or None)
            ciphertext = ct_and_tag[:-TAG_SIZE]
            tag = ct_and_tag[-TAG_SIZE:]
            return MAGIC + nonce + tag + ciphertext

        nonce = os.urandom(NONCE_SIZE)
        tag = ctypes.create_string_buffer(TAG_SIZE)
        ciphertext = ctypes.create_string_buffer(len(data))
        cb_result = wintypes.ULONG()

        auth_info = BCRYPT_AUTH_MODE_INFO()
        auth_info.cbSize = ctypes.sizeof(BCRYPT_AUTH_MODE_INFO)
        auth_info.dwInfoVersion = 1
        auth_info.pbNonce = nonce
        auth_info.cbNonce = NONCE_SIZE
        auth_info.pbTag = ctypes.cast(tag, ctypes.c_char_p)
        auth_info.cbTag = TAG_SIZE
        if aad:
            auth_info.pbAuthData = aad
            auth_info.cbAuthData = len(aad)

        st = bcrypt.BCryptEncrypt(
            self._hKey,
            data,
            len(data),
            ctypes.byref(auth_info),
            None,
            0,
            ciphertext,
            len(ciphertext),
            ctypes.byref(cb_result),
            0,
        )
        if st != 0:
            raise CryptoError(f"AES-GCM encryption failed with status {hex(st)}")

        return MAGIC + nonce + tag.raw + ciphertext.raw[:cb_result.value]

    def decrypt(self, payload: bytes, aad: bytes = b"") -> bytes:
        """Decrypts and authenticates payload with AES-256-GCM.
        Raises AuthenticationError if corrupted or wrong key.
        """
        min_len = len(MAGIC) + NONCE_SIZE + TAG_SIZE
        if len(payload) < min_len:
            raise AuthenticationError("Payload is too short to be a valid encrypted block")

        if payload[:len(MAGIC)] != MAGIC:
            raise AuthenticationError("Invalid magic header; data is not encrypted or corrupted")

        offset = len(MAGIC)
        nonce = payload[offset : offset + NONCE_SIZE]
        offset += NONCE_SIZE
        tag = payload[offset : offset + TAG_SIZE]
        offset += TAG_SIZE
        ciphertext = payload[offset:]

        if self._aesgcm is not None:
            try:
                return self._aesgcm.decrypt(nonce, ciphertext + tag, aad or None)
            except Exception as e:
                raise AuthenticationError(
                    "Decryption / authentication failed. Wrong encryption key or corrupted data!"
                ) from e

        plaintext = ctypes.create_string_buffer(len(ciphertext))
        cb_result = wintypes.ULONG()

        auth_info = BCRYPT_AUTH_MODE_INFO()
        auth_info.cbSize = ctypes.sizeof(BCRYPT_AUTH_MODE_INFO)
        auth_info.dwInfoVersion = 1
        auth_info.pbNonce = nonce
        auth_info.cbNonce = NONCE_SIZE
        auth_info.pbTag = tag
        auth_info.cbTag = TAG_SIZE
        if aad:
            auth_info.pbAuthData = aad
            auth_info.cbAuthData = len(aad)

        st = bcrypt.BCryptDecrypt(
            self._hKey,
            ciphertext,
            len(ciphertext),
            ctypes.byref(auth_info),
            None,
            0,
            plaintext,
            len(plaintext),
            ctypes.byref(cb_result),
            0,
        )
        if st != 0:
            raise AuthenticationError(
                f"Decryption / authentication failed (status {hex(st if st >= 0 else (1 << 32) + st)}). "
                "Wrong encryption key or corrupted data!"
            )

        return plaintext.raw[:cb_result.value]


class KeyRing:
    """Encrypts with the current key; decrypts with whichever known key works.

    Keeping older keys means data written before a key change stays readable.
    """

    def __init__(self, primary: bytes, older=()):
        self._engines = [CryptoEngine(primary)]
        for k in older:
            self.add(k)

    @classmethod
    def from_hex(cls, primary_hex: str, older_hex=()):
        older = []
        for h in older_hex or ():
            try:
                older.append(parse_key(h))
            except ValueError as e:
                log.warning("Ignoring an invalid older encryption key: %s", e)
        return cls(parse_key(primary_hex), older)

    @property
    def key(self) -> bytes:
        return self._engines[0].key

    def keys(self):
        return [e.key for e in self._engines]

    def add(self, key: bytes) -> bool:
        """Add an older key for decryption. Returns False if it was already known."""
        if any(e.key == key for e in self._engines):
            return False
        self._engines.append(CryptoEngine(key))
        return True

    def encrypt(self, data: bytes, aad: bytes = b"") -> bytes:
        return self._engines[0].encrypt(data, aad)

    def decrypt(self, payload: bytes, aad: bytes = b"") -> bytes:
        err = None
        for e in list(self._engines):
            try:
                return e.decrypt(payload, aad)
            except AuthenticationError as x:
                err = x
        raise err

    def close(self):
        for e in self._engines:
            e.close()
