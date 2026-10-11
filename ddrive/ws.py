"""A small WebSocket client (standard library only): text messages over ws:// or wss://.

Just what the message logger needs to listen to Discord: one connection, read by one thread.
`recv` may time out without losing anything (a frame is only taken off the buffer once it is whole).
"""

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
import urllib.parse

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 64 * 1024 * 1024


class Closed(Exception):
    """The other side closed the connection (`code` is its close code, 1006 when it just dropped)."""

    def __init__(self, code=1006, reason=""):
        super().__init__(f"connection closed ({code}{': ' + reason if reason else ''})")
        self.code = code


class WebSocket:
    def __init__(self, url, timeout=30.0, headers=None):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("ws", "wss") or not u.hostname:
            raise ValueError(f"not a WebSocket address: {url}")
        port = u.port or (443 if u.scheme == "wss" else 80)
        sock = socket.create_connection((u.hostname, port), timeout=timeout)
        if u.scheme == "wss":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
        self._sock = sock
        self._buf = bytearray()
        self._parts = []                  # a message that arrives in several frames
        self._send_lock = threading.Lock()
        self.closed = False
        key = base64.b64encode(os.urandom(16))
        path = (u.path or "/") + ("?" + u.query if u.query else "")
        lines = [f"GET {path} HTTP/1.1", f"Host: {u.hostname}" + ("" if port in (80, 443) else f":{port}"),
                 "Upgrade: websocket", "Connection: Upgrade", "Sec-WebSocket-Version: 13",
                 "Sec-WebSocket-Key: " + key.decode()]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        while b"\r\n\r\n" not in self._buf:
            data = sock.recv(4096)
            if not data:
                raise Closed(1006, "no answer to the handshake")
            self._buf += data
        head, _, rest = bytes(self._buf).partition(b"\r\n\r\n")
        self._buf = bytearray(rest)
        status = head.split(b"\r\n", 1)[0].decode("latin-1")
        answers = {}
        for line in head.split(b"\r\n")[1:]:
            k, _, v = line.decode("latin-1").partition(":")
            answers[k.strip().lower()] = v.strip()
        accept = base64.b64encode(hashlib.sha1(key + _GUID).digest()).decode()
        if " 101" not in status or answers.get("sec-websocket-accept") != accept:
            sock.close()
            raise Closed(1006, f"the server refused the connection ({status})")

    # ------------------------------------------------------------ sending
    def _frame(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload)) if n < 4096 else _mask(payload, mask)
        with self._send_lock:
            self._sock.sendall(head + mask + masked)

    def send(self, text):
        self._frame(0x1, text.encode("utf-8"))

    def close(self, code=1000):
        if self.closed:
            return
        self.closed = True
        try:
            self._frame(0x8, struct.pack("!H", code))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------ receiving
    def _take(self):
        """One whole frame off the buffer: (fin, opcode, payload), or None when more data is needed."""
        buf = self._buf
        if len(buf) < 2:
            return None
        n = buf[1] & 0x7F
        pos = 2
        if n == 126:
            if len(buf) < 4:
                return None
            n = struct.unpack_from("!H", buf, 2)[0]
            pos = 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n = struct.unpack_from("!Q", buf, 2)[0]
            pos = 10
        if n > MAX_MESSAGE:
            raise Closed(1009, "message too large")
        masked = bool(buf[1] & 0x80)
        if len(buf) < pos + (4 if masked else 0) + n:
            return None
        if masked:
            payload = _mask(bytes(buf[pos + 4:pos + 4 + n]), bytes(buf[pos:pos + 4]))
            pos += 4
        else:
            payload = bytes(buf[pos:pos + n])
        fin, opcode = bool(buf[0] & 0x80), buf[0] & 0x0F
        del buf[:pos + n]
        return fin, opcode, payload

    def recv(self, timeout=None):
        """The next text message, or None when nothing arrived within `timeout` seconds."""
        self._sock.settimeout(timeout)
        while True:
            frame = self._take()
            if frame is None:
                try:
                    data = self._sock.recv(65536)
                except socket.timeout:
                    return None
                except OSError as e:
                    raise Closed(1006, str(e)) from None
                if not data:
                    raise Closed(1006)
                self._buf += data
                continue
            fin, opcode, payload = frame
            if opcode == 0x8:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                self.closed = True
                try:
                    self._sock.close()
                except OSError:
                    pass
                raise Closed(code, payload[2:].decode("utf-8", "replace"))
            if opcode == 0x9:
                self._frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            self._parts.append(payload)
            if sum(map(len, self._parts)) > MAX_MESSAGE:
                raise Closed(1009, "message too large")
            if fin:
                data, self._parts = b"".join(self._parts), []
                return data.decode("utf-8", "replace")


def _mask(data, mask):
    n = len(data)
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big") if n else b""
