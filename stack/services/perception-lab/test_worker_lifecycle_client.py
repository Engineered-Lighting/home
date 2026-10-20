import json
import unittest
from worker_lifecycle_client import LifecycleClient


class Transport:
    status = 202
    def __init__(self, receipt):
        self.receipt = receipt
        self.requests = []
    def request(self, *args):
        self.requests.append(args)
    def getresponse(self):
        return self
    def read(self, maximum):
        return json.dumps(self.receipt).encode()[:maximum]
    def close(self):
        pass


class ClientTests(unittest.TestCase):
    def test_site_and_session_binding_cannot_change(self):
        rid = '12345678-1234-1234-1234-123456789abc'
        sid = 'a' * 32
        transport = Transport(dict(version=1, request_id=rid, session_id=sid, site_id='echo',
                                   state='starting', engine_epoch='la-live-test'))
        client = LifecycleClient('https://192.168.0.100:9443', lambda: 'x'*32, None, 'echo',
                                 connection_factory=lambda: transport)
        self.assertEqual(client.start(rid, sid)['state'], 'starting')
        self.assertEqual(transport.requests[0][1], '/v1/lifecycle/start')
        transport.receipt['site_id'] = 'victoria'
        with self.assertRaises(ValueError):
            client.status(rid)
        with self.assertRaises(ValueError):
            client.start(rid, 'b'*32)
        self.assertEqual(len(transport.requests), 2)

    def test_only_provisioned_private_origins(self):
        with self.assertRaises(ValueError):
            LifecycleClient('https://example.org', lambda: 'x'*32, None, 'echo')


class JetsonGateReasonTests(unittest.TestCase):
    def client(self, directory, error):
        from pathlib import Path
        from unittest.mock import patch
        from lifecycle import Lifecycle
        from jetson_lifecycle import JetsonLauncher, JetsonLifecycleClient
        launcher = JetsonLauncher('0123456789abcdef')
        client = object.__new__(JetsonLifecycleClient)
        client.lifecycle = Lifecycle(Path(directory)/'lifecycle.sqlite', launcher, site_id='victoria',
                                     epoch_prefix='jetson-live-', lease_seconds=115)
        self.addCleanup(lambda: client.lifecycle.closed or client.lifecycle.close())
        with patch.object(JetsonLauncher, 'health', side_effect=error):
            receipt = client.start('12345678-1234-1234-1234-123456789abc', 'a'*32)
            client.lifecycle.thread.join(5)
        return client, receipt

    def test_preflight_gate_code_reaches_the_receipt_without_changing_the_ledger(self):
        import tempfile
        rid = '12345678-1234-1234-1234-123456789abc'
        for message, gate in (('Memory headroom unavailable', 'preflight_memory'),
                              ('Thermal headroom unavailable', 'preflight_thermal'),
                              ('/proc/meminfo unreadable', 'preflight_inspection_error')):
            with tempfile.TemporaryDirectory() as directory:
                client, receipt = self.client(directory, ValueError(message))
                self.assertEqual(receipt['state'], 'starting')
                status = client.status(rid)
                self.assertEqual((status['state'], status['reason']), ('failed', gate))
                self.assertEqual(client.stop(rid, status['engine_epoch'])['reason'], gate)
                durable = client.lifecycle.db.execute('SELECT reason FROM leases').fetchone()[0]
                self.assertEqual(durable, 'preflight_rejected')
                self.assertTrue(client.lifecycle.close())

    def test_other_failures_keep_their_lifecycle_reason(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            client, _ = self.client(directory, ValueError('Memory headroom unavailable'))
            client.lifecycle.launcher.gates.clear()
            status = client.status('12345678-1234-1234-1234-123456789abc')
            self.assertEqual(status['reason'], 'preflight_rejected')
            with patch.dict(client.lifecycle.launcher.gates, {status['engine_epoch']: 'arbitrary text'}):
                self.assertEqual(client.status('12345678-1234-1234-1234-123456789abc')['reason'], 'preflight_rejected')
            self.assertTrue(client.lifecycle.close())


if __name__ == '__main__':
    unittest.main()
