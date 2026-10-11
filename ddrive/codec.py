"""How one piece is stored on Discord: compressed when that helps, then encrypted.

Every stored piece says how to read it back:

    DENC + nonce + tag + ciphertext   encrypted (AES-256-GCM), as in every earlier version
    DENZ + nonce + tag + ciphertext   compressed with zlib, then encrypted. The "compressed" fact
                                      is authenticated (AES-GCM associated data), so flipping the
                                      marker makes decryption fail instead of returning garbage
    DDZ1 + zlib data                  compressed, not encrypted (drives with encryption off)
    anything else                     stored as is (not encrypted, not compressed)

Compression is lossless and happens before encryption, so the encryption is exactly as strong as
before. Pieces that don't shrink (video, photos, archives, already-encrypted data) are stored
uncompressed; a quick test on a sample decides, so they cost almost nothing extra.
"""

import hashlib
import zlib

from .crypto import AuthenticationError

ENC = b"DENC"
ENC_Z = b"DENZ"
PLAIN_Z = b"DDZ1"
_AAD_Z = b"DiscordDrive:zlib"
_SAMPLE = 64 * 1024


def worth_compressing(data) -> bool:
    """Quick test on up to three 64 KiB samples (start, middle, end) with the fastest level."""
    n = len(data)
    if n < 1024:
        return False
    if n <= 3 * _SAMPLE:
        sample = data
    else:
        mid = n // 2
        sample = data[:_SAMPLE] + data[mid:mid + _SAMPLE] + data[-_SAMPLE:]
    return len(zlib.compress(sample, 1)) < len(sample) * 0.9


def encode(data: bytes, crypto=None, compress=True):
    """Returns (payload to upload, whether it was compressed)."""
    body, packed = data, False
    if compress and worth_compressing(data):
        c = zlib.compress(data, 6)
        if len(c) <= len(data) * 0.95:
            body, packed = c, True
    if crypto is not None:
        if packed:
            return ENC_Z + crypto.encrypt(body, aad=_AAD_Z)[len(ENC):], True
        return crypto.encrypt(body), False
    return (PLAIN_Z + body, True) if packed else (body, False)


def decode(payload: bytes, crypto=None, sha256: str = None) -> bytes:
    """The original bytes of a stored piece. Raises AuthenticationError when it can't be decrypted."""
    head = payload[:4]
    if head == ENC_Z:
        if crypto is None:
            raise AuthenticationError("this piece is encrypted, but encryption is off on this device")
        try:
            return zlib.decompress(crypto.decrypt(ENC + payload[4:], aad=_AAD_Z))
        except zlib.error as e:
            raise AuthenticationError(f"decrypted piece is not valid compressed data ({e})") from None
    if head == ENC:
        if crypto is None:
            raise AuthenticationError("this piece is encrypted, but encryption is off on this device")
        return crypto.decrypt(payload)
    if head == PLAIN_Z:
        try:
            data = zlib.decompress(payload[4:])
        except zlib.error:
            return payload            # an uncompressed piece that happens to start with the marker
        if sha256 and hashlib.sha256(data).hexdigest() != sha256:
            return payload
        return data
    return payload
