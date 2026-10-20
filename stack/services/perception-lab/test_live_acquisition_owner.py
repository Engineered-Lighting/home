"""Public owner/arbiter seam with fixed synthetic camera and clock ports."""
import base64
import json
import threading
from types import SimpleNamespace
import unittest

from frame_ring import CaptureTurns
import frame_ring
import chrony_preview_client
from live_acquisition_owner import CaptureOwnerRefusal, create_controller_capture_owner, pair_transport
from unittest.mock import patch


class OwnerTests(unittest.TestCase):
    def setup_owner(self, *, acceptance=True, transport=None, clock_authority_ref=None):
        self.calls = []
        self.allow = True
        self.now = 100.0
        self.utc = 1800000000.2
        self.turns = CaptureTurns(threading.Lock(), monotonic=lambda: self.now)
        self.cancel = threading.Event()
        self.body = json.dumps({'schema': 'home-camera-pair-1', 'version': 1,
                                'jpeg_b64': base64.b64encode(b'original-jpeg').decode(),
                                'header': 'original-header',
                                'bundle_b64': base64.b64encode(b'original-bundle').decode(),
                                'calibration': {'session': 'capture-demo'}}).encode()
        def camera(token, *, timeout):
            self.calls.append(('pair', token, timeout))
            return self.body
        def fetch(path, token):
            self.calls.append(('original', path))
            return {'content-type': 'application/json'}, b'{"healthy":true}'
        def accept(config, token, *, transport, publisher_factory):
            self.calls.append(('accept',))
            transport('/health', token)
            headers, jpeg = transport('/frame.jpg', token)
            self.assertEqual((headers['x-victoria-frame-metadata'], jpeg), ('original-header', b'original-jpeg'))
            transport('/health', token)
            if not acceptance:
                raise ValueError('original commissioning refused')
            return {'status': 'accepted'}
        config = SimpleNamespace(ntp=SimpleNamespace(server_ip='192.0.2.1', local_clock_quantization_ns=1000,
                                                     drift_slew_ppm=1000, timeout_seconds=.5,
                                                     valid_for_seconds=1, max_uncertainty_seconds=1))
        pilot = SimpleNamespace(CONFIG='fixed', load_config=lambda _: config, fetch=fetch,
                                accept_once=accept, read_token=lambda: 'fixture-token-only')
        def runtime():
            self.calls.append(('runtime',))
            return pilot, lambda *args: None, lambda *args: None
        self.owner = create_controller_capture_owner(clock=lambda: self.utc, monotonic=lambda: self.now,
                    turns=self.turns, runtime_factory=runtime, pair_transport=transport or camera,
                    clock_authority_ref=clock_authority_ref)
        return self.owner

    def acquire(self):
        return self.owner.acquire(cancel=self.cancel, deadline=105.0, authorized=lambda: self.allow)

    def test_factory_is_idle_then_preserves_pair_and_actual_receipt_availability(self):
        self.setup_owner()
        self.assertEqual(self.calls, [])
        result = self.acquire()
        self.assertEqual(result, {'jpeg': b'original-jpeg', 'header': 'original-header',
                                 'bundle': b'original-bundle', 'calibration': {'session': 'capture-demo'},
                                 'received_at': self.utc})
        self.assertEqual([c[0] for c in self.calls], ['runtime', 'accept', 'original', 'pair', 'original'])
        self.assertIsNone(self.owner.pair)
        self.assertFalse(self.turns.locked())

    def test_local_denial_cancellation_or_expired_deadline_never_contacts_camera(self):
        for kind in ('denied', 'cancelled', 'expired'):
            with self.subTest(kind=kind):
                self.setup_owner()
                if kind == 'denied':
                    self.allow = False
                elif kind == 'cancelled':
                    self.cancel.set()
                else:
                    self.now = 105
                with self.assertRaises(CaptureOwnerRefusal):
                    self.acquire()
                self.assertEqual(self.calls, [])

    def test_existing_camera_chat_turn_and_recent_completion_have_priority(self):
        self.setup_owner()
        self.turns.acquire()
        try:
            with self.assertRaisesRegex(CaptureOwnerRefusal, 'camera_capture_busy'):
                self.acquire()
            self.assertEqual(self.calls, [])
        finally:
            self.turns.release()
        with self.assertRaisesRegex(CaptureOwnerRefusal, 'camera_capture_busy'):
            self.acquire()
        self.now += .5
        self.acquire()

    def test_acceptance_failure_does_not_expose_or_retain_pair(self):
        self.setup_owner(acceptance=False)
        with self.assertRaises(CaptureOwnerRefusal):
            self.acquire()
        self.assertIsNone(self.owner.pair)
        self.assertFalse(self.turns.locked())

    def test_revocation_close_clear_or_deadline_during_capture_discard_response(self):
        for kind in ('revoke', 'close', 'clear', 'deadline'):
            with self.subTest(kind=kind):
                def camera(token, *, timeout):
                    if kind == 'revoke': self.allow = False
                    elif kind == 'close': self.owner.close()
                    elif kind == 'clear': self.owner.clear()
                    else: self.now = 105
                    return self.body
                self.setup_owner(transport=camera)
                with self.assertRaises(CaptureOwnerRefusal):
                    self.acquire()
                self.assertIsNone(self.owner.pair)
                self.assertFalse(self.turns.locked())

    def test_malformed_pair_is_not_fallback_to_separate_bundle_or_jpeg(self):
        for kind in ('duplicate', 'missing', 'invalid_base64', 'oversize', 'nan'):
            with self.subTest(kind=kind):
                self.setup_owner()
                value = json.loads(self.body)
                if kind == 'duplicate':
                    self.body = self.body[:-1] + b',"version":1}'
                elif kind == 'oversize': self.body = b' ' * (8 * 1024**2 + 1)
                else:
                    if kind == 'missing': value.pop('bundle_b64')
                    elif kind == 'invalid_base64': value['bundle_b64'] = '!'
                    else: value['calibration']['bad'] = float('nan')
                    self.body = json.dumps(value).encode()
                with self.assertRaises(CaptureOwnerRefusal):
                    self.acquire()
                self.assertEqual(len([c for c in self.calls if c[0] == 'pair']), 1)
                self.assertIsNone(self.owner.pair)

    def test_close_never_releases_another_camera_user(self):
        self.setup_owner()
        self.turns.acquire()
        try:
            self.owner.close()
            self.assertTrue(self.turns.locked())
            with self.assertRaisesRegex(CaptureOwnerRefusal, 'capture_owner_closed'):
                self.acquire()
        finally:
            self.turns.release()

    def test_only_one_acquisition_in_flight_and_clear_does_not_unlock_active_capture(self):
        entered, finish = threading.Event(), threading.Event()
        failures = []
        def camera(token, *, timeout):
            entered.set()
            if not finish.wait(2):
                raise ValueError('synthetic capture did not finish')
            return self.body
        self.setup_owner(transport=camera)
        def run():
            try:
                self.acquire()
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(CaptureOwnerRefusal, 'capture_owner_busy'):
                self.acquire()
            self.owner.clear()
            self.assertTrue(self.turns.locked())
        finally:
            finish.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], CaptureOwnerRefusal)
        self.assertFalse(self.turns.locked())

    def test_clock_authority_is_exact_per_accepted_observation_not_first_nonce(self):
        authority = {'id': 'clock-acceptor-demo', 'digest': 'a' * 64}
        receipts = []
        def camera(token, *, timeout):
            receipt = {'schema': 3, 'challenge_nonce': str(len(receipts) + 1) * 32,
                       'host_clock_validation_id': 'clock:' + str(len(receipts) + 1)}
            receipt['validation_id'] = 'causal:' + receipt['challenge_nonce']
            receipts.append(receipt)
            value = json.loads(self.body)
            value['header'] = base64.urlsafe_b64encode(json.dumps(receipt).encode()).decode()
            return json.dumps(value).encode()
        # This test's fake external acceptor checks the shape and original bytes;
        # the production default invokes the commissioned pilot's full checks.
        self.setup_owner(transport=camera, clock_authority_ref=authority)
        pilot, policy, publisher = self.owner.runtime_factory()
        def accept(config, token, *, transport, publisher_factory):
            transport('/frame.jpg', token)
            return {'status': 'accepted'}
        pilot.accept_once = accept
        self.owner.runtime_factory = lambda: (pilot, policy, publisher)
        self.assertFalse(self.owner.authorize_clock_receipt({}, authority))
        first = self.acquire()
        first_receipt = json.loads(base64.urlsafe_b64decode(first['header']))
        self.assertTrue(self.owner.authorize_clock_receipt(first_receipt, authority))
        second = self.acquire()
        second_receipt = json.loads(base64.urlsafe_b64decode(second['header']))
        self.assertNotEqual(first_receipt['validation_id'], second_receipt['validation_id'])
        self.assertFalse(self.owner.authorize_clock_receipt(first_receipt, authority))
        self.assertTrue(self.owner.authorize_clock_receipt(second_receipt, authority))
        self.assertFalse(self.owner.authorize_clock_receipt(second_receipt, {**authority, 'digest': 'b' * 64}))
        changed = {**second_receipt, 'host_clock_validation_id': 'unaccepted-clock'}
        self.assertFalse(self.owner.authorize_clock_receipt(changed, authority))
        self.assertFalse(self.owner.authorize_clock_receipt({**second_receipt, 'schema': 3.0}, authority))
        self.owner.clear()
        self.assertFalse(self.owner.authorize_clock_receipt(second_receipt, authority))

    def test_clock_authority_retirement_is_sticky_after_local_revocation(self):
        authority = {'id': 'clock-acceptor-demo', 'digest': 'a' * 64}
        self.setup_owner(clock_authority_ref=authority)
        receipt = {'schema': 3, 'challenge_nonce': 'a' * 32, 'validation_id': 'causal:' + 'a' * 32}
        value = json.loads(self.body)
        value['header'] = base64.urlsafe_b64encode(json.dumps(receipt).encode()).decode()
        self.body = json.dumps(value).encode()
        pilot, policy, publisher = self.owner.runtime_factory()
        pilot.accept_once = lambda config, token, *, transport, publisher_factory: (
            transport('/frame.jpg', token) and {'status': 'accepted'})
        self.owner.runtime_factory = lambda: (pilot, policy, publisher)
        self.acquire()
        self.assertTrue(self.owner.authorize_clock_receipt(receipt, authority))
        self.allow = False
        self.assertFalse(self.owner.authorize_clock_receipt(receipt, authority))
        self.allow = True
        self.assertFalse(self.owner.authorize_clock_receipt(receipt, authority))

    def test_default_transport_is_fixed_loopback_and_rejects_unbounded_response_before_read(self):
        calls = []
        reads = []
        class Connection:
            sock = SimpleNamespace(shutdown=lambda _: None)
            def __init__(self, *args, **kwargs): calls.append(('connect_to', args, kwargs))
            def connect(self): pass
            def request(self, method, path, *, headers): calls.append(('request', method, path, headers))
            def getresponse(self):
                return SimpleNamespace(status=200, getheaders=lambda: [('Content-Type', 'application/json'),
                    ('Content-Length', str(8 * 1024**2 + 1))], read=lambda size: reads.append(size) or b'')
            def close(self): calls.append(('closed',))
        with patch('live_acquisition_owner.http.client.HTTPConnection', Connection):
            with self.assertRaisesRegex(CaptureOwnerRefusal, 'paired_capture_unavailable'):
                pair_transport('s' * 32, timeout=.5)
        self.assertEqual(calls[0], ('connect_to', ('127.0.0.1', 8090), {'timeout': .5}))
        self.assertEqual(calls[1][1:3], ('GET', '/frame-pair'))
        self.assertEqual(calls[-1], ('closed',))
        self.assertEqual(reads, [])

    def test_default_owner_refuses_without_incumbent_controller_capability(self):
        with patch('frame_ring._CONTROLLER_SCOPE', None):
            with self.assertRaisesRegex(ValueError, 'controller_capture_context_unavailable'):
                create_controller_capture_owner()

    def test_bare_turns_cannot_bypass_scope_with_either_physical_default_port(self):
        with patch('frame_ring._CONTROLLER_SCOPE', None):
            turns = CaptureTurns(threading.Lock())
            for partial_ports in ({}, {'runtime_factory': lambda: None}, {'pair_transport': lambda *args, **kw: None}):
                with self.subTest(partial_ports=partial_ports), self.assertRaisesRegex(ValueError, 'controller_capture_context_unavailable'):
                    create_controller_capture_owner(turns=turns, **partial_ports)
            synthetic = create_controller_capture_owner(turns=turns, runtime_factory=lambda: None,
                                                       pair_transport=lambda *args, **kw: None)
            self.assertIs(synthetic.turns, turns)

    def test_controller_capability_is_independent_of_la_readiness_but_not_lifecycle(self):
        with patch('frame_ring._CONTROLLER_SCOPE', None), \
                patch('chrony_preview_client._CAPTURE_LOCK', threading.Lock()):
            # An idle incumbent capture is enough: its LA demand may be false,
            # its occupancy gate unknown, and no LA transport has to exist.
            capture = frame_ring.RingCapture(SimpleNamespace(), lambda: None,
                                            lambda: {'state': 'unknown'}, enabled=lambda: False)
            scope = frame_ring.register_controller_capture_scope(capture)
            self.assertIs(frame_ring.existing_controller_capture_scope(), scope)
            self.assertIs(scope.require_turns(), chrony_preview_client._CAPTURE_LOCK)
            owner = create_controller_capture_owner(runtime_factory=lambda: self.fail('must not acquire'))
            self.assertIs(owner.turns, capture.turns)
            scope.close()
            with self.assertRaisesRegex(CaptureOwnerRefusal, 'controller_capture_context_unavailable'):
                owner.acquire(cancel=threading.Event(), deadline=1e30, authorized=lambda: True)
            self.assertFalse(capture.turns.locked())

    def test_explicit_isolated_turns_cannot_register_production_controller_authority(self):
        with patch('frame_ring._CONTROLLER_SCOPE', None):
            capture = frame_ring.RingCapture(SimpleNamespace(), lambda: None, lambda: {'state': 'unknown'},
                                            turns=CaptureTurns(threading.Lock()))
            self.assertIsNone(frame_ring.register_controller_capture_scope(capture))
            with self.assertRaisesRegex(ValueError, 'controller_capture_context_unavailable'):
                frame_ring.existing_controller_capture_scope()

    def test_actual_managed_controller_without_la_anchors_scope_and_shutdown_revokes(self):
        from managed_detector_app import create_managed_runtime
        from managed_readiness import ReadinessPair
        with patch('frame_ring._CONTROLLER_SCOPE', None), \
                patch('chrony_preview_client._CAPTURE_LOCK', threading.Lock()):
            clients = {'a': SimpleNamespace(close=lambda: True), 'b': SimpleNamespace(close=lambda: True)}
            # Actual production constructor with no LA config, inference,
            # fact-log, recording store or worker preparation.
            runtime = create_managed_runtime(None, None, {}, clients)
            self.assertIsInstance(runtime, ReadinessPair)
            scope = frame_ring.existing_controller_capture_scope()
            self.assertIs(scope, runtime.capture_scope)
            self.assertTrue(scope.runtime)
            owner = create_controller_capture_owner()
            self.assertIs(owner.turns, scope.require_turns())
            self.assertTrue(runtime.close()['drained'])
            with self.assertRaisesRegex(CaptureOwnerRefusal, 'controller_capture_context_unavailable'):
                owner.acquire(cancel=threading.Event(), deadline=1e30, authorized=lambda: True)
            with self.assertRaisesRegex(ValueError, 'controller_capture_context_unavailable'):
                create_controller_capture_owner()

    def test_la_fallback_cannot_replace_or_close_general_controller_scope(self):
        from managed_detector_app import create_managed_runtime
        with patch('frame_ring._CONTROLLER_SCOPE', None), \
                patch('chrony_preview_client._CAPTURE_LOCK', threading.Lock()):
            clients = {'a': SimpleNamespace(close=lambda: True), 'b': SimpleNamespace(close=lambda: True)}
            runtime = create_managed_runtime(None, None, {}, clients)
            scope = runtime.capture_scope
            capture = frame_ring.RingCapture(SimpleNamespace(), lambda: None, lambda: {'state': 'unknown'})
            self.assertIsNone(frame_ring.register_controller_capture_scope(capture))
            self.assertIs(capture.turns, scope.require_turns())
            capture.stopped.set()
            self.assertIs(frame_ring.existing_controller_capture_scope(), scope)
            self.assertFalse(runtime.closed)
            runtime.close()


if __name__ == '__main__':
    unittest.main()
