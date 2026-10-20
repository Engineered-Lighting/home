"""Additive capture policy2 at the qualified entry/public journal seam."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import live_capture_fixtures as fixtures
from journal import Journal, PREFIX, digest
from live_capture import LiveCaptureAdapter
from live_test_helpers import LiveHttp


def policy2_fixture():
    config, policy, method, calibration, grant = deepcopy(fixtures.setup())
    policy['schema'], policy['version'] = 'home-live-capture-policy-2', 2
    policy['clock_binding'] = {
        'timing_generation': 0,
        'host_boot_id': '00000000-0000-0000-0000-000000000001',
        'validation_mode': 'owner_accepted_per_observation',
        'authority_ref': fixtures.pin('synthetic-commissioned-clock-acceptor')}
    method['digest'] = digest(policy)
    source = config['manifest']['sources'][fixtures.SOURCE]
    source['capture_clock']['mapping_digest'] = digest(policy['clock_binding'])
    source['method_refs'] = [deepcopy(method)]
    source['association_method_refs'] = [deepcopy(method)]
    config['manifest_ref']['digest'] = digest(config['manifest'])
    grant['live_bindings'][0]['manifest_ref'] = deepcopy(config['manifest_ref'])
    return config, policy, method, calibration, grant


def rotating_inputs(sequence=7, *, nonce=None, validation_id=None):
    nonce = nonce or str(sequence) * 32
    return fixtures.inputs(sequence=sequence,
        observed_ns=1800000000000000000 + (sequence - 7) * 1_000_000_000,
        challenge_nonce=nonce, validation_id=validation_id or 'causal:' + nonce,
        host_clock_validation_id='synthetic-host-clock-' + str(sequence))


class CapturePolicy2Tests(unittest.TestCase):
    def setUp(self):
        self.now = fixtures.NOW + .2
        self.config, self.policy, self.method, self.calibration, self.grant = policy2_fixture()
        self.expected_receipt = None
        self.server = self.journal = None
        self.work = tempfile.TemporaryDirectory(prefix='synthetic-capture-policy2-')
        self.addCleanup(self.work.cleanup)

    def tearDown(self):
        if self.server is not None: self.server.close()
        if self.journal is not None: self.journal.close()

    def proof(self, receipt, authority_ref):
        return (receipt == self.expected_receipt and self.expected_receipt is not None
                and authority_ref == self.policy['clock_binding']['authority_ref'])

    def construct(self, proof=None):
        self.adapter = LiveCaptureAdapter(self.config, source_id=fixtures.SOURCE, revision=fixtures.REVISION,
            policy=self.policy, method_ref=self.method, clock=lambda: self.now,
            publish=self.publish, control_clock=self.control_clock,
            clock_authority=self.proof if proof is None else proof)
        self.journal = Journal(Path(self.work.name) / 'synthetic.sqlite', grants=[self.grant],
            profile=fixtures.PROFILE, live_config=self.config, clock=lambda: self.now,
            live_receipt_resolver=lambda store, key: self.adapter.resolve_receipt(store, key))
        self.server = LiveHttp(self.journal)

    def control_clock(self):
        policy = self.config['manifest']['sources'][fixtures.SOURCE]['control_clock']
        return {'observed_at': self.now, 'clock': {'status': 'commissioned', 'mapping_id': policy['mapping_id'],
            'mapping_digest': policy['mapping_digest'], 'source_epoch': policy['source_epoch'],
            'original_unit': 'unix_ns', 'original_value': str(round(self.now * 1e9)), 'uncertainty_s': .001}}

    def publish(self, event):
        status, body = self.server.request('POST', PREFIX + '/events', event, token=fixtures.TOKEN)
        if status != 200: raise ValueError(body)
        return body

    def snapshot(self):
        status, body = self.server.request('GET', PREFIX + '/snapshot?site_id=synthetic-site&room_id=synthetic-room', token=fixtures.TOKEN)
        self.assertEqual(status, 200, body)
        return body

    def test_rotating_clock_challenges_keep_original_receipts_and_publish_both_captures(self):
        self.construct()
        results = []
        for sequence in (7, 8):
            data = rotating_inputs(sequence)
            self.expected_receipt = fixtures.decoded_header(data[1])
            self.now = fixtures.NOW + sequence - 7 + .2
            result = self.adapter.offer(*data, received_at=self.now - .1)
            self.assertEqual(result['status'], 'qualified', result)
            refs = result['publication']['payload']['original_receipt_refs']
            rgb_ref = next(ref for ref in refs if ref['component_id'] == 'rgb')
            original = self.adapter.original_receipt(rgb_ref['store_id'], rgb_ref['receipt_id'])
            self.assertEqual(original['rgb_receipt'], self.expected_receipt)
            self.assertEqual(result['publication']['payload']['clock']['original_value'],
                             str(1800000000000000000 + (sequence - 7) * 1_000_000_000))
            results.append(result)
        self.assertGreater(results[1]['receipt']['cursor'], results[0]['receipt']['cursor'])
        current = self.snapshot()['items']
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]['cursor'], results[1]['receipt']['cursor'])
        self.assertEqual(current[0]['payload']['observed_at'], fixtures.NOW + 1)
        self.assertEqual(current[0]['payload']['available_at'], fixtures.NOW + 1.2)

    def test_policy2_without_fixed_owner_authority_refuses_construction(self):
        with self.assertRaises(ValueError):
            LiveCaptureAdapter(self.config, source_id=fixtures.SOURCE, revision=fixtures.REVISION,
                policy=self.policy, method_ref=self.method, clock=lambda: self.now, publish=lambda _: None)

    def test_missing_owner_proof_withdraws_without_fabricating_capture(self):
        self.construct(proof=lambda *_: False)
        result = self.adapter.offer(*rotating_inputs(), received_at=self.now - .1)
        self.assertEqual(result['status'], 'refused', result)
        self.assertIsNone(result['capture'])
        self.assertEqual(result['withdrawal'], 'recorded', result)
        self.assertEqual(self.snapshot()['items'], [])

    def test_owner_acceptance_cannot_excuse_mismatched_causal_nonce(self):
        self.construct(proof=lambda *_: True)
        data = rotating_inputs(validation_id='causal:' + 'a' * 32)
        result = self.adapter.offer(*data, received_at=self.now - .1)
        self.assertEqual(result['status'], 'refused', result)
        self.assertIsNone(result['capture'])
        self.assertEqual(self.snapshot()['items'], [])

    def test_previous_full_receipt_proof_cannot_authorize_a_different_current_header(self):
        self.construct()
        previous = rotating_inputs()
        self.expected_receipt = fixtures.decoded_header(previous[1])
        current = rotating_inputs(8)
        self.now = fixtures.NOW + 1.2
        result = self.adapter.offer(*current, received_at=self.now - .1)
        self.assertEqual(result['status'], 'refused', result)
        self.assertIsNone(result['capture'])
        self.assertEqual(self.snapshot()['items'], [])


if __name__ == '__main__': unittest.main()
