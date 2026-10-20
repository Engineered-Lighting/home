"""Authored exact-pair transport through unchanged commissioned-client control."""
import base64
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import live_capture_fixtures as capture
from paired_preview_client import PairedPreviewClient, checked_pair, policy
from store import LabError


def authored_pair(*, sequence=7, observed_ns=capture.NOW*1000000000):
    jpeg, header, bundle, calibration = capture.inputs(sequence=sequence, observed_ns=observed_ns)
    return {'jpeg': jpeg, 'header': header, 'bundle': bundle, 'calibration': calibration,
            'received_at': capture.NOW+.2}


def wire(pair):
    return json.dumps({'schema': 'home-camera-pair-1', 'version': 1,
        'jpeg_b64': base64.b64encode(pair['jpeg']).decode(), 'header': pair['header'],
        'bundle_b64': base64.b64encode(pair['bundle']).decode(), 'calibration': pair['calibration']}).encode()


def commissioned_double(calls, *, fail=False):
    config = SimpleNamespace(ntp=SimpleNamespace(server_ip='192.0.2.1', local_clock_quantization_ns=1000,
        drift_slew_ppm=1000, timeout_seconds=.5, valid_for_seconds=1, max_uncertainty_seconds=1))
    def original(path, token):
        calls.append(('original_transport', path))
        return {'content-type': 'application/json'}, b'{"healthy":true}'
    def accept(value, token, *, transport, publisher_factory):
        if value is not config:
            raise AssertionError('commissioning configuration changed')
        calls.append(('commissioned_accept_once',))
        publisher_factory(value)
        transport('/health', token)
        transport('/frame.jpg', token)
        transport('/health', token)
        if fail:
            raise ValueError('commissioned clock refusal')
        return {'status': 'accepted'}
    pilot = SimpleNamespace(CONFIG='fixed', load_config=lambda _: config, fetch=original,
        read_token=lambda: 'fictional-token-'+'x'*32, accept_once=accept)
    return pilot, lambda *args: args, lambda *args: None


class PairedPreviewClientTests(unittest.TestCase):
    def setUp(self):
        self.pair, self.calls = authored_pair(), []

    def client(self):
        def transport(token, *, timeout):
            self.assertEqual(timeout, 2)
            self.calls.append(('one_pair',))
            return wire(self.pair)
        return PairedPreviewClient(transport=transport, clock=lambda: capture.NOW+.2)

    def test_same_originals_return_only_after_commissioning_and_transfer_once(self):
        client = self.client()
        with patch('chrony_preview_client.commissioned_runtime', return_value=commissioned_double(self.calls)):
            jpeg, header = client()
        self.assertEqual((jpeg, header), (self.pair['jpeg'], self.pair['header']))
        self.assertEqual(client.take_pair(jpeg, header), self.pair)
        with self.assertRaisesRegex(ValueError, 'one accepted exact pair'):
            client.take_pair(jpeg, header)
        self.assertEqual(self.calls, [('commissioned_accept_once',), ('original_transport', '/health'),
            ('one_pair',), ('original_transport', '/health')])

    def test_commissioned_refusal_discards_pair_without_fallback(self):
        client = self.client()
        with patch('chrony_preview_client.commissioned_runtime', return_value=commissioned_double(self.calls, fail=True)):
            with self.assertRaisesRegex(ValueError, 'commissioned clock refusal'):
                client()
        with self.assertRaisesRegex(ValueError, 'one accepted exact pair'):
            client.take_pair(self.pair['jpeg'], self.pair['header'])
        self.assertEqual(sum(c[0]=='one_pair' for c in self.calls), 1)

    def test_existing_capture_turn_contention_never_requests_pair(self):
        client = self.client()
        with patch('chrony_preview_client._CAPTURE_LOCK', SimpleNamespace(acquire=lambda **kw: False)):
            with self.assertRaisesRegex(ValueError, 'capture busy'):
                client()
        self.assertFalse(self.calls)

    def test_wrong_pair_or_changed_calibration_epoch_refuses(self):
        other = authored_pair(sequence=8)
        for bundle, calibration in ((other['bundle'], self.pair['calibration']),
                                   (self.pair['bundle'], {**self.pair['calibration'], 'session': 'other-epoch'})):
            with self.assertRaisesRegex(ValueError, 'pair binding'):
                checked_pair(self.pair['jpeg'], self.pair['header'], bundle, calibration)

    def test_changed_jpeg_or_malformed_raw_archive_refuses(self):
        for jpeg, bundle in ((self.pair['jpeg']+b'x', self.pair['bundle']),
                             (self.pair['jpeg'], b'not-a-numerical-archive')):
            with self.assertRaises((ValueError, LabError)):
                checked_pair(jpeg, self.pair['header'], bundle, self.pair['calibration'])

    def test_wrong_take_arguments_clear_unconsumed_pair(self):
        client = self.client()
        with patch('chrony_preview_client.commissioned_runtime', return_value=commissioned_double(self.calls)):
            client()
        with self.assertRaises(ValueError):
            client.take_pair(self.pair['jpeg'], 'changed-header')
        with self.assertRaises(ValueError):
            client.take_pair(self.pair['jpeg'], self.pair['header'])

    def test_policy_is_explicit_closed_and_bounded(self):
        value = {'schema': 'home-lab-paired-preview-1', 'version': 1, 'enabled': True, 'max_pairs': 24}
        self.assertEqual(policy(value), value)
        for changes in ({'max_pairs': 0}, {'max_pairs': 25}, {'max_pairs': True}, {'enabled': False}, {'upstream': 'http://foreign'}):
            with self.assertRaises(ValueError):
                policy({**deepcopy(value), **changes})


if __name__ == '__main__':
    unittest.main()
