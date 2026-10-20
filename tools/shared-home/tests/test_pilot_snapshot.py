import base64
import contextlib
import hashlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from home_shared import pilot_snapshot as pilot
import test_causal_protocol


class Publisher:
    def __init__(self, evidence):
        self.evidence, self.closed, self.calls = evidence, False, 0

    def publish_once(self):
        self.calls += 1
        return self.evidence

    def close(self):
        self.closed = True


class PilotSnapshotTests(unittest.TestCase):
    def setUp(self):
        output = io.BytesIO()
        Image.new('RGB', (640, 360)).save(output, 'JPEG', quality=85)
        self.jpeg = output.getvalue()
        self.meta = test_causal_protocol.CausalProtocolTests().receipt(jpeg_sha256=hashlib.sha256(self.jpeg).hexdigest())
        self.publisher = Publisher(dict(host_boot_id=self.meta['host_boot_id'], validation_id='host-clock',
            checked_monotonic_ns=4_900_000_000, valid_until_monotonic_ns=5_900_000_000,
            checked_unix_ns=1004900000000, uncertainty_seconds=.2))
        self.sessions = ['capture', 'capture']
        self.calls = []

    def transport(self, path, token):
        self.calls.append(path)
        self.assertEqual(token, 'private-fixture')
        if path == '/health':
            return {'content-type': 'application/json'}, json.dumps({'healthy': True, 'session': self.sessions.pop(0)}).encode()
        return {'content-type': 'image/jpeg', 'x-victoria-frame-metadata':
            base64.urlsafe_b64encode(json.dumps(self.meta).encode()).decode()}, self.jpeg

    def run_accept(self, **kwargs):
        defaults = dict(point=lambda: SimpleNamespace(before=5_500_000_000, after=5_500_000_001),
                        continuous=lambda *args: None)
        defaults.update(kwargs)
        return pilot.accept_once(SimpleNamespace(ntp=None), 'private-fixture', publisher_factory=lambda _: self.publisher,
            transport=self.transport, monotonic_ns=lambda: 5_400_000_000,
            boot_id=lambda: '12345678-1234-1234-1234-123456789abc', **defaults)

    def test_post_decode_expiry_or_suspend_fails(self):
        with self.assertRaises(ValueError):
            self.run_accept(point=lambda: SimpleNamespace(before=5_900_000_000, after=5_900_000_001))
        self.setUp()
        def suspended(*args):
            raise ValueError('suspend detected')
        with self.assertRaises(ValueError):
            self.run_accept(continuous=suspended)
        self.assertTrue(self.publisher.closed)

    def test_real_jpeg_and_protocol_accept_without_content_output(self):
        result = self.run_accept()
        self.assertEqual(result['frame_id'], 'c2-s7')
        self.assertEqual(result['jpeg_sha256'], hashlib.sha256(self.jpeg).hexdigest())
        self.assertEqual(self.calls, ['/health', '/frame.jpg', '/health'])
        self.assertTrue(self.publisher.closed)
        self.assertEqual(self.publisher.calls, 1)
        self.assertNotIn('private-fixture', json.dumps(result))
        self.assertNotIn('jpeg', result)

    def test_failure_reports_last_safe_phase_without_repeating_probe(self):
        phases = []
        original = self.transport
        def failed_frame(path, token):
            if path == '/frame.jpg':
                raise ValueError('private response must not become a phase')
            return original(path, token)
        self.transport = failed_frame
        with self.assertRaises(ValueError):
            self.run_accept(on_phase=phases.append)
        self.assertEqual(phases[-1], 'jpeg_request')
        self.assertEqual(self.publisher.calls, 1)
        self.assertTrue(self.publisher.closed)

    def test_wrong_boot_expiry_and_clock_binding_fail(self):
        for changes in ({'host_boot_id': 'aaaaaaaa-1234-1234-1234-123456789abc'},
                        {'valid_until_monotonic_ns': 5_400_000_000},
                        {'validation_id': 'other'}, {'uncertainty_seconds': .1}):
            with self.subTest(changes=changes):
                self.setUp()
                self.publisher.evidence.update(changes)
                with self.assertRaises(ValueError):
                    self.run_accept()
                self.assertTrue(self.publisher.closed)

    def test_session_change_fails(self):
        self.sessions[1] = 'restarted'
        with self.assertRaises(ValueError):
            self.run_accept()
        self.assertTrue(self.publisher.closed)

    def test_future_frame_fails(self):
        self.meta.update(host_send_monotonic_ns=5_500_000_000, host_receive_monotonic_ns=5_700_000_000,
                         observed_monotonic_seconds=5.6)
        with self.assertRaises(ValueError):
            self.run_accept()

    def test_unix_timestamp_must_match_published_mapping(self):
        self.meta['observed_at_unix_ns'] += 60_000_000_000
        with self.assertRaises(ValueError):
            self.run_accept()

    def test_corrupt_or_wrong_dimensions_fail_even_with_matching_hash(self):
        for image in (b'not a JPEG', None):
            self.setUp()
            if image is None:
                output = io.BytesIO()
                Image.new('RGB', (641, 360)).save(output, 'JPEG')
                image = output.getvalue()
            self.jpeg = image
            self.meta['jpeg_sha256'] = hashlib.sha256(image).hexdigest()
            with self.assertRaises((ValueError, OSError)):
                self.run_accept()

    def test_publication_failure_closes_without_fetch_or_retry(self):
        with patch.object(self.publisher, 'publish_once', side_effect=RuntimeError('private error')):
            with self.assertRaises(RuntimeError):
                self.run_accept()
        self.assertEqual(self.calls, ['/health'])
        self.assertTrue(self.publisher.closed)

    def test_cli_is_explicit_and_failure_does_not_print_exception(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit):
                pilot.main([])
            with patch.object(pilot.sys, 'platform', 'linux'), patch.object(pilot.os, 'geteuid', return_value=1000, create=True), \
                    patch.object(pilot.socket, 'gethostname', return_value='victoria-den-jetson'), \
                    patch.object(pilot, 'load_config', side_effect=RuntimeError('PRIVATE_TOKEN')):
                self.assertEqual(pilot.main(['--once']), 1)
        self.assertNotIn('PRIVATE_TOKEN', err.getvalue())

    def test_transport_rejects_redirect_body_bounds_and_duplicates(self):
        class Response:
            status = 200
            def getheaders(self):
                return self.headers
            def read(self, bound):
                return b'x'
        for status, headers in ((302, [('Location', 'http://other')]),
                (200, [('Content-Length', str(pilot.MAX_JPEG + 1))]),
                (200, [('Content-Length', '1'), ('Content-Length', '1')]),
                (200, [('Content-Length', '1'), ('Transfer-Encoding', 'chunked')])):
            response = Response()
            response.status, response.headers = status, headers
            with patch.object(pilot.http.client, 'HTTPConnection') as factory:
                connection = factory.return_value
                connection.getresponse.return_value = response
                with self.assertRaises(ValueError):
                    pilot.fetch('/frame.jpg', 'secret')
                factory.assert_called_once_with('127.0.0.1', 8090, timeout=2)
                connection.close.assert_called_once()
                connection.request.assert_called_once()


if __name__ == '__main__':
    unittest.main()
