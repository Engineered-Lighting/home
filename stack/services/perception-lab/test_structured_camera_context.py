"""Read-only always-on context: original receipts, no sender or file replay."""
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

import la_fact_log
import la_live_perceiver
import qwen_scene as scene
from structured_camera_context import StructuredCameraContext, missing_context


NOW = 1_800_000_000.0
PROVIDER = dict(state='running', pump_status='ready', last_reason=None,
                outstanding=False, halted=False)
FACTS = {key: values[0] for key, values in scene.FIELDS.items()}
FACTS.update(change_vs_prior='no_prior', visible_basis=['person_visible'])


def original(index=1, observed=NOW, *, content=None):
    frames = [dict(media_id='ring-' + scene.digest([index, n]), frame_id=f'capture-0-{index}-{n}',
                   capture_epoch='capture-0', observed_at=observed - 1 + n) for n in range(2)]
    reference = dict(request_id=scene.digest(['request', index]), session_id='ab' * 16,
                     site_id='victoria', body_sha256=scene.digest(['body', index]),
                     prompt_version=scene.PROMPT_VERSION)
    draft = scene.build_observation(site_id='victoria', camera_id='den',
        session_id=reference['session_id'], request_id=reference['request_id'], frames=frames,
        generated_at=observed + .2, prompt_version=scene.PROMPT_VERSION,
        context=scene.model_context('evening 19:40'), body_sha256=reference['body_sha256'],
        engine_epoch='actual-fixture-engine', artifact_sha256='a' * 64,
        content=json.dumps(FACTS) if content is None else content,
        finish_reason='stop', prior_supplied=False)
    return (dict(reference=reference, frames=frames, sent_at=observed + .1,
                 profile=dict(engine_epoch='actual-fixture-engine')),
            scene.first_receipt(draft, observed + .3))


class StructuredCameraContextTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW + 1
        self.cache = StructuredCameraContext(site_id='victoria', camera_id='den', clock=lambda: self.now)

    def test_original_daily_scope_and_exclusive_expiry_never_refresh(self):
        metadata, receipt = original()
        self.assertEqual(self.cache.observe(metadata, receipt), 'accepted')
        result = self.cache.snapshot(PROVIDER)
        self.assertEqual(result['latest'], dict(metadata=metadata, receipt=receipt))
        self.assertEqual(result['scope'], dict(kind='perceiver_daily',
            perceiver_session_id=receipt['session_id'], lab_session_id=None))
        self.assertEqual(result['freshness'], 'current')
        self.now = receipt['valid_until']
        expired = self.cache.snapshot(PROVIDER)
        self.assertEqual(expired['freshness'], 'stale')
        self.assertEqual(expired['latest']['receipt'], receipt)
        self.assertEqual(expired['server_time'], self.now)
        self.assertIsNone(expired['qualification'])
        self.assertIs(expired['physical_commands'], False)

    def test_callback_and_snapshot_are_copies_and_readback_has_no_io(self):
        metadata, receipt = original()
        expected = deepcopy(dict(metadata=metadata, receipt=receipt))
        with patch('builtins.open', side_effect=AssertionError('file I/O')), \
             patch('socket.socket', side_effect=AssertionError('network I/O')), \
             patch('subprocess.Popen', side_effect=AssertionError('child process')):
            self.assertEqual(self.cache.observe(metadata, receipt), 'accepted')
            metadata['frames'][0]['frame_id'] = 'changed'
            receipt['facts']['visible_basis'].append('changed')
            result = self.cache.snapshot(PROVIDER)
            result['latest']['receipt']['facts']['visible_basis'].clear()
            self.assertEqual(self.cache.snapshot(PROVIDER)['latest'], expected)

    def test_closed_shape_vocabulary_scope_and_original_links_refuse(self):
        metadata, receipt = original()
        changes = [
            lambda m, r: r.update(extra='private prose'),
            lambda m, r: r['facts'].update(activity_hypothesis='turn_on_light'),
            lambda m, r: m['frames'][0].update(frame_id='foreign'),
            lambda m, r: m['reference'].update(session_id='cd' * 16),
            lambda m, r: r.update(camera_id='foreign'),
            lambda m, r: m.update(sent_at=NOW + 5),
            lambda m, r: r.update(fingerprint='f' * 64),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                m, r = deepcopy(metadata), deepcopy(receipt)
                change(m, r)
                self.assertEqual(self.cache.observe(m, r), 'refused')
                self.assertIsNone(self.cache.snapshot(PROVIDER)['latest'])

    def test_invalid_model_reply_stays_an_invalid_original(self):
        metadata, receipt = original(content='not JSON')
        self.assertEqual(self.cache.observe(metadata, receipt), 'accepted')
        result = self.cache.snapshot(PROVIDER)
        self.assertEqual(result['freshness'], 'invalid')
        self.assertEqual(result['latest']['receipt'], receipt)
        self.assertIsNone(result['latest']['receipt']['facts'])

    def test_late_or_conflicting_receipt_cannot_replace_newer_original(self):
        current = original(2, NOW)
        self.assertEqual(self.cache.observe(*current), 'accepted')
        late = original(1, NOW - 2)
        self.assertEqual(self.cache.observe(*late), 'out_of_order')
        retry = deepcopy(current)
        retry[1]['available_at'] += .1
        retry[1]['first_received_at'] += .1
        self.assertEqual(self.cache.observe(*retry), 'conflict')
        self.assertEqual(self.cache.observe(*current), 'duplicate')
        self.assertEqual(self.cache.snapshot(PROVIDER)['latest'],
                         dict(metadata=current[0], receipt=current[1]))

    def test_bad_read_clock_is_invalid_and_provider_state_is_independent(self):
        self.cache.observe(*original())
        paused = dict(PROVIDER, state='running', pump_status='paused', last_reason='request_indeterminate', halted=True)
        self.assertEqual(self.cache.snapshot(paused)['freshness'], 'current')
        self.assertEqual(self.cache.snapshot(paused)['provider'], paused)
        self.now = float('nan')
        result = self.cache.snapshot(paused)
        self.assertEqual(result['freshness'], 'invalid')
        self.assertIsNone(result['server_time'])

    def test_missing_projection_is_explicit_and_contains_no_lab_session(self):
        result = missing_context(site_id='victoria', camera_id='den', server_time=NOW)
        self.assertEqual(result['freshness'], 'missing')
        self.assertEqual(result['provider']['state'], 'unavailable')
        self.assertIsNone(result['latest'])
        self.assertEqual(result['scope'], dict(kind='perceiver_daily', perceiver_session_id=None, lab_session_id=None))

    def test_perceiver_subscribes_before_start_and_stop_unsubscribes(self):
        class Transport:
            config = dict(deadline_seconds=8)
            def close(self): return None

        class Reader:
            def __call__(self): return True
            def stop(self, timeout=None): return None
            def snapshot(self): return dict(state='occupied')

        with tempfile.TemporaryDirectory() as directory:
            log = la_fact_log.FactLog(directory, private=False, clock=lambda: self.now)
            settings = la_live_perceiver.check_config(dict(enabled=True,
                transport_file='/authored/transport.json', artifact_sha256='a' * 64,
                runtime_revision='authored-runtime', fact_log_directory='/authored/log'))
            perceiver = la_live_perceiver.LaLivePerceiver(transport=Transport(),
                profile=dict(model_version=scene.MODEL, served_model=scene.SERVED_MODEL,
                    artifact_sha256='a' * 64, engine_epoch='actual-fixture-engine',
                    runtime_revision='authored-runtime', adapter_revision=scene.PROMPT_VERSION),
                reader=Reader(), fact_log=log, granted=lambda: False,
                fetch=lambda: self.fail('capture must never run'), settings=settings,
                site_id='victoria', camera_id='den', clock=lambda: self.now,
                local_time=lambda epoch: (19, 40))
            try:
                self.assertEqual(perceiver.state, 'idle')
                self.assertEqual(len(log.receipt_subscribers), 1)
                metadata, receipt = original()
                callback = next(iter(log.receipt_subscribers))
                callback(metadata, receipt)
                self.assertEqual(perceiver.structured_context()['latest'], dict(metadata=metadata, receipt=receipt))
                self.assertFalse(perceiver.pump.thread.is_alive())
                self.assertFalse(perceiver.capture.thread.is_alive())
                perceiver.stop()
                self.assertEqual(len(log.receipt_subscribers), 0)
                self.assertEqual(callback(*original(2, NOW + .1)), 'closed')
                self.assertEqual(perceiver.structured_context()['provider']['state'], 'stopped')
            finally:
                perceiver.stop()


if __name__ == '__main__':
    unittest.main()
