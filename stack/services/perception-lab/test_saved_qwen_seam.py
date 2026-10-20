"""End-to-end saved Qwen seam with fakes only: Victoria controller, analysis and
mTLS-shaped transport against the LA consumer routes, the shared admission
dispatcher and an in-memory model endpoint. Nothing leaves loopback."""
import hashlib
import http.client
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from config import Settings
from controller import Controller
from managed_pair import ManagedPair
from qwen_transport import saved_analysis
from qwen_adapter import MODEL, PROMPT_VERSION
from qwen_transport import QwenTransport
from store import Store
from home_shared.dispatch_service import DispatchPump
import test_lab_saved_activity as la
from test_managed_pair import Client, Inner


class SavedQwenSeamTests(la.LaFixture, la.LabHttpHarness, unittest.TestCase):
    def setUp(self):
        self.build()
        self.start_http()
        self.pump = DispatchPump(self.gateway, poll_seconds=.01)
        self.pump.start()
        self.temp = tempfile.TemporaryDirectory()
        (Path(self.temp.name) / 'token').write_bytes(la.LAB_TOKEN)
        self.store = Store(Settings(Path(self.temp.name) / 'lab', max_session_seconds=60, cameras=({'id': 'den'},)))
        self.session = self.saved_session()
        self.runtime = None

    def tearDown(self):
        if self.runtime is not None:
            self.runtime.close(timeout=5)
        self.store.close()
        self.temp.cleanup()
        self.pump.stop(timeout_seconds=5)
        self.stop_http()
        self.close()

    def saved_session(self):
        from PIL import Image
        session, _ = self.store.create_session('den', 'placement-same', 'saved', 'saved1')
        self.store.transition(session['id'], 'starting')
        self.store.transition(session['id'], 'recording')
        started = self.store.get_session(session['id'])['started_at']
        encoded = io.BytesIO()
        Image.new('RGB', (64, 36), (90, 80, 70)).save(encoded, format='JPEG')
        self.stored = encoded.getvalue()
        self.media = self.store.add_media(session['id'], self.stored, 'image/jpeg', {
            'site_id': 'victoria', 'camera_id': 'den', 'stream': 'rgb', 'frame_id': 'f1', 'capture_epoch': 'c1',
            'observed_at': started + 2, 'available_at': started + 2.1, 'uncertainty_seconds': .01,
            'clock_mapping_status': 'verified', 'clock_mapping_id': 'fixture', 'width': 64, 'height': 36})
        self.store.append_event(session['id'], 'perception.output', {
            'site_id': 'victoria', 'camera_id': 'den', 'arm': 'b', 'generation': 1, 'status': 'completed',
            'observed_at': started + 2, 'available_at': started + 2.2, 'valid_until': started + 12,
            'result': {'objects': [{'label': 'book', 'confidence': .8}], 'human_label': 'label-marker-7f3a'}})
        self.store.transition(session['id'], 'stopped')
        with self.store.transaction() as db:
            db.execute('UPDATE sessions SET ended_at=? WHERE id=?', (started + 10, session['id']))
        self.store.save_annotation(session['id'], 'annotation-marker-9c2e', {'at_ms': 0, 'end_ms': 10000, 'label': 'reading',
                                   'origin': 'human', 'reviewed': True}, 0, 'owner')
        return self.store.get_session(session['id'])

    def transport(self, config):
        return QwenTransport(config, connection_factory=lambda timeout: http.client.HTTPConnection(
            '127.0.0.1', self.server.server_port, timeout=timeout))

    def test_saved_analysis_runs_one_admitted_request_at_a_time_and_replays_without_inference(self):
        profile = {'model_version': MODEL, 'served_model': 'qwen3-vl-30b', 'artifact_sha256': 'a' * 64,
                   'engine_epoch': la.EPOCH, 'runtime_revision': 'fixture-runtime', 'adapter_revision': PROMPT_VERSION}
        config = {'hostname': 'la-lab.example', 'port': 9447, 'ca_file': '/fixture/ca.pem', 'client_certificate': '/fixture/client.pem',
                  'client_key': '/fixture/client.key', 'token_file': str(Path(self.temp.name) / 'token'),
                  'deadline_seconds': 15, 'response_grace_seconds': 5, 'poll_seconds': .1}
        jobs = saved_analysis(self.store, profile, config, transport_factory=self.transport)
        self.runtime = ManagedPair(self.store, {a: Client() for a in 'ab'}, lambda _: Inner(), [{'id': 'placement-same'}], analysis_jobs=jobs)
        controller = Controller(self.store, self.runtime)
        job = controller.job({'kind': 'analysis', 'request_id': 'seam1', 'session_id': self.session['id'],
                              'parameters': {'run_ids': ['qBase', 'qA', 'qB'], 'mode': 'causal'}})
        until = time.monotonic() + 30
        while self.store.get_job(job['id'])['state'] not in {'completed', 'failed', 'indeterminate', 'interrupted'}:
            self.assertLess(time.monotonic(), until, 'saved analysis did not finish')
            time.sleep(.05)
        finished = self.store.get_job(job['id'])
        self.assertEqual((finished['state'], finished['result']['steps_done']), ('completed', 6), finished)
        outputs = [e['payload'] for e in self.store.list_events(self.session['id']) if e['kind'] == 'analysis.output']
        self.assertEqual(sorted(o['run_id'] for o in outputs), ['qA', 'qA', 'qB', 'qB', 'qBase', 'qBase'])
        self.assertTrue(all(o['judgment']['activity'] == 'reading' for o in outputs))
        # The model received exactly the bodies Victoria hashed, one at a time.
        uploaded = [hashlib.sha256(body).hexdigest() for _, body in self.upstream.bodies]
        self.assertEqual(sorted(uploaded), sorted(o['processing']['body_sha256'] for o in outputs))
        self.assertEqual(self.upstream.peak, 1)
        for _, body in self.upstream.bodies:
            self.assertNotIn(b'label-marker-7f3a', body)
            self.assertNotIn(b'annotation-marker-9c2e', body)
        with_evidence = [json.loads(json.loads(body)['messages'][1]['content'][0]['text'])['observations'] for _, body in self.upstream.bodies]
        self.assertEqual(sum(1 for rows in with_evidence if rows), 2)  # qB windows only; qBase and qA saw none
        ledger_rows = self.rows()
        # Replay reads stored results only: no admission request, no model call.
        for at_ms in (0, 5000, 9000, 10000):
            controller.detail(self.session['id'], at_ms)
            self.store.outputs_at(self.session['id'], at_ms)
        controller.findings(self.session['id'])
        self.assertEqual((len(self.upstream.bodies), self.rows()), (6, ledger_rows))
        self.assertTrue(self.runtime.capabilities()['analysis']['available'])


if __name__ == '__main__':
    unittest.main()
