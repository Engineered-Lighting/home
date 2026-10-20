"""Synchronous terminal replies (speed plan S0.4) on the LA camera pilot and the
live-scene consumer, driven by the unchanged clients over the real private TLS
ingress: GatewayClient and OnDemandRefresh (camera chat, Jetson) and Victoria's
live QwenTransport (perception-lab). Localhost fixtures and fakes only: no
model, host or credential."""
import base64
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from home_shared import camera_pilot as camera_module
from home_shared import lab_live_pilot as pilot_live
from home_shared.adapter import Frame
from home_shared.camera_pilot import REPLY_WAIT_SECONDS, CameraPilot
from home_shared.ingress import IngressPolicy, PrivateTLSIngress
from home_shared.on_demand import OnDemandRefresh
from home_shared.submissions import SubmissionJournal
from test_gateway import binding_fixture, header_fixture, request_fixture
from test_gateway_client import GatewayClient, config as client_config
from test_lab_live_pilot import Envelopes, Model, config_value
from test_lab_live_scene import EPOCH, FACTS

FIXTURES = Path(__file__).parent / "fixtures" / "gateway_client"
VICTORIA = Path(__file__).resolve().parents[3] / "stack" / "services" / "perception-lab"


def fingerprint(name):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert((FIXTURES / name).read_text())).hexdigest()


def tls_options(identities):
    return dict(certificate=str(FIXTURES / "server.pem"), private_key=str(FIXTURES / "server-test-only.key"),
                client_ca=str(FIXTURES / "ca.pem"), identities=identities, policy=IngressPolicy(4, 2, 3, 2))


def victoria_live_transport():
    """Victoria's live-scene transport class, imported from perception-lab unchanged."""
    if "qwen_live" not in sys.modules:
        sys.path.insert(0, str(VICTORIA))
        try:
            import qwen_live  # noqa: F401  (perception-lab, on the Jetson's Python 3.10)
        finally:
            sys.path.remove(str(VICTORIA))
    return sys.modules["qwen_live"].LiveSceneTransport


class Serving:
    """One ingress on a background thread, stopped at cleanup."""

    def serve(self, server):
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(stop)
        return server.server_address[1]


class CameraPilotReplyTests(Serving, unittest.TestCase):
    """CameraPilot.submit behind PrivateTLSIngress, called by the real GatewayClient."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls, self.hold, self.error, self.closures = [], None, None, []

    def pilot(self, reply_wait_seconds=REPLY_WAIT_SECONDS, clock=time.time):
        pilot = CameraPilot(Path(self.temp.name) / "admission.sqlite", binding=binding_fixture(), backend=self.backend,
                            model_revision="pinned", clock=clock, image_validator=lambda _: None, free_space_floor=0,
                            reply_wait_seconds=reply_wait_seconds)
        self.addCleanup(pilot.close)
        close_admission = pilot.close_admission

        def recorded(reason=None):
            self.closures.append(reason)
            close_admission(reason)
        pilot.close_admission = recorded
        return pilot

    def backend(self, request):
        self.calls.append(request)
        if self.hold is not None:
            self.hold.wait(5)
        if self.error is not None:
            raise self.error
        return "An empty den."

    def client(self, pilot):
        port = self.serve(PrivateTLSIngress(("127.0.0.1", 0), pilot,
                                            **tls_options({fingerprint("client.pem"): binding_fixture()})))
        return GatewayClient(client_config(port, allow_interactive=True, client_certificate=FIXTURES / "client.pem",
                                           client_key=FIXTURES / "client-test-only.key"))

    def payload(self, rid="camera-1", now=None):
        return {**request_fixture(now=time.time() if now is None else now, request_id=rid), "task_class": "interactive"}

    def test_reply_wait_is_bounded_and_off_by_default(self):
        self.assertEqual(self.pilot(0).reply_wait_seconds, 0)
        for value in (-1, 8.5, float("nan"), True, "8"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CameraPilot(Path(self.temp.name) / "other.sqlite", binding=binding_fixture(), backend=self.backend,
                            model_revision="pinned", reply_wait_seconds=value)

    def test_gateway_client_accepts_the_terminal_answer_in_the_submit_reply(self):
        pilot = self.pilot()
        client = self.client(pilot)
        started = time.monotonic()
        response = client.submit(self.payload())
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((response["status"], response["outcome"]), ("terminal", "completed"))
        self.assertEqual(response["observation"]["description"], "An empty den.")
        self.assertEqual(response["observation"]["model_revision"], "pinned")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(pilot.readiness()["ready"])
        self.assertEqual(self.closures, [])

    def test_a_reply_wait_timeout_returns_the_reservation_and_polling_recovers(self):
        pilot = self.pilot(reply_wait_seconds=.3)
        client = self.client(pilot)
        self.hold = threading.Event()
        started = time.monotonic()
        response = client.submit(self.payload())
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual((response["status"], response["outcome"]), ("dispatching", None))
        self.hold.set()
        pilot.worker.join(5)
        status = client.status("camera-1")
        self.assertEqual((status["status"], status["outcome"]), ("terminal", "completed"))
        self.assertEqual(self.closures, [])

    def test_the_wait_ends_half_a_second_before_the_request_deadline(self):
        now = [time.time()]
        pilot = self.pilot(clock=lambda: now[0])
        self.hold = threading.Event()
        payload = self.payload(now=now[0])
        now[0] += 14.6  # 0.4 s of the 15 s deadline left: no wait at all
        started = time.monotonic()
        self.assertEqual(pilot.submit(payload, binding_fixture())["status"], "dispatching")
        self.assertLess(time.monotonic() - started, 1)
        self.hold.set()

    def test_an_error_while_waiting_never_reaches_the_ingress_or_closes_admission(self):
        pilot = self.pilot()
        client = self.client(pilot)
        status, reads = pilot.status, []

        def failing_reread(*args):
            reads.append(args)
            if len(reads) == 2:  # the re-read after the worker finished
                raise sqlite3.OperationalError("database is locked")
            return status(*args)
        pilot.status = failing_reread
        response = client.submit(self.payload())
        self.assertEqual((response["status"], response["outcome"]), ("dispatching", None))
        pilot.worker.join(5)
        self.assertFalse(pilot.closed)
        self.assertEqual(self.closures, [])
        self.assertTrue(pilot.readiness()["ready"])

    def test_a_failing_join_returns_the_reservation(self):
        class BrokenJoin(threading.Thread):
            broken = True

            def join(self, timeout=None):
                if BrokenJoin.broken:
                    BrokenJoin.broken = False
                    raise RuntimeError("join failed")
                return super().join(timeout)
        pilot = self.pilot()
        with patch.object(camera_module.threading, "Thread", BrokenJoin):
            response = pilot.submit(self.payload(), binding_fixture())
        self.assertEqual(response["status"], "dispatching")
        pilot.worker.join(5)
        self.assertFalse(pilot.closed)

    def test_an_unknown_outcome_keeps_its_own_fail_closed_rule(self):
        pilot = self.pilot()
        client = self.client(pilot)
        self.error = RuntimeError("transport lost")
        with patch("sys.stderr"):
            response = client.submit(self.payload())
        # The reply is the recorded row; the pilot closed itself (camera_pilot.py
        # _execute), not because the wait raised into the ingress.
        self.assertEqual((response["status"], response["outcome"]), ("indeterminate", "upstream_outcome_unknown"))
        self.assertTrue(pilot.closed)
        self.assertEqual(self.closures, [])


class OnDemandReplyTests(Serving, unittest.IsolatedAsyncioTestCase):
    """The Jetson's OnDemandRefresh breaks on a terminal first response: no poll."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pilot = CameraPilot(Path(self.temp.name) / "admission.sqlite", binding=binding_fixture(),
                                 backend=lambda request: "An empty den.", model_revision="pinned",
                                 image_validator=lambda _: None, free_space_floor=0,
                                 reply_wait_seconds=REPLY_WAIT_SECONDS)
        self.addCleanup(self.pilot.close)
        port = self.serve(PrivateTLSIngress(("127.0.0.1", 0), self.pilot,
                                            **tls_options({fingerprint("client.pem"): binding_fixture()})))
        self.client = GatewayClient(client_config(port, allow_interactive=True,
                                                  client_certificate=FIXTURES / "client.pem",
                                                  client_key=FIXTURES / "client-test-only.key"))
        self.journal = SubmissionJournal(Path(self.temp.name) / "journal.sqlite", device_id="den-adapter",
                                         gateway_identity=self.client.gateway_identity, max_records=100,
                                         free_space_floor_bytes=0)
        self.addCleanup(self.journal.close)

    async def test_terminal_submit_reply_publishes_without_a_status_poll(self):
        statuses, published = [], []
        status = self.client.status
        self.client.status = lambda request_id: statuses.append(request_id) or status(request_id)
        jpeg = header_fixture()
        observed = time.time() - .2
        refresh = OnDemandRefresh(camera=lambda: (jpeg, "header"), gateway=self.client, journal=self.journal,
                                  caption_writer=lambda value, **kw: published.append(value), model_revision="pinned",
                                  configuration_revision="rev-1")
        rid = "12345678-1234-1234-1234-123456789abc"
        with patch("home_shared.on_demand.camera_frame",
                   return_value=Frame("capture", "frame", observed, 0, .1, "map", jpeg)):
            await refresh.accept("a" * 64, rid)
            await refresh.task
        self.assertEqual(statuses, [])
        self.assertEqual([value["description"] for value in published], ["An empty den."])
        self.assertEqual(self.journal.refresh_status("a" * 64, rid)["outcome"], "completed")
        image = await refresh.image("a" * 64, rid)
        self.assertEqual(image["image_base64"], base64.b64encode(jpeg).decode())
        await refresh.close()


@unittest.skipUnless((VICTORIA / "qwen_live.py").is_file(), "Victoria perception-lab sources not in this checkout")
class LiveSceneReplyTests(Serving, unittest.TestCase):
    """PilotLiveScene.submit behind PilotLiveIngress, called by Victoria's live transport."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logs = []
        self.model = Model()
        self.pilot = CameraPilot(Path(self.temp.name) / "admission.sqlite", binding=binding_fixture(),
                                 backend=lambda request: "An empty den.", model_revision="pinned",
                                 image_validator=lambda _: None, free_space_floor=0)
        path = Path(self.temp.name) / "live-scene.json"
        path.write_text(json.dumps(config_value()))
        self.config = pilot_live.load_live_config(path)

    def tearDown(self):
        if self.model.hold is not None:
            self.model.hold.set()
        if hasattr(self, "live"):
            self.live.close(5)
        self.pilot.close()

    def compose(self, reply_wait_seconds=REPLY_WAIT_SECONDS):
        self.live = pilot_live.PilotLiveScene(self.pilot, self.config, backend=self.model, log=self.logs.append,
                                              reply_wait_seconds=reply_wait_seconds)
        return self.live

    def transport(self, live):
        server = pilot_live.PilotLiveIngress(("127.0.0.1", 0), self.pilot, live,
                                             **tls_options({fingerprint("client.pem"): live.binding,
                                                            "0" * 64: binding_fixture()}))
        port = self.serve(server)
        context = ssl.create_default_context(cafile=str(FIXTURES / "ca.pem"))
        context.check_hostname = False
        context.load_cert_chain(str(FIXTURES / "client.pem"), str(FIXTURES / "client-test-only.key"))
        config = {"hostname": "localhost", "port": port, "ca_file": str(FIXTURES / "ca.pem"),
                  "client_certificate": str(FIXTURES / "client.pem"),
                  "client_key": str(FIXTURES / "client-test-only.key"), "deadline_seconds": 7,
                  "response_grace_seconds": 2, "poll_seconds": .1}
        transport = victoria_live_transport()(
            config, connection_factory=lambda timeout: http.client.HTTPSConnection("127.0.0.1", port, timeout=timeout,
                                                                                    context=context))
        self.lookups = []
        lookup = transport.lookup
        transport.lookup = lambda reference, **kw: self.lookups.append(reference) or lookup(reference, **kw)
        return transport

    def envelope(self):
        return Envelopes().envelope(deadline=time.time() + 7)

    def test_qwen_transport_takes_the_terminal_202_without_a_lookup(self):
        transport = self.transport(self.compose())
        envelope = self.envelope()
        proof = transport(envelope)
        self.assertEqual((proof["state"], proof["terminal"], proof["engine_epoch"]), ("completed", True, EPOCH))
        self.assertEqual(json.loads(proof["response"]["choices"][0]["message"]["content"]), FACTS)
        self.assertEqual(self.lookups, [])
        self.assertEqual(len(self.model.calls), 1)
        self.assertFalse(self.live.closed)

    def test_a_reply_wait_timeout_falls_back_to_lookup_polling(self):
        transport = self.transport(self.compose(reply_wait_seconds=.3))
        self.model.hold = threading.Event()
        threading.Timer(.8, self.model.hold.set).start()
        proof = transport(self.envelope())
        self.assertEqual((proof["state"], proof["terminal"]), ("completed", True))
        self.assertGreaterEqual(len(self.lookups), 1)
        self.assertEqual(len(self.model.calls), 1)

    def test_terminal_failures_also_arrive_in_the_submit_reply(self):
        transport = self.transport(self.compose())
        self.model.error = pilot_live.UpstreamFinished("exit 1")
        proof = transport(self.envelope())
        self.assertEqual((proof["state"], proof["terminal"], proof["reason"]), ("failed", True, "upstream_failed"))
        self.assertEqual(self.lookups, [])

    def test_an_error_while_waiting_returns_the_reservation_and_keeps_live_open(self):
        live = self.compose()
        record, reads = live._record, []

        def failing_reread(ledger_id):
            reads.append(ledger_id)
            if len(reads) == 2:  # the re-read after the worker finished
                raise sqlite3.OperationalError("database is locked")
            return record(ledger_id)
        live._record = failing_reread
        proof = live.submit(self.envelope(), live.binding)
        self.assertEqual((proof["state"], proof["terminal"]), ("dispatching", False))
        live.worker.join(5)
        self.assertFalse(live.closed or self.pilot.closed)
        live._record = record
        self.assertEqual(live.submit(self.envelope(), live.binding)["state"], "completed")


if __name__ == "__main__":
    unittest.main()
