"""The click page's server: 127.0.0.1 only, a one-time token, Host and Origin checks.

Follows tools/den-label/label.py on claude/den-label. The page reads the
session's state and its one frame, and posts clicks, clears and done. Frames
never leave the PC.
"""
import hmac
import json
import re
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
MAX_BODY = 64 * 1024
PAGE_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
TOKEN_HEADER = "X-Reg-Token"


class PageError(Exception):
    """A request the session refuses; the page shows the text."""


def make_server(session, token=None):
    """session: .state() -> dict, .image(name) -> bytes, .click(body), .clear(body), .done()."""
    token = token or secrets.token_urlsafe(24)
    page = (HERE / "page.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = "den-register", ""

        def log_message(self, *args):
            pass

        def send(self, status, content_type, body, extra=()):
            self.send_response(status)
            for name, value in (("Content-Type", content_type), ("Content-Length", str(len(body))),
                                ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                                ("Referrer-Policy", "no-referrer"), *extra):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status, value):
            self.send(status, "application/json", json.dumps(value).encode("ascii"))

        def allowed(self):
            port = self.server.server_address[1]
            origin = self.headers.get("Origin")
            supplied = self.headers.get(TOKEN_HEADER) or parse_qs(urlsplit(self.path).query).get("t", [""])[0]
            ok = (self.headers.get("Host") == f"127.0.0.1:{port}"
                  and origin in (None, f"http://127.0.0.1:{port}")
                  and hmac.compare_digest(supplied.encode("utf-8", "replace"), token.encode("ascii")))
            if not ok:
                self.send(403, "text/plain", b"Forbidden\n")
            return ok

        def do_GET(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            try:
                if path == "/":
                    self.send(200, "text/html; charset=utf-8", page, (("Content-Security-Policy", PAGE_CSP),))
                elif path == "/api/state":
                    self.send_json(200, session.state())
                elif re.fullmatch(r"/api/(left|rgb)", path):
                    self.send(200, "application/octet-stream", session.image(path.rsplit("/", 1)[1]))
                else:
                    self.send(404, "text/plain", b"Not found\n")
            except PageError as exc:
                self.send_json(410, {"error": str(exc)})

        def do_POST(self):
            if not self.allowed():
                return
            path = urlsplit(self.path).path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 < length <= MAX_BODY:
                    raise PageError("empty or too large")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if type(body) is not dict:
                    raise PageError("a JSON object")
                if path == "/api/click":
                    self.send_json(200, session.click(body))
                elif path == "/api/clear":
                    self.send_json(200, session.clear(body))
                elif path == "/api/done":
                    session.done()
                    self.send_json(200, {"ok": True})
                else:
                    self.send(404, "text/plain", b"Not found\n")
            except (ValueError, PageError) as exc:
                self.send_json(400, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server, token


def serve(session, *, open_browser=True, ready=None, out=sys.stdout):
    """Serve until the page says done (or Ctrl+C). Returns True when done."""
    server, token = make_server(session)
    url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Click page: {url}", file=out, flush=True)
    print("This PC only. Leave this window open; press Done on the page when finished.", file=out, flush=True)
    if ready:
        ready(url)
    if open_browser:
        webbrowser.open(url)
    try:
        while not session.finished.wait(0.2):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return session.finished.is_set()
