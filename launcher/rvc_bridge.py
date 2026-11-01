"""Loopback-only RVC HTTP bridge. Torch stays in the independent subprocess."""
from __future__ import annotations

import base64
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

MAX_AUDIO = 5 * 1024 * 1024
MAX_REQUEST = 7 * 1024 * 1024


def make_server(converter, token: str, port: int = 9882) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("RVC bridge requires a token")
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_json(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}")

        def do_GET(self):
            if not self.authorized():
                return self.send_json(401, {"error": "unauthorized"})
            if self.path != "/health":
                return self.send_json(404, {"error": "not found"})
            # A synchronous local synthesizer may not answer /health while its
            # inference is running. Busy is distinct from an offline model.
            ready = lock.locked() or converter.is_available()
            self.send_json(200 if ready else 503, {"ready": ready, "busy": lock.locked()})

        def do_POST(self):
            if not self.authorized():
                return self.send_json(401, {"error": "unauthorized"})
            if self.path == "/v1/audio/speech" and hasattr(converter, "synthesize"):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8192:
                        return self.send_json(400, {"error": "invalid request size"})
                    payload = json.loads(self.rfile.read(length))
                    text = str(payload.get("input") or "").strip()
                    if not 0 < len(text) <= 1000:
                        return self.send_json(400, {"error": "input must contain 1-1000 characters"})
                except (ValueError, TypeError, AttributeError):
                    return self.send_json(400, {"error": "invalid request"})
                if not lock.acquire(blocking=False):
                    return self.send_json(429, {"error": "local voice is busy"})
                try:
                    audio = converter.synthesize(text)
                    if not audio or len(audio) > MAX_AUDIO:
                        return self.send_json(502, {"error": "local synthesis failed"})
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(audio)))
                    self.end_headers()
                    self.wfile.write(audio)
                except Exception:
                    self.send_json(502, {"error": "local synthesis failed; check local model log"})
                finally:
                    lock.release()
                return
            if self.path != "/convert" or hasattr(converter, "synthesize"):
                return self.send_json(404, {"error": "not found"})
            self.connection.settimeout(15)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    return self.send_json(413, {"error": "audio too large"})
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("invalid request")
                audio = base64.b64decode(payload["audio"], validate=True)
                if not audio or len(audio) > MAX_AUDIO:
                    return self.send_json(413, {"error": "audio too large"})
                voice = str(payload.get("voice") or converter.default_voice)
                if len(voice) > 64:
                    raise ValueError("invalid voice")
            except (ValueError, KeyError, TypeError):
                return self.send_json(400, {"error": "invalid audio request"})
            if not lock.acquire(blocking=False):
                return self.send_json(429, {"error": "RVC is busy; try later"})
            try:
                with TemporaryDirectory(prefix="im-rvc-") as tmp:
                    source = Path(tmp) / "input.mp3"
                    source.write_bytes(audio)
                    result = converter.convert(source, voice)
                    if result is None:
                        return self.send_json(502, {"error": "RVC conversion failed; check local logs"})
                    data = result.read_bytes()
                    if len(data) > MAX_AUDIO:
                        return self.send_json(413, {"error": "result too large"})
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
            finally:
                lock.release()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
