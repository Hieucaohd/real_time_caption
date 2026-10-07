from __future__ import annotations

import json
import mimetypes
import re
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .service import Service
from .store import DATA_DIR

STATIC = Path(__file__).resolve().parent / "static"


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service: Service):
        self.service = service
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: WebServer

    def log_message(self, fmt, *args):
        message = re.sub(r"token=[^& ]+", "token=<hidden>", fmt % args)
        print(f"[web] {self.address_string()} {message}")

    def json_body(self) -> dict:
        size = min(int(self.headers.get("Content-Length", 0)), 2_000_000)
        return json.loads(self.rfile.read(size) or b"{}")

    def send_json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def auth(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        token = self.headers.get("X-Web-Token") or query.get("token", [""])[0]
        if secrets_compare(token, self.server.service.settings.token):
            return True
        self.send_json({"error": "Invalid access token"}, 401)
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self.auth():
                return
            try:
                self.api_get(parsed.path)
            except (ValueError, KeyError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/ca.crt":
            target = DATA_DIR / "certs" / "RealTimeCaption-Web-CA.crt"
        else:
            path = "index.html" if parsed.path == "/" else parsed.path.removeprefix("/static/")
            target = (STATIC / path).resolve()
        allowed = STATIC.resolve() in target.resolve().parents or DATA_DIR.resolve() in target.resolve().parents
        if not allowed or not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def api_get(self, path: str):
        service = self.server.service
        if path == "/api/bootstrap":
            self.send_json({"conversations": [x.json() for x in service.store.list()],
                            "settings": settings_json(service), "capture": service.state()})
        elif path.startswith("/api/conversations/"):
            self.send_json(service.detail(path.rsplit("/", 1)[1]))
        elif path.startswith("/api/tasks/"):
            task = service.tasks.get(path.rsplit("/", 1)[1])
            if not task:
                raise ValueError("Task not found")
            self.send_json(task)
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/") or not self.auth():
            return
        try:
            self.api_post(path)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def api_post(self, path: str):
        service = self.server.service
        if path == "/api/audio":
            size = min(int(self.headers.get("Content-Length", 0)), 1_000_000)
            service.audio(self.headers.get("X-Conversation-Id", ""), self.rfile.read(size))
            self.send_json({"ok": True})
            return
        body = self.json_body()
        if path == "/api/conversations":
            self.send_json(service.store.create(body.get("title", "iPhone microphone")).json(), 201)
        elif path.endswith("/rename") and path.startswith("/api/conversations/"):
            cid = path.split("/")[3]
            self.send_json(service.store.rename(cid, body.get("title", "")).json())
        elif path == "/api/capture/start":
            self.send_json(service.start_capture(body.get("conversation_id"), body.get("title", ""),
                                                 int(body.get("sample_rate", 48000))))
        elif path == "/api/capture/stop":
            service.stop_capture(); self.send_json({"ok": True})
        elif path == "/api/chat":
            task = service.submit("chat", body["conversation_id"], body.get("message", ""),
                                  new_chat=bool(body.get("new_chat")),
                                  attach_transcript=bool(body.get("attach_transcript", True)),
                                  attach_history=bool(body.get("attach_history", False)))
            self.send_json({"task_id": task}, 202)
        elif path == "/api/summary":
            self.send_json({"task_id": service.submit("summary", body["conversation_id"])}, 202)
        elif path == "/api/settings":
            for key in settings_json(service):
                if key in body:
                    setattr(service.settings, key, body[key])
            service.settings.save(); self.send_json(settings_json(service))
        else:
            self.send_error(404)


def settings_json(service: Service) -> dict:
    return {k: v for k, v in service.settings.__dict__.items() if k != "token"}


def secrets_compare(left: str, right: str) -> bool:
    import secrets
    return bool(left) and secrets.compare_digest(left, right)


def serve(host: str, port: int, cert: Path | None = None, key: Path | None = None,
          service: Service | None = None):
    service = service or Service()
    server = WebServer((host, port), service)
    if cert and key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server, service
