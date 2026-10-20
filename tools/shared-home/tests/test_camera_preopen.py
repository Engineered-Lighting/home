"""S1.5 camera chat: the Jetson opens TCP and TLS to :9446 in parallel with the
frame capture, then sends exactly one request on that connection. The real
GatewayClient and OnDemandRefresh against the real private TLS ingress (with
the LA keep-alive policy) in front of a CameraPilot; localhost fixtures only."""
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from home_shared.adapter import Frame
from home_shared.camera_pilot import REPLY_WAIT_SECONDS, CameraPilot
from home_shared.gateway_client import (GatewayBusy, GatewayDeadline, GatewayNotSent, GatewayTransportError,
                                        PreopenedConnection)
from home_shared.ingress import IngressPolicy, PrivateTLSIngress
from home_shared.on_demand import OnDemandRefresh
from home_shared.submissions import SubmissionJournal
from test_gateway import binding_fixture, header_fixture, request_fixture
from test_gateway_client import GatewayClient, config as client_config
from test_pilot_sync_reply import FIXTURES, Serving, fingerprint

RID = "12345678-1234-1234-1234-123456789abc"


def wait_until(condition, timeout=3):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(.005)
    return condition()


class PreopenFixture(Serving):
    """A CameraPilot behind PrivateTLSIngress with the :9446 keep-alive policy."""

    def build(self, idle=20):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.pilot = CameraPilot(Path(self.temp.name) / "admission.sqlite", binding=binding_fixture(),
                                 backend=self.backend, model_revision="pinned", image_validator=lambda _: None,
                                 free_space_floor=0, reply_wait_seconds=REPLY_WAIT_SECONDS)
        self.addCleanup(self.pilot.close)
        self.server = PrivateTLSIngress(("127.0.0.1", 0), self.pilot, certificate=str(FIXTURES / "server.pem"),
                                        private_key=str(FIXTURES / "server-test-only.key"),
                                        client_ca=str(FIXTURES / "ca.pem"),
                                        identities={fingerprint("client.pem"): binding_fixture()},
                                        policy=IngressPolicy(8, 2, 3, 2, 100, idle))
        # finish_request runs once TLS completed and the client certificate is
        # a registered identity: the connection is ready for its request.
        self.ready, self.accepted, self.requests = threading.Event(), [], []
        finish, process = self.server.finish_request, self.server.process_request

        def ready(connection, address):
            self.ready.set()
            return finish(connection, address)

        def counted(request, address):
            self.accepted.append(address)
            return process(request, address)
        self.server.finish_request, self.server.process_request = ready, counted
        port = self.serve(self.server)
        self.dials = []

        def dial(address, timeout):
            self.dials.append(address)
            return socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.client = GatewayClient(client_config(port, allow_interactive=True, client_certificate=FIXTURES / "client.pem",
                                                  client_key=FIXTURES / "client-test-only.key"), _connect=dial)

    def backend(self, request):
        self.calls.append(request)
        return "An empty den."

    def payload(self, rid="camera-1"):
        return {**request_fixture(now=time.time(), request_id=rid), "task_class": "interactive"}

    def released(self):
        return wait_until(lambda: not self.client._inflight.locked())


class GatewayClientPreopenTests(PreopenFixture, unittest.TestCase):
    def setUp(self):
        self.build()

    def test_tls_completes_before_submit_and_carries_exactly_that_one_request(self):
        connection = self.client.preopen()
        self.assertTrue(self.ready.wait(3))   # TCP, TLS and the identity check done, no request yet
        self.assertEqual(self.calls, [])
        response = self.client.submit(self.payload(), preopened=connection)
        self.assertEqual((response["status"], response["outcome"]), ("terminal", "completed"))
        self.assertEqual((len(self.accepted), len(self.dials), len(self.calls)), (1, 1, 1))
        self.assertTrue(self.released())
        connection.close()   # a no-op once submit took it
        # One request per pre-open: the next operation dials its own connection.
        self.assertEqual(self.client.status("camera-1")["status"], "terminal")
        self.assertEqual((len(self.accepted), len(self.dials)), (2, 2))
        self.assertTrue(wait_until(lambda: self.server.active_connections == 0))

    def test_close_without_a_request_sends_nothing_and_returns_the_permit(self):
        connection = self.client.preopen()
        self.assertTrue(self.ready.wait(3))
        self.assertTrue(self.client._inflight.locked())   # the pre-open is this client's one operation
        with self.assertRaises(GatewayBusy):
            self.client.status("camera-1")
        connection.close()
        connection.close()
        self.assertTrue(self.released())
        self.assertTrue(wait_until(lambda: self.server.active_connections == 0))
        self.assertEqual(self.calls, [])
        self.assertTrue(self.pilot.readiness()["ready"])
        response = self.client.submit(self.payload())   # an ordinary submit afterwards
        self.assertEqual(response["outcome"], "completed")

    def test_a_failed_preopen_dial_fails_the_submit_exactly_like_a_failed_fresh_dial(self):
        def fail(*args, **kwargs):
            self.dials.append(args)
            raise OSError("failed dial")
        outcomes = []
        for preopen in (False, True):
            client = GatewayClient(self.client.config, _connect=fail)
            connection = client.preopen() if preopen else None
            with self.assertRaises(GatewayTransportError) as caught:
                client.submit(self.payload(), preopened=connection)
            outcomes.append((type(caught.exception), str(caught.exception)))
            self.assertNotIsInstance(caught.exception, GatewayNotSent)
            self.assertTrue(wait_until(lambda: not client._inflight.locked()))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(len(self.dials), 2)   # one dial each: never a second dial for the pre-open
        self.assertEqual(self.calls, [])

    def test_a_preopen_that_outlived_its_budget_never_sends(self):
        connection = self.client.preopen(.3)
        self.assertTrue(self.ready.wait(3))
        self.assertTrue(self.released())   # the worker waited its budget out, closed, and exited
        with self.assertRaises(GatewayTransportError) as caught:
            self.client.submit(self.payload(), preopened=connection)
        self.assertNotIsInstance(caught.exception, GatewayNotSent)
        self.assertEqual((len(self.dials), self.calls), (1, []))
        self.assertTrue(wait_until(lambda: self.server.active_connections == 0))

    def test_a_connection_the_server_closed_while_idle_stays_unproven(self):
        # Not reachable with the 15 s pre-open bound and LA's 20 s idle close;
        # forced here to show the outcome class is unchanged.
        self.server.policy = IngressPolicy(8, 2, 3, 2, 100, .2)
        connection = self.client.preopen()
        self.assertTrue(self.ready.wait(3))
        self.assertTrue(wait_until(lambda: self.server.active_connections == 0))
        with self.assertRaises(GatewayTransportError) as caught:
            self.client.submit(self.payload(), preopened=connection)
        self.assertNotIsInstance(caught.exception, GatewayNotSent)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.released())

    def test_busy_permit_or_unusable_budget_opens_nothing_and_submit_dials_as_before(self):
        self.client._inflight.acquire()
        try:
            busy = self.client.preopen()
        finally:
            self.client._inflight.release()
        dormant = [busy, self.client.preopen(0), self.client.preopen(float("nan")), self.client.preopen(-1)]
        self.assertTrue(all(isinstance(item, PreopenedConnection) and item._operation is None for item in dormant))
        self.assertEqual(self.dials, [])
        self.assertFalse(self.client._inflight.locked())
        self.assertEqual(self.client.submit(self.payload(), preopened=busy)["outcome"], "completed")
        self.assertEqual(len(self.dials), 1)

    def test_validation_failure_is_not_sent_and_closes_the_preopen(self):
        connection = self.client.preopen()
        self.assertTrue(self.ready.wait(3))
        payload = self.payload()
        payload["site_id"] = "echo"
        with self.assertRaises(GatewayNotSent):
            self.client.submit(payload, preopened=connection)
        self.assertTrue(self.released())
        self.assertTrue(wait_until(lambda: self.server.active_connections == 0))
        self.assertEqual(self.calls, [])
        other = GatewayClient(self.client.config, _connect=lambda *a, **kw: self.dials.append(a))
        foreign = self.client.preopen()
        self.addCleanup(foreign.close)
        with self.assertRaises(GatewayNotSent):
            other.submit(self.payload(), preopened=foreign)   # another client's handle: not its to use or close
        self.assertIsNotNone(foreign._operation)

    def test_deadline_while_the_preopen_dial_is_stuck_keeps_the_single_permit(self):
        entered, release = threading.Event(), threading.Event()

        def stuck(*args, **kwargs):
            entered.set()
            release.wait(2)
            raise OSError("peer-data-and-secret-must-not-escape")
        client = GatewayClient(self.client.config, _connect=stuck)
        connection = client.preopen()
        self.assertTrue(entered.wait(2))
        payload = self.payload()
        payload["scheduled_at"] -= 14.8
        payload["request_deadline"] -= 14.8
        try:
            with self.assertRaises(GatewayDeadline):
                client.submit(payload, preopened=connection)
            with self.assertRaises(GatewayBusy):
                client.status("camera-1")
        finally:
            release.set()
        self.assertTrue(wait_until(lambda: not client._inflight.locked()))


class OnDemandPreopenTests(PreopenFixture, unittest.IsolatedAsyncioTestCase):
    """OnDemandRefresh opens the connection, then captures, then submits on it."""

    async def asyncSetUp(self):
        self.build()
        self.journal = SubmissionJournal(Path(self.temp.name) / "journal.sqlite", device_id="den-adapter",
                                         gateway_identity=self.client.gateway_identity, max_records=100,
                                         free_space_floor_bytes=0)
        self.addCleanup(self.journal.close)
        self.published = []

    def refresh(self, camera):
        return OnDemandRefresh(camera=camera, gateway=self.client, journal=self.journal,
                               caption_writer=lambda value, **kw: self.published.append(value),
                               model_revision="pinned", configuration_revision="rev-1")

    async def run_refresh(self, camera, observed=None):
        refresh = self.refresh(camera)
        frame = Frame("capture", "frame", time.time() - .2 if observed is None else observed, 0, .1, "map",
                      header_fixture())
        with patch("home_shared.on_demand.camera_frame", return_value=frame):
            await refresh.accept("a" * 64, RID)
            await refresh.task
        return refresh

    async def test_tls_runs_while_the_frame_is_read_then_one_request_on_it(self):
        seen = []

        def camera():
            # The capture still runs while TCP and TLS complete.
            seen.append(self.ready.wait(3))
            return header_fixture(), "header"
        statuses = []
        status = self.client.status
        self.client.status = lambda request_id: statuses.append(request_id) or status(request_id)
        refresh = await self.run_refresh(camera)
        self.assertEqual(seen, [True])
        self.assertEqual([value["description"] for value in self.published], ["An empty den."])
        self.assertEqual(self.journal.refresh_status("a" * 64, RID)["outcome"], "completed")
        self.assertEqual((len(self.accepted), len(self.dials), len(self.calls), statuses), (1, 1, 1, []))
        self.assertTrue(self.released())
        await refresh.close()

    async def test_a_failed_capture_closes_the_preopened_connection(self):
        def camera():
            self.ready.wait(3)
            raise ConnectionError("camera unavailable")
        refresh = await self.run_refresh(camera)
        self.assertTrue(self.released())
        self.assertTrue(wait_until(lambda: self.server.active_connections == 0))
        self.assertEqual((len(self.accepted), self.calls, self.published), (1, [], []))
        self.assertIsNone(self.journal.outstanding())
        self.assertEqual((await refresh.status("a" * 64, RID))["outcome"], "client_not_sent")
        self.assertTrue(self.pilot.readiness()["ready"])

    async def test_a_stale_frame_closes_the_preopened_connection(self):
        refresh = await self.run_refresh(lambda: (header_fixture(), "header"), observed=time.time() - 10)
        self.assertTrue(self.released())
        self.assertTrue(wait_until(lambda: self.server.active_connections == 0))
        self.assertEqual(self.calls, [])
        self.assertEqual((await refresh.status("a" * 64, RID))["outcome"], "client_not_sent")


class OnDemandOrderTests(unittest.IsolatedAsyncioTestCase):
    """Order and ownership with a fake gateway: no sockets."""

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = SubmissionJournal(Path(self.directory.name) / "journal.sqlite", device_id="den-adapter",
                                         gateway_identity="ingress", max_records=100, free_space_floor_bytes=0)
        self.addCleanup(self.journal.close)
        self.events = []
        events = self.events

        class Handle:
            def close(self):
                events.append("close")

        def preopen(budget):
            events.append(("preopen", 0 < budget <= 15))
            return self.handle

        def submit(payload, *, preopened=None):
            events.append(("submit", preopened is self.handle))
            return dict(version=1, site_id="victoria", request_id=payload["request_id"], status="terminal",
                        outcome="preflight_rejected")
        self.handle = Handle()
        self.gateway = SimpleNamespace(gateway_identity="ingress", preopen=preopen, submit=submit,
                                       config=SimpleNamespace(device_id="den-adapter", configuration_revision="config",
                                                              cameras={"den_oak": "den"}))
        frame = patch("home_shared.on_demand.camera_frame",
                      return_value=Frame("capture", "frame", 1000, 0, .1, "map", b"jpeg"))
        frame.start()
        self.addCleanup(frame.stop)

    def refresh(self, camera):
        return OnDemandRefresh(camera=camera, gateway=self.gateway, journal=self.journal,
                               caption_writer=lambda value, **kw: None, model_revision="model",
                               configuration_revision="config", clock=lambda: 1001)

    def camera(self):
        self.events.append("capture")
        return b"jpeg", "header"

    async def test_preopen_before_capture_and_the_handle_goes_to_submit(self):
        refresh = self.refresh(self.camera)
        await refresh.accept("a" * 64, RID)
        await refresh.task
        self.assertEqual(self.events, [("preopen", True), "capture", ("submit", True), "close"])

    async def test_capture_failure_closes_without_submit(self):
        def broken():
            self.events.append("capture")
            raise ConnectionError("camera unavailable")
        refresh = self.refresh(broken)
        await refresh.accept("a" * 64, RID)
        await refresh.task
        self.assertEqual(self.events, [("preopen", True), "capture", "close"])

    async def test_refused_prepare_closes_without_submit(self):
        refresh = self.refresh(self.camera)
        with patch.object(self.journal, "prepare", return_value=False):
            await refresh.accept("a" * 64, RID)
            await refresh.task
        self.assertEqual(self.events, [("preopen", True), "capture", "close"])

    async def test_a_gateway_without_preopen_submits_as_before(self):
        submitted = []
        self.gateway = SimpleNamespace(gateway_identity="ingress", config=self.gateway.config,
                                       submit=lambda payload: submitted.append(payload) or dict(
                                           version=1, site_id="victoria", request_id=payload["request_id"],
                                           status="terminal", outcome="preflight_rejected"))
        refresh = self.refresh(self.camera)
        await refresh.accept("a" * 64, RID)
        await refresh.task
        self.assertEqual(len(submitted), 1)


if __name__ == "__main__":
    unittest.main()
