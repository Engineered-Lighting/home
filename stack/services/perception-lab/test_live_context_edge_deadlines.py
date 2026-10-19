"""Public ASGI requests and real loopback peers; no internal transport mocks."""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest

from live_context_edge import ContextEdge, binding_digest
from test_live_context_edge import BINDING, SCOPE, TOKEN

ROUTE = "/api/home-context/v1/snapshot?site_id=victoria&room_id=den"
BODY = json.dumps({"schema": "home-context-snapshot-1", "items": [], "cursor": 0}).encode()


async def request(edge, *, method="GET", body=b"", receive=None, deadline=None):
    path = "/api/home-context/v1/intent-events" if method == "POST" else ROUTE
    path, _, query = path.partition("?")
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": query.encode(),
        "headers": [(b"authorization", ("Bearer " + TOKEN).encode()),
            (b"x-home-context-binding", binding_digest(BINDING).encode()), (b"content-type", b"application/json")]}
    messages = []
    async def ordinary_receive(): return {"type": "http.request", "body": body, "more_body": False}
    async def send(value): messages.append(value)
    await edge(scope, receive or ordinary_receive, send, **({} if deadline is None else {"deadline_monotonic": deadline}))
    return messages[0]["status"], json.loads(messages[1]["body"])


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.mode = "healthy"
        self.calls = []
        self.started = threading.Event()
        self.peer_closed = threading.Event()
        owner = self
        class Peer(BaseHTTPRequestHandler):
            # HTTP/1.0 detaches HTTPConnection.sock while its body remains open.
            protocol_version = "HTTP/1.0"
            def log_message(self, *_): pass
            def do_GET(self):
                mode = owner.mode
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.calls.append((self.command, self.path, body))
                try:
                    if mode == "headers":
                        self.connection.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nX-Slow: ")
                        owner.started.set()
                        for _ in range(80): self.connection.sendall(b"x"); time.sleep(.01)
                        self.connection.sendall(b"\r\nContent-Length: " + str(len(BODY)).encode() + b"\r\n\r\n" + BODY)
                    else:
                        if mode == "delayed": time.sleep(.09)
                        self.send_response(200); self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(BODY))); self.end_headers()
                        owner.started.set()
                        if mode == "body":
                            for byte in BODY: self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(.01)
                        else: self.wfile.write(BODY)
                except (OSError, ConnectionError):
                    owner.peer_closed.set()
            do_POST = do_GET
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        self.thread.start()
        async def lab(*_): raise AssertionError("context request escaped to Lab")
        self.lab = lab

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(1)
        self.assert_edge_threads_retired()

    def assert_edge_threads_retired(self):
        self.assertEqual([thread.name for thread in threading.enumerate()
            if thread.name in {"home-context-edge-forward", "home-context-edge-deadline"}], [],
            "public completion must retire both blocking worker and watchdog")

    def edge(self, timeout=.12):
        return ContextEdge(self.lab, binding=BINDING, token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
            journal_address=lambda: self.server.server_address, timeout_s=timeout)

    def test_chunks_cannot_renew_the_inbound_receive_budget(self):
        intent = json.dumps({"schema": "home-live-intent-event-1", **SCOPE, "manifest_ref": BINDING["manifest_ref"]}).encode()
        chunks = [intent[n:n+40] for n in range(0, len(intent), 40)]
        async def upload():
            await asyncio.sleep(.03)
            chunk = chunks.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}
        start = time.monotonic()
        status, value = asyncio.run(request(self.edge(), method="POST", receive=upload))
        self.assertEqual((status, value["error"]["code"]), (503, "context_unavailable"))
        self.assertLess(time.monotonic()-start, .4)
        self.assertEqual(self.calls, [])

    def test_journal_header_and_http10_body_trickles_close_at_the_same_absolute_bound(self):
        for self.mode in ("headers", "body"):
            with self.subTest(mode=self.mode):
                self.started.clear(); self.peer_closed.clear()
                start = time.monotonic()
                status, value = asyncio.run(request(self.edge()))
                self.assertEqual((status, value["error"]["code"]), (503, "context_unavailable"))
                self.assertLess(time.monotonic()-start, .4)
                self.assertTrue(self.peer_closed.wait(.3), "peer must observe the actual socket close")
        self.mode = "healthy"
        self.assertEqual(asyncio.run(request(self.edge()))[0], 200)

    def test_invalidation_wakes_pending_upload_without_forwarding(self):
        async def scenario():
            entered = asyncio.Event()
            async def upload(): entered.set(); await asyncio.Event().wait()
            edge = self.edge(.8)
            pending = asyncio.create_task(request(edge, method="POST", receive=upload))
            await entered.wait(); start = time.monotonic(); edge.invalidate()
            status, value = await pending
            self.assertLess(time.monotonic()-start, .3)
            self.assertEqual((status, value["error"]["code"]), (409, "context_binding_changed"))
            self.assertEqual(self.calls, [])
            self.assertEqual((await request(edge))[0], 200)
        asyncio.run(scenario())

    def test_invalidation_from_control_thread_wakes_upload_and_caller_cancel_cleans_receive(self):
        async def scenario(invalidate):
            edge, entered, retired = self.edge(.8), asyncio.Event(), asyncio.Event()
            async def upload():
                entered.set()
                try: await asyncio.Event().wait()
                finally: retired.set()
            pending = asyncio.create_task(request(edge, method="POST", receive=upload))
            await entered.wait()
            if invalidate:
                control = threading.Thread(target=edge.invalidate); control.start()
                status, value = await pending; control.join()
                self.assertEqual((status, value["error"]["code"]), (409, "context_binding_changed"))
            else:
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError): await pending
            self.assertTrue(retired.is_set())
            self.assertEqual(self.calls, [])
        asyncio.run(scenario(True)); asyncio.run(scenario(False))

    def test_invalidation_closes_headers_and_detached_body_and_preserves_next_request(self):
        for self.mode in ("headers", "body"):
            with self.subTest(mode=self.mode):
                self.started.clear(); self.peer_closed.clear()
                async def scenario():
                    edge = self.edge(.8)
                    pending = asyncio.create_task(request(edge))
                    self.assertTrue(await asyncio.to_thread(self.started.wait, .4))
                    start = time.monotonic(); edge.invalidate()
                    status, value = await pending
                    self.assertLess(time.monotonic()-start, .3)
                    self.assertEqual((status, value["error"]["code"]), (409, "context_binding_changed"))
                    self.assertTrue(await asyncio.to_thread(self.peer_closed.wait, .3))
                    self.mode = "healthy"
                    self.assertEqual((await request(edge))[0], 200)
                asyncio.run(scenario())

    def test_caller_cancellation_propagates_after_closing_the_pending_peer(self):
        self.mode = "body"
        async def scenario():
            edge = self.edge(.8)
            pending = asyncio.create_task(request(edge))
            self.assertTrue(await asyncio.to_thread(self.started.wait, .4))
            start = time.monotonic(); pending.cancel()
            with self.assertRaises(asyncio.CancelledError): await pending
            self.assertLess(time.monotonic()-start, .3)
            self.assert_edge_threads_retired()  # Before event-loop/executor shutdown.
            self.assertTrue(await asyncio.to_thread(self.peer_closed.wait, .3))
            self.mode = "healthy"
            self.assertEqual((await request(edge))[0], 200)
        asyncio.run(scenario())

    def test_private_deadline_only_tightens_and_body_time_consumes_the_forward_budget(self):
        self.mode = "delayed"
        async def upload():
            await asyncio.sleep(.07)
            return {"type": "http.request", "body": b"", "more_body": False}
        start = time.monotonic()
        self.assertEqual(asyncio.run(request(self.edge(), receive=upload))[0], 503)
        self.assertLess(time.monotonic()-start, .3)
        self.mode = "body"
        self.assertEqual(asyncio.run(request(self.edge(.8), deadline=time.monotonic()+.05))[0], 503)
        self.assertEqual(asyncio.run(request(self.edge(.12), deadline=time.monotonic()+10))[0], 503)
        for value in (True, False, float("nan"), float("inf"), -1, time.monotonic()-1, "1", {}):
            before = len(self.calls)
            with self.subTest(value=value):
                status, body = asyncio.run(request(self.edge(), deadline=value))
                self.assertEqual((status, body["error"]["code"]), (503, "context_unavailable"))
                self.assertEqual(len(self.calls), before)

    def test_ambiguous_post_is_sent_once_and_never_retried_after_response_deadline(self):
        self.mode = "body"
        body = json.dumps({"schema": "home-live-intent-event-1", **SCOPE, "manifest_ref": BINDING["manifest_ref"]}).encode()
        status, value = asyncio.run(request(self.edge(), method="POST", body=body))
        self.assertEqual((status, value["error"]["code"]), (503, "context_unavailable"))
        self.assertEqual(self.calls, [("POST", "/api/home-context/v1/intent-events", body)])
        self.assertTrue(self.peer_closed.wait(.3))


if __name__ == "__main__": unittest.main()
