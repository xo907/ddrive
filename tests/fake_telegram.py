"""A stand-in for Telegram's bot API, as far as DDrive uses it (telegram.py): channels with numbered messages
that hold documents, one pinned message each, files read back by file id, and the answers Telegram gives
(not modified, not found, too many requests)."""

import email.parser
import email.policy
import json
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeTelegram:
    def __init__(self, bot_id=777, admin=True, can_delete=True):
        self.bot_id = bot_id
        self.admin, self.can_delete = admin, can_delete
        self.chats = {}             # chat id -> {"type", "title", "messages": {id: msg}, "next": n, "pinned": id or None}
        self.files = {}             # file id -> bytes
        self.calls = []             # (method, params)
        self.too_many = 0           # answer this many calls with 429 first
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add_chat(self, chat_id, title="Pieces", kind="channel"):
        self.chats[str(chat_id)] = {"type": kind, "title": title, "messages": {}, "next": 1, "pins": []}
        return str(chat_id)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def documents(self, chat_id):
        return [m for m in self.chats[str(chat_id)]["messages"].values() if "document" in m]

    # ------------------------------------------------------------ the API
    def handle(self, method, params, files):
        with self.lock:
            self.calls.append((method, params))
            if self.too_many > 0:
                self.too_many -= 1
                return 429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 0",
                             "parameters": {"retry_after": 0}}
            chat = self.chats.get(str(params.get("chat_id", "")))
            if method == "getMe":
                return self.ok({"id": self.bot_id, "is_bot": True, "username": "ddrive_test_bot"})
            if method == "getFile":
                fid = params.get("file_id", "")
                if fid not in self.files:
                    return self.bad("Bad Request: wrong file_id or the file is temporarily unavailable")
                return self.ok({"file_id": fid, "file_path": f"documents/{fid}"})
            if chat is None:
                return self.bad("Bad Request: chat not found")
            if method == "getChat":
                out = {"id": int(params["chat_id"]), "type": chat["type"], "title": chat["title"]}
                pins = [m for m in chat["pins"] if m in chat["messages"]]     # (the newest pinned one still there)
                if pins:
                    out["pinned_message"] = chat["messages"][pins[-1]]
                return self.ok(out)
            if method == "getChatMember":
                return self.ok({"status": "administrator" if self.admin else "member", "can_delete_messages": self.can_delete,
                                "user": {"id": self.bot_id}})
            if method in ("sendDocument", "sendMessage"):
                mid = chat["next"]
                chat["next"] += 1
                msg = {"message_id": mid, "date": int(time.time()), "chat": {"id": int(params["chat_id"])}}
                if method == "sendDocument":
                    name, data = files["document"]
                    fid = "BQAC" + secrets.token_urlsafe(30)            # (file ids have - and _ in them)
                    self.files[fid] = data
                    msg["document"] = {"file_id": fid, "file_unique_id": secrets.token_hex(8), "file_name": name, "file_size": len(data)}
                    if params.get("caption"):
                        msg["caption"] = params["caption"]
                else:
                    msg["text"] = params.get("text", "")
                chat["messages"][mid] = msg
                return self.ok(msg)
            if method == "editMessageReplyMarkup":
                if int(params.get("message_id", 0)) not in chat["messages"]:
                    return self.bad("Bad Request: message to edit not found")
                return self.bad("Bad Request: message is not modified: specified new message content and reply markup "
                                "are exactly the same as a current content and reply markup of the message")
            if method == "pinChatMessage":
                mid = int(params["message_id"])
                if mid not in chat["messages"]:
                    return self.bad("Bad Request: message to pin not found")
                chat["pins"] = [m for m in chat["pins"] if m != mid] + [mid]
                return self.ok(True)
            if method == "deleteMessage":
                mid = int(params["message_id"])
                if mid not in chat["messages"]:
                    return self.bad("Bad Request: message to delete not found")
                if not self.can_delete and time.time() - chat["messages"][mid]["date"] > 48 * 3600:
                    return self.bad("Bad Request: message can't be deleted")
                self._drop(chat, mid)
                return self.ok(True)
            if method == "deleteMessages":
                for mid in json.loads(params["message_ids"]):
                    if mid in chat["messages"]:
                        self._drop(chat, mid)
                return self.ok(True)
            return 404, {"ok": False, "error_code": 404, "description": "Not Found"}

    @staticmethod
    def _drop(chat, mid):
        chat["messages"].pop(mid, None)
        chat["pins"] = [m for m in chat["pins"] if m != mid]

    @staticmethod
    def ok(result):
        return 200, {"ok": True, "result": result}

    @staticmethod
    def bad(text):
        return 400, {"ok": False, "error_code": 400, "description": text}

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                parts = self.path.split("/")              # /file/bot<token>/documents/<file id>
                if len(parts) >= 5 and parts[1] == "file":
                    data = fake.files.get(parts[-1])
                    if data is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                method = self.path.rsplit("/", 1)[-1]
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                params, files = {}, {}
                ctype = self.headers.get("Content-Type", "")
                if ctype.startswith("multipart/form-data"):
                    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
                        b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
                    for part in msg.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        if part.get_filename():
                            files[name] = (part.get_filename(), part.get_payload(decode=True))
                        else:
                            params[name] = part.get_payload(decode=True).decode("utf-8")
                else:
                    params = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode("utf-8")).items()}
                status, out = fake.handle(method, params, files)
                data = json.dumps(out).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        return H
