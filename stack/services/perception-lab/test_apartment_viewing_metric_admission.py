"""Actual installed-parent pump/runner campaign; only external inputs authored.

No manual finalize, no host/model/camera/native calls. Original byte campaigns
are fixed before consumption; the existing 1 Hz source sampling rule is intact.
"""
import base64
from copy import deepcopy
import io
import json
import time
import unittest

import numpy as np
import test_apartment_viewing_metric_source as fixtures
import test_apartment_desk_preview as preview_fixtures
import apartment_desk_preview
import apartment_viewing_metric_bootstrap as bootstrap
from unittest.mock import patch


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class MetricAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MetricSourceTests.setUpClass()
        cls.addClassCleanup(fixtures.MetricSourceTests.doClassCleanups)

    def setUp(self):
        self.f = fixtures.MetricSourceTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.base_headers = {k: json.loads(base64.urlsafe_b64decode(v))
                             for k, v in self.f.headers.items()}
        self.campaign = [self.originals(i) for i in range(3)]
        self.capture_index = 0

    def original_header(self, offset):
        h = deepcopy(self.base_headers['b'])
        h.update(sequence=10 + round(offset * 12),
                 observed_at_unix_ns=1800000000000000000 + round(offset * 10**9),
                 frame_device_ns=50200000000 + round(offset * 10**9),
                 host_send_monotonic_ns=10000000000 + round(offset * 10**9),
                 host_receive_monotonic_ns=10020000000 + round(offset * 10**9),
                 observed_monotonic_seconds=10.01 + offset,
                 uncertainty_seconds=.022001, host_clock_uncertainty_seconds=.002)
        return base64.urlsafe_b64encode(encode(h)).decode()

    def originals(self, index):
        proof = deepcopy(self.f.proof)
        for point in ('before', 'after'):
            for key in proof[point]:
                proof[point][key] += index * 10**9
        for key in ('checked_monotonic_ns', 'checked_unix_ns', 'valid_until_monotonic_ns'):
            proof['evidence'][key] += index * 10**9
        proof['evidence']['uncertainty_seconds'] = .002
        with np.load(io.BytesIO(self.f.originals['npz']), allow_pickle=False) as archive:
            arrays = {k: archive[k].copy() for k in archive.files}
        meta = json.loads(arrays['metadata_json'].tobytes())
        for frame in meta['frames'].values():
            frame['sequence'] += index * 12
            frame['device_timestamp_seconds'] += index
            frame['host_monotonic_timestamp_seconds'] += index
        meta['captured_unix_ns'] += index * 10**9
        arrays['metadata_json'] = np.frombuffer(encode(meta), np.uint8)
        raw = io.BytesIO()
        np.savez_compressed(raw, **arrays)
        return {'npz': raw.getvalue(), 'calibration': self.f.originals['calibration'],
                'proof': proof}

    def build(self, execution_arms=('a', 'b')):
        f = self.f
        alignment, _, originals, config = f.approximate_configuration()
        candidate = encode(preview_fixtures.configured_candidate(alignment.pack))
        import hashlib
        configured_preview = apartment_desk_preview.bind_desk_preview(
            candidate, hashlib.sha256(candidate).hexdigest(), alignment.pack)
        owner = self
        from chrony_preview_client import _CAPTURE_LOCK
        class Client:
            def _read(_, path):
                owner.assertTrue(_CAPTURE_LOCK.locked())
                if path=='/frame.jpg':return f.originals['anchor_b'],owner.original_header(owner.capture_index+.5)
                item = owner.campaign[owner.capture_index]
                return item['npz' if path == '/frame.npz' else 'calibration'], ''
        class Clock:
            def begin(_, receipt):
                owner.assertTrue(_CAPTURE_LOCK.locked())
                if owner.capture_index == 0:
                    raise ValueError('receiver_clock_anchor_changed')
                return receipt
            def finish(_, receipt):
                return deepcopy(owner.campaign[owner.capture_index]['proof'])
        by_path = {config['files'][k]['path']: v for k, v in originals.items()}
        with patch.object(bootstrap, '_read_private', side_effect=lambda p: by_path[p]), \
                patch.object(bootstrap.os, 'lstat', side_effect=FileNotFoundError):
            configured = bootstrap.load_factory(config, client=Client())
        configured.clock = Clock()
        configured.desk_preview = configured_preview
        runtime = f.build(metric_factory=configured, execution_arms=execution_arms)
        runner = runtime.runners[f.session['id']]
        runner.sampling_hz = {'a': 1, 'b': 1}  # Actual trial's authored config.
        model_result = preview_fixtures.add_shoulders(f.event)['payload']['result']
        execute = runtime.executor
        def model(request):
            result = execute(request)
            result['result'] = deepcopy(model_result)
            result['result']['frame_id'] = request['frame']['frame_id']
            return result
        runtime.executor = runner.executor = model
        self.runtime = runtime
        self.source = runtime.apartment_viewing_sessions[f.session['id']]
        self.pump = runtime.preview_pumps[f.session['id']]
        return runtime

    def consume(self, offset, now):
        self.f.now = 1800000000 + now
        return self.pump.consume(self.f.originals['anchor_b'], self.original_header(offset))

    def outputs(self, minimum):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            rows = [e for e in self.f.store.list_events(self.f.session['id'], 0, 500)
                    if e['kind'] == 'perception.output']
            if len(rows) >= minimum:
                return rows
            time.sleep(.005)
        self.fail('actual paired authored model output did not settle')

    def dropped_lease(self, execution_arms=('a', 'b')):
        self.build(execution_arms)
        self.consume(0, .59)
        self.consume(.5, .59)
        self.outputs(len(execution_arms))
        self.capture_index = 1
        # Hold only the new post-offer hook so its exact production terminal
        # rows can exercise refusal cases. No runner/admission result is faked.
        with patch.object(self.source.metric, 'after_offer', return_value=False):
            dropped = self.consume(1.5, 1.59)
        record = self.source.metric.image_record
        frame = {**self.source.metric.lease.image_metadata,
                 'session_id': self.f.session['id'], 'media_ids': [record['id']]}
        with self.f.store.lock:
            rows = self.f.store.db.execute(
                "SELECT id,payload FROM paired_requests WHERE session_id=? AND "
                "json_extract(payload,'$.frame.media_ids[0]')=? ORDER BY arm",
                (self.f.session['id'], dropped['media_ids'][0])).fetchall()
        return frame, rows

    def test_la_only_cadence_drop_releases_exact_unexecuted_lease(self):
        frame, rows = self.dropped_lease(('b',))
        self.assertEqual(len(rows), 1)
        metric = self.source.metric
        capture_at, identity = metric.last_capture_at, metric.last_npz_identity
        runner = self.runtime.runners[self.f.session['id']]
        for scope in ((), ('a',), ('b', 'b'), ('foreign',)):
            runner.execution_arms = scope
            self.assertFalse(metric.after_offer(frame))
        runner.execution_arms = ('b',)
        runner.active['b'] = rows[0]['id']
        self.assertFalse(metric.after_offer(frame))
        runner.active['b'] = None
        runner.pending['b'] = json.loads(rows[0]['payload'])
        self.assertFalse(metric.after_offer(frame))
        runner.pending['b'] = None
        self.assertTrue(metric.after_offer(frame),
                        'LA-only cadence drop must not wait for a nonexistent A request')
        self.assertIsNone(metric.lease)
        self.assertIsNone(metric.image_record)
        self.assertEqual(metric.last_capture_at, capture_at)
        self.assertEqual(metric.last_npz_identity, identity)

    def test_actual_la_only_pump_advances_after_sampling_drop(self):
        self.build(('b',))
        self.consume(0, .59)
        self.consume(.5, .59)
        self.outputs(1)
        self.capture_index = 1
        self.consume(1.5, 1.59)
        self.assertIsNone(self.source.metric.lease,
                          'actual LA-only pump must retire a sampling-dropped lease immediately')
        self.capture_index = 2
        admitted = self.consume(2.5, 2.59)
        outputs = self.outputs(2)
        matching = [e for e in outputs if e['payload']['media_ids']==admitted['media_ids']]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['payload']['arm'], 'b')
        self.consume(2.6, 2.7)
        self.assertEqual(self.source.metric.projected, 1)

    def test_only_both_exact_cadence_drops_without_possible_execution_release(self):
        frame, rows = self.dropped_lease()
        metric = self.source.metric
        lease, record = metric.lease, metric.image_record
        runner = self.runtime.runners[self.f.session['id']]
        capture_at, identity = metric.last_capture_at, metric.last_npz_identity
        for state in ('offered', 'dispatching', 'running', 'indeterminate',
                      'rejected', 'cancelled', 'completed', 'expired'):
            with self.subTest(state=state):
                with self.f.store.transaction() as db:
                    db.execute('UPDATE paired_requests SET state=? WHERE id=?', (state, rows[0]['id']))
                self.assertFalse(metric.after_offer(frame))
                self.assertIs(metric.lease, lease)
                with self.f.store.transaction() as db:
                    db.execute("UPDATE paired_requests SET state='dropped' WHERE id=?", (rows[0]['id'],))
        for key, value in (('frame_id', 'foreign'), ('capture_epoch', 'foreign'),
                           ('site_id', 'foreign'), ('camera_id', 'foreign'),
                           ('session_id', 'foreign'), ('observed_at', frame['observed_at']-.1),
                           ('valid_until', frame['valid_until']+.1), ('media_ids', ['foreign'])):
            with self.subTest(binding=key):
                self.assertFalse(metric.after_offer({**frame, key: value}))
                self.assertIs(metric.lease, lease)
        original = rows[0]['payload']
        for field in ('generation', 'profile'):
            changed = json.loads(original)
            changed[field] = 999 if field == 'generation' else {**changed[field], 'engine_epoch': 'foreign'}
            with self.f.store.transaction() as db:
                db.execute('UPDATE paired_requests SET payload=? WHERE id=?', (encode(changed).decode(), rows[0]['id']))
            self.assertFalse(metric.after_offer(frame))
            self.assertIs(metric.lease, lease)
            with self.f.store.transaction() as db:
                db.execute('UPDATE paired_requests SET payload=? WHERE id=?', (original, rows[0]['id']))
        runner.active['a'] = rows[0]['id']
        self.assertFalse(metric.after_offer(frame)); runner.active['a'] = None
        runner.pending['a'] = json.loads(original)
        self.assertFalse(metric.after_offer(frame)); runner.pending['a'] = None
        self.assertTrue(metric.after_offer(frame))
        self.assertIsNone(metric.lease); self.assertIsNone(metric.image_record)
        self.assertEqual(metric.last_capture_at, capture_at)
        self.assertEqual(metric.last_npz_identity, identity)
        self.assertIs(self.source.entries[record['id']]['data'], lease.image_bytes)

    def test_stop_and_original_expiry_never_revive_released_or_late_lease(self):
        frame, _ = self.dropped_lease()
        self.f.now = self.source.metric.lease.descriptor['retirement_deadline']
        self.source.prune()
        self.assertFalse(self.source.metric.after_offer(frame))
        self.assertIsNone(self.source.metric.lease)
        self.assertEqual(self.source.metric.reason, 'metric_original_expired')
        self.runtime.stop_session(self.f.session)
        self.assertFalse(self.source.metric.after_offer(frame))
        self.assertEqual(self.source.status()['resident_bytes'], 0)

    def test_cadence_drop_releases_unexecuted_lease_and_next_real_pump_cycle_projects(self):
        runtime = self.build()
        self.consume(0, .59)
        fallback = self.consume(.5, .59)
        self.outputs(2)
        self.assertTrue(fallback['media_ids'])
        self.assertEqual(self.source.metric.reason, 'receiver_clock_anchor_changed')
        self.capture_index = 1
        dropped = self.consume(1.5, 1.59)
        with self.f.store.lock:
            rows = self.f.store.db.execute(
                "SELECT state,result,payload FROM paired_requests WHERE session_id=? AND "
                "json_extract(payload,'$.frame.media_ids[0]')=? ORDER BY arm",
                (self.f.session['id'], dropped['media_ids'][0])).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['state'] == 'dropped' and
                            json.loads(r['result']) == {'reason': 'source_sampling_cadence'} for r in rows))
        self.assertTrue(self.source.metric.lease is None,
                          'both arms provably never execute, but metric lease still blocks the pump until expiry')
        before = self.source.accepted
        self.consume(2, 2.09)
        self.assertEqual(self.source.accepted, before, 'metric wait must not consume its next slot with JPEG')
        self.capture_index = 2
        admitted = self.consume(2.5, 2.59)
        outputs = self.outputs(4)
        matching = [e for e in outputs if e['payload']['media_ids'] == admitted['media_ids']]
        self.assertEqual(len(matching), 2)
        # Only the next genuine incumbent pump callback finalizes the result.
        self.consume(2.6, 2.7)
        arm = runtime.live(self.f.session)['arms']['B']
        self.assertTrue(arm['virtual_target_preview']['available'], arm['virtual_target_preview'])
        self.assertEqual(self.source.metric.projected, 1)
        self.assertEqual(arm['virtual_target_preview']['frame']['frame_id'], matching[-1]['payload']['frame_id'])
        runtime.stop_session(self.f.session)
        self.assertEqual(self.source.status()['resident_bytes'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
