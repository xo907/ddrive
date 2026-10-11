"""Keeps the secrets of the config file (bot tokens, encryption keys, dashboard sign-in) encrypted
on disk, so the file alone is worth nothing: a copy of it in a backup, a synced folder, a screenshot
or a support request doesn't give away the drive.

* Windows: the Data Protection API (DPAPI). Only this Windows account on this computer can decrypt.
* Linux:   AES-256-GCM with a key made from a private key file kept apart from the config
           (<data dir>/machine.key, readable by this user only), this machine's id and the user id.
           The config is useless without that file, and both together are useless on another machine.

Nothing has to be typed, so the drive still starts by itself. That also sets the limit: a program
running as the same user on the same computer can ask for the secrets the way DDrive does.
"""

import base64
import hashlib
import os
import sys


class ProtectError(Exception):
    pass


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b):
        buf = ctypes.create_string_buffer(b, len(b))
        return Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    src, _keep1 = blob(data)
    extra, _keep2 = blob(b"DiscordDrive-config")
    out = Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(out)):   # 0x1: never ask
        raise ProtectError(ctypes.FormatError(ctypes.get_last_error()).strip())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _machine_key(create: bool) -> bytes:
    from .config import default_data_dir
    path = os.path.join(default_data_dir(), "machine.key")
    try:
        with open(path, "rb") as f:
            secret = f.read()
    except FileNotFoundError:
        if not create:
            raise ProtectError(f"the key file {path} is missing") from None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        secret = os.urandom(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secret)
    except OSError as e:
        raise ProtectError(f"can't read the key file {path}: {e}") from None
    machine = b""
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(p, "rb") as f:
                machine = f.read().strip()
            if machine:
                break
        except OSError:
            pass
    uid = str(os.getuid()).encode() if hasattr(os, "getuid") else b""
    return hashlib.sha256(b"DiscordDrive-config\0" + secret + b"\0" + machine + b"\0" + uid).digest()


def _machine(data: bytes, encrypt: bool) -> bytes:
    from .crypto import CryptoEngine
    try:
        engine = CryptoEngine(_machine_key(create=encrypt))
        try:
            return engine.encrypt(data, b"config") if encrypt else engine.decrypt(data, b"config")
        finally:
            engine.close()
    except ProtectError:
        raise
    except Exception as e:
        raise ProtectError(str(e) or type(e).__name__) from None


METHOD = "dpapi" if sys.platform == "win32" else "machine"
HOW = {"dpapi": "encrypted for this Windows account on this computer",
       "machine": "encrypted for this user on this machine"}


def protect(data: bytes) -> str:
    """Encrypt for this computer and account: 'method:base64'."""
    out = _dpapi(data, True) if METHOD == "dpapi" else _machine(data, True)
    return METHOD + ":" + base64.b64encode(out).decode("ascii")


def unprotect(text: str) -> bytes:
    method, _, body = str(text).partition(":")
    try:
        raw = base64.b64decode(body)
    except ValueError:
        raise ProtectError("damaged") from None
    if method != METHOD:
        raise ProtectError("it was made on another kind of system")
    return _dpapi(raw, False) if method == "dpapi" else _machine(raw, False)
