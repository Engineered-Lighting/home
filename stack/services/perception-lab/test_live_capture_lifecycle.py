"""Public capture-owner withdrawal and household-buffer cleanup boundary."""
import tempfile
import unittest
from pathlib import Path

import live_capture_fixtures as fixtures
from journal import Journal, PREFIX
from live_test_helpers import LiveHttp
from live_capture import LiveCaptureAdapter


class CaptureLifecycleTests(unittest.TestCase):
    def test_withdraw_clears_originals_and_blocks_new_capture_and_numeric_use(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-capture-lifecycle-') as work:
            now = fixtures.NOW + .2
            config, policy, method, _, grant = fixtures.setup()
            server = None
            def publish(event):
                status, response = server.request('POST', PREFIX + '/events', event, token=fixtures.TOKEN)
                if status != 200:
                    raise ValueError('synthetic journal rejection')
                return response
            def control():
                binding = config['manifest']['sources'][fixtures.SOURCE]['control_clock']
                return {'observed_at': now, 'clock': {'status': 'commissioned',
                    'mapping_id': binding['mapping_id'], 'mapping_digest': binding['mapping_digest'],
                    'source_epoch': binding['source_epoch'], 'original_unit': 'unix_ns',
                    'original_value': str(int(now * 1e9)), 'uncertainty_s': .001}}
            adapter = LiveCaptureAdapter(config, source_id=fixtures.SOURCE, revision=fixtures.REVISION,
                policy=policy, method_ref=method, clock=lambda: now, publish=publish, control_clock=control)
            journal = Journal(Path(work) / 'synthetic.sqlite', grants=[grant], profile=fixtures.PROFILE,
                live_config=config, clock=lambda: now, live_receipt_resolver=adapter.resolve_receipt)
            server = LiveHttp(journal)
            try:
                first = adapter.offer(*fixtures.inputs(), received_at=fixtures.NOW + .1)
                self.assertEqual(first['status'], 'qualified', first['reason'])
                ref = first['publication']['payload']['original_receipt_refs'][0]
                result = adapter.withdraw('operator_withdrawal')
                self.assertTrue(result['buffers_cleared'])
                self.assertEqual(result['withdrawal'], 'recorded')
                with self.assertRaises(KeyError):
                    adapter.original_receipt(ref['store_id'], ref['receipt_id'])
                with self.assertRaises(ValueError):
                    adapter.point_at(first['capture']['id'], [320, 200])
                self.assertEqual(adapter.offer(*fixtures.inputs(), received_at=fixtures.NOW + .1)['reason'], 'capture_adapter_closed')
                status, snapshot = server.request('GET', PREFIX + '/snapshot?site_id=synthetic-site&room_id=synthetic-room', token=fixtures.TOKEN)
                self.assertEqual(status, 200)
                self.assertEqual(snapshot['items'], [])
            finally:
                server.close()
                journal.close()


if __name__ == '__main__':
    unittest.main()
