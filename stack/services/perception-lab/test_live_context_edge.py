"""Authored local edge tests; no Home hosts, models, cameras or household data."""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import unittest

from live_context_edge import ContextEdge, binding_digest

SCOPE = {"site_id": "victoria", "room_id": "den", "session_id": "fictional-session", "domain_epoch": "fictional-domain"}
BINDING = {"scope": SCOPE, "manifest_ref": {"id": "fictional-manifest", "digest": "a" * 64}}
TOKEN = "fictional-context-token-not-a-Lab-grant"


async def invoke(edge, path, *, method="GET", body=b"", token=TOKEN, binding=None):
    split = path.split("?", 1)
    scope = {"type": "http", "method": method, "path": split[0], "raw_path": split[0].encode(),
             "query_string": split[1].encode() if len(split) > 1 else b"",
             "headers": [(b"authorization", ("Bearer " + token).encode()),
                         (b"x-home-context-binding", (binding or binding_digest(BINDING)).encode()),
                         (b"content-type", b"application/json"), (b"cookie", b"browser-secret")]}
    messages = []
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    async def send(value):
        messages.append(value)
    await edge(scope, receive, send)
    return messages[0]["status"], json.loads(messages[1]["body"])


class ContextEdgeTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.lab_calls = [], []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                owner.calls.append({"url": self.path, "headers": dict(self.headers)})
                raw = json.dumps({"schema": "home-context-snapshot-1", "stream_epoch": "fictional-stream", "cursor": 0,
                                  "revocation_generation": 0, "items": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            do_POST = do_GET
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()
        async def lab(scope, receive, send):
            self.lab_calls.append(scope)
            await send({"type": "http.response.start", "status": 401, "headers": []})
            await send({"type": "http.response.body", "body": b'{"lab":"unchanged_auth"}'})
        self.edge = ContextEdge(lab, binding=BINDING, token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
                                journal_address=lambda: self.server.server_address)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path="/api/home-context/v1/snapshot?site_id=victoria&room_id=den", **kwargs):
        return asyncio.run(invoke(self.edge, path, **kwargs))

    def test_scoped_edge_reaches_only_fixed_loopback_and_does_not_forward_browser_headers(self):
        status, value = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(value["schema"], "home-context-snapshot-1")
        self.assertEqual(self.calls[0]["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertNotIn("Cookie", self.calls[0]["headers"])
        self.assertEqual(self.lab_calls, [])

    def test_context_scope_grant_and_private_routes_refuse_before_journal(self):
        for path in ("/api/home-context/v1/snapshot?site_id=echo&room_id=den",
                     "/api/home-context/v1/live-input-cut?site_id=victoria&room_id=den",
                     "/api/home-context/v1/worker-commits?site_id=victoria&room_id=den",
                     "/api/home-context/v1/events?site_id=victoria&room_id=den&after=0&url=https://elsewhere.invalid",
                     "/api/home-context/v1/intent-events/a%2fb?site_id=victoria&room_id=den"):
            self.assertGreaterEqual(self.request(path)[0], 400)
        self.assertEqual(self.request(token="Lab-token")[0], 401)
        self.assertEqual(self.request(binding="b" * 64)[0], 409)
        self.assertEqual(self.calls, [])

    def test_other_routes_retain_incumbent_lab_auth(self):
        self.assertEqual(self.request("/api/perception-lab/v1/capabilities")[0], 401)
        self.assertEqual(len(self.lab_calls), 1)

    def test_declaration_route_requires_explicit_intent3_capability(self):
        body=json.dumps({'schema':'home-live-intent-event-3',**SCOPE,'manifest_ref':BINDING['manifest_ref']}).encode()
        for version in (2,3):
            edge=ContextEdge(self.edge.app,binding=BINDING,token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
                journal_address=lambda:self.server.server_address,intent_version=version)
            status,_=asyncio.run(invoke(edge,'/api/home-context/v1/intent-events',method='POST',body=body))
            self.assertEqual(status,200 if version==3 else 403)
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('Cookie',self.calls[0]['headers'])

    def test_intent_scope_duplicate_keys_and_nonloopback_destination_refuse(self):
        intent = {"schema": "home-live-intent-event-1", **SCOPE, "manifest_ref": BINDING["manifest_ref"]}
        self.assertEqual(self.request("/api/home-context/v1/intent-events", method="POST", body=json.dumps({**intent, "domain_epoch": "other"}).encode())[0], 403)
        self.assertGreaterEqual(self.request("/api/home-context/v1/intent-events", method="POST", body=b'{"schema":"home-live-intent-event-1","schema":"other"}')[0], 400)
        self.edge.journal_address = lambda: ("192.0.2.1", 80)
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
