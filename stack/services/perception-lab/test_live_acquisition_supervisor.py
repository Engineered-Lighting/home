"""Supervised stop retains original receipt authority until explicit drain."""
from copy import deepcopy
import unittest

from test_live_acquisition import ProductionAcquisitionTests


class AcquisitionSupervisorTests(unittest.TestCase):
    setUp = ProductionAcquisitionTests.setUp
    camera_pair = ProductionAcquisitionTests.camera_pair
    control_clock = ProductionAcquisitionTests.control_clock
    construct = ProductionAcquisitionTests.construct
    snapshot = ProductionAcquisitionTests.snapshot

    def test_quiesce_withdraw_preserve_http_and_originals_until_bound_drain(self):
        graph = self.construct(shutdown_owner='supervisor')
        self.assertEqual(graph.readiness()['state'], 'idle')
        self.assertEqual(graph.step()['status'], 'observed')
        ref = self.snapshot()['items'][0]['payload']['original_receipt_refs'][0]
        self.assertEqual(graph.quiesce()['status'], 'quiescent')
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['status'], 'withdrawn', withdrawal)
        self.assertEqual(self.snapshot()['items'], [])
        original = graph.capture.original_receipt(ref['store_id'], ref['receipt_id'])
        self.assertIsInstance(original, dict)
        self.assertEqual(graph.finalize()['status'], 'refused')
        drain = {'schema': 'home-acquisition-drain-1', 'version': 1,
            'session_ref': deepcopy(self.config['session_ref']), 'configuration_digest': graph.configuration_digest,
            'withdrawal_receipt': withdrawal['barrier_receipt'], 'recorder_status': 'drained',
            'worker_status': 'settled', 'terminal_cursor': withdrawal['input_cursor']}
        wrong = deepcopy(drain)
        wrong['configuration_digest'] = '0' * 64
        self.assertEqual(graph.finalize(drain_receipt=wrong)['status'], 'refused')
        self.assertEqual(graph.capture.original_receipt(ref['store_id'], ref['receipt_id']), original)
        result = graph.finalize(drain_receipt=drain)
        self.assertEqual(result['status'], 'stopped', result)
        self.assertTrue(result['buffers_cleared'])
        with self.assertRaises(KeyError):
            graph.capture.original_receipt(ref['store_id'], ref['receipt_id'])
        self.assertEqual(graph.finalize(drain_receipt=drain), result)

    def test_unconfirmed_withdrawal_keeps_originals_until_explicit_incomplete_cleanup(self):
        self.journal_grant['live_bindings'][0]['capabilities'].remove('control')
        graph = self.construct(shutdown_owner='supervisor')
        self.assertEqual(graph.step()['status'], 'observed')
        ref = self.snapshot()['items'][0]['payload']['original_receipt_refs'][0]
        withdrawal = graph.withdraw()
        self.assertEqual(withdrawal['withdrawal'], 'not_recorded')
        self.assertIsInstance(graph.capture.original_receipt(ref['store_id'], ref['receipt_id']), dict)
        drain = {'schema': 'home-acquisition-drain-1', 'version': 1,
            'session_ref': deepcopy(self.config['session_ref']), 'configuration_digest': graph.configuration_digest,
            'withdrawal_receipt': None, 'recorder_status': 'incomplete', 'worker_status': 'unconfirmed', 'terminal_cursor': 1}
        result = graph.finalize(drain_receipt=drain)
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertTrue(result['buffers_cleared'])
        self.assertEqual(result['withdrawal'], 'not_recorded')

    def test_revocation_does_not_close_supervised_journal_before_drain(self):
        graph = self.construct(shutdown_owner='supervisor')
        self.permitted = False
        self.assertEqual(graph.step()['status'], 'blocked')
        self.assertEqual(self.snapshot()['items'], [])
        self.assertEqual(graph.readiness()['state'], 'quiescing')
        withdrawal = graph.withdraw()
        drain = {'schema': 'home-acquisition-drain-1', 'version': 1,
            'session_ref': deepcopy(self.config['session_ref']), 'configuration_digest': graph.configuration_digest,
            'withdrawal_receipt': withdrawal['barrier_receipt'], 'recorder_status': 'drained',
            'worker_status': 'settled', 'terminal_cursor': withdrawal['input_cursor']}
        self.assertEqual(graph.finalize(drain_receipt=drain)['status'], 'stopped')


if __name__ == '__main__':
    unittest.main()
