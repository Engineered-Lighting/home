"""Causal Qwen caption context through the real runtime, scheduler, provider and egress policy."""
import json
import os
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import test_paired_runtime as fixtures
from caption_history import CaptionHistory, UnavailableCaption, latest_caption
from jev_evidence import project
from jev_policy import LabJevPolicy
from jev_provider import JevProvider
from jev_semantics import QUESTIONS as SUPPORT
from live_inputs import live_records
from live_reasoning import LiveReasoning
from reasoning import ACTIVITIES
from service_runtime import ServiceRuntime


class Recorder:
    def start(self, session):
        return {'state': 'recording'}

    def stop(self, session):
        return {'state': 'stopped'}


class CaptionReasoningTests(unittest.TestCase):
    def setUp(self):
        fixtures.PairedRuntimeTests.setUp(self)
        self.now = 100.0
        self.store.clock = lambda: self.now
        self.store.settings = replace(self.store.settings, cameras=({'id': 'den'},))
        self.caption = None
        self.history = CaptionHistory(self.store, 'unused', 'den', reader=lambda *_: {**self.caption, 'status': 'current'})
        self.calls, self.runtimes = [], []
        self.policy = {'schema_version': 1, 'enabled': True, 'scope': 'household_structured', 'revision': 'lab1',
                       'generation': 1, 'expires_at': 500, 'site_id': 'victoria', 'camera_ids': ['den'],
                       'model_selector': 'jev-1.13', 'max_calls_per_minute': 120, 'historical_caption': 'caption-activity-1'}

    def tearDown(self):
        for runtime in self.runtimes:
            self.assertTrue(runtime.close(timeout=5)['drained'])
            for reasoner in runtime.reasoners.values():
                reasoner.pool.shutdown(wait=True)
        fixtures.PairedRuntimeTests.tearDown(self)

    def transport(self, body, token, timeout):
        packet = json.loads(body)
        self.calls.append(packet)
        answers = {'activity': {'type': 'choice', 'choice': 'reading', 'confidence': .6,
                                'probabilities': {k: float(k == 'reading') for k in ACTIVITIES}}}
        answers.update({k: {'type': 'noul', 'noul': .5} for k in SUPPORT})
        return json.dumps({'model': 'jev-1.13.0', 'answers': answers}).encode()

    def runtime(self, caption_evidence, windows=None):
        def factory(store, sid):
            gate = LabJevPolicy(store, lambda: dict(self.policy), lambda: {'state': 'on', 'received_at': self.now})
            provider = JevProvider(model_selector='jev-1.13', response_version='jev-1.13.0', authorize=gate,
                                   credential=lambda: 'fixture', transport=self.transport, clock=lambda: self.now,
                                   allow_recent_history=True, semantic=True)
            return LiveReasoning(store, sid, provider)
        prepared = {'state': 'ready', 'profiles': self.profiles, 'reasoning_windows': windows or {'a': 0, 'b': 10}}
        if caption_evidence is not None:
            prepared['caption_evidence'] = caption_evidence
        runtime = ServiceRuntime(self.store, {'den': Recorder()}, lambda *_: dict(prepared), lambda r: None,
                                 reasoning_factory=factory)
        self.runtimes.append(runtime)
        self.assertEqual(runtime.start_session(self.session)['inference'], 'ready')
        return runtime

    def ingest(self, request, observed, generated, at):
        self.now = at
        self.caption = dict(site_id='victoria', camera_id='den', processing_site_id='echo', model='qwen3-vl-30b',
                            request_id=request, frame_id='pilot-'+request, observed_at=observed, generated_at=generated,
                            valid_until=observed+180, timestamp_semantics='fixture', uncertainty_seconds=0,
                            text='The image shows a living room. A person is sitting on a couch, reading a book.')
        return self.history.poll(self.session['id'])

    def visual(self, arm, observed, valid):
        self.store.append_event(self.session['id'], 'perception.output', {
            'site_id': 'victoria', 'camera_id': 'den', 'arm': arm, 'generation': 1, 'status': 'completed',
            'observed_at': observed, 'available_at': observed+.1, 'valid_until': valid, 'result': {'objects': [{'label': 'book'}]}})

    def offer(self, runtime, cutoff, expected_calls):
        self.now = max(self.now, cutoff)
        records = live_records(self.store, self.session, 1, cutoff, window_seconds=10, retain_history=True)
        runtime.offer_reasoning(self.session['id'], records, generation=1, cutoff=cutoff)
        deadline = time.monotonic()+10
        def settled():
            with self.store.lock:
                return not self.store.db.execute("""SELECT 1 FROM lab_reasoning_requests
                    WHERE state IN ('pending','dispatching')""").fetchone()
        while not settled() or len(self.calls) < expected_calls:
            if time.monotonic() > deadline:
                self.fail('bounded fixture reasoning did not settle')
            time.sleep(.01)
        return {p['state']['evidence'][0]['arm']: p for p in self.calls}

    def captions(self, packet):
        return [r for r in packet['state']['evidence'] if r['source_kind'] == 'caption.receipt']

    def deliveries(self):
        return sorted((e['payload']['arm'], e['payload']['state'], e['payload']['status']) for e in
                      self.store.list_events(self.session['id']) if e['kind'] == 'reasoning.delivery')

    def test_windows_zero_and_ten_get_identical_caption_context_and_replay_selects_it(self):
        runtime = self.runtime({'a': True, 'b': True})
        self.assertEqual(self.ingest('r1', observed=85, generated=88, at=100)['state'], 'available')
        for arm in ('a', 'b'):
            self.visual(arm, 99.5, 102)
        self.visual('b', 92, 94)  # only the 10 s arm retains this history
        packets = self.offer(runtime, 100, 2)
        self.assertEqual([len(p['state']['evidence']) for _, p in sorted(packets.items())], [2, 3])
        a, b = (self.captions(packets[arm]) for arm in ('a', 'b'))
        self.assertEqual((len(a), len(b)), (1, 1))
        # 15 s old: outside both arms' windows, still shared and within validity.
        self.assertEqual({k: v for k, v in a[0].items() if k != 'arm'}, {k: v for k, v in b[0].items() if k != 'arm'})
        self.assertEqual(a[0]['historical_caption']['activity'], 'reading')
        self.assertEqual(packets['a']['state']['semantic']['historical_caption'], packets['b']['state']['semantic']['historical_caption'])
        self.assertEqual(packets['a']['state']['semantic']['historical_caption']['age_seconds'], 15)
        # Replay/saved selection is the same pure query, before and after stop.
        selected = latest_caption(self.store, self.session, 100)
        self.assertEqual(project([{**selected, 'arm': 'a', 'generation': 1}]), a)
        self.store.transition(self.session['id'], 'stopping')
        self.store.transition(self.session['id'], 'stopped')
        self.assertEqual(latest_caption(self.store, self.session, 100), selected)
        # The local caption text never leaves: not in any stored or sent packet.
        stored = [e['payload'] for e in self.store.list_events(self.session['id']) if e['kind'] == 'reasoning.request']
        self.assertEqual(len(stored), 2)
        self.assertNotIn('living room', json.dumps(stored)+json.dumps(self.calls))

    def test_caption_received_after_the_cutoff_is_excluded(self):
        runtime = self.runtime({'a': True, 'b': True})
        for arm in ('a', 'b'):
            self.visual(arm, 99.5, 102)
        self.ingest('r1', observed=95, generated=96, at=101)
        packets = self.offer(runtime, 100, 2)
        self.assertEqual([self.captions(p) for p in packets.values()], [[], []])
        self.assertIsNone(latest_caption(self.store, self.session, 100))
        self.assertIsNotNone(latest_caption(self.store, self.session, 101))

    def test_expired_caption_is_excluded_despite_retained_history(self):
        runtime = self.runtime({'a': True, 'b': True})
        self.ingest('r1', observed=85, generated=88, at=100)
        for arm in ('a', 'b'):
            self.visual(arm, 114, 115)
        packets = self.offer(runtime, 116, 2)
        for packet in packets.values():
            self.assertEqual(self.captions(packet), [])
            # The expired visual record is retained as history; the caption is not.
            self.assertEqual([r['valid_until'] for r in packet['state']['evidence']], [115])
            self.assertEqual(packet['state']['semantic']['source_coverage']['qwen_caption'], 'not_supplied')

    def test_caption_only_context_never_calls_the_provider(self):
        runtime = self.runtime({'a': True, 'b': True})
        self.ingest('r1', observed=95, generated=96, at=100)
        self.offer(runtime, 100, 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.deliveries(), [('a', 'completed', 'missing_evidence'), ('b', 'completed', 'missing_evidence')])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM lab_jev_egress').fetchone()[0], 0)

    def test_unapproved_caption_is_rejected_by_policy_before_transport(self):
        del self.policy['historical_caption']
        runtime = self.runtime({'a': False, 'b': True})
        self.ingest('r1', observed=95, generated=96, at=100)
        for arm in ('a', 'b'):
            self.visual(arm, 99.5, 102)
        packets = self.offer(runtime, 100, 1)
        self.assertEqual(set(packets), {'a'})
        self.assertEqual(self.captions(packets['a']), [])
        self.assertEqual(self.deliveries(), [('a', 'completed', 'ready'), ('b', 'failed', 'failed')])
        with self.store.lock:
            reason = self.store.db.execute("SELECT result FROM lab_reasoning_requests WHERE arm='b'").fetchone()[0]
        self.assertEqual(json.loads(reason)['reason'], 'household_egress_not_authorized:caption_not_permitted')

    def test_ablation_grants_only_the_recipe_arm_and_activation_records_it(self):
        runtime = self.runtime({'a': False, 'b': True})
        self.ingest('r1', observed=95, generated=96, at=100)
        for arm in ('a', 'b'):
            self.visual(arm, 99.5, 102)
        packets = self.offer(runtime, 100, 2)
        self.assertEqual((len(self.captions(packets['a'])), len(self.captions(packets['b']))), (0, 1))
        [activation] = [e['payload'] for e in self.store.list_events(self.session['id']) if e['kind'] == 'experiment.activated']
        self.assertEqual(activation['caption_evidence'], {'a': False, 'b': True})

    def test_recipe_without_caption_field_withholds_captions_from_both_arms(self):
        runtime = self.runtime(None)
        self.ingest('r1', observed=95, generated=96, at=100)
        for arm in ('a', 'b'):
            self.visual(arm, 99.5, 102)
        packets = self.offer(runtime, 100, 2)
        self.assertEqual([self.captions(p) for p in packets.values()], [[], []])
        [activation] = [e['payload'] for e in self.store.list_events(self.session['id']) if e['kind'] == 'experiment.activated']
        self.assertEqual(activation['caption_evidence'], {'a': False, 'b': False})


class CaptionProvisioningTests(unittest.TestCase):
    def setUp(self):
        fixtures.PairedRuntimeTests.setUp(self)
        self.store.settings = replace(self.store.settings, cameras=({'id': 'den'},))
    tearDown = fixtures.PairedRuntimeTests.tearDown

    def build(self, **extra):
        from live_detector_app import build_runtime
        from test_ha_provisioning import live_config
        return build_runtime(self.store, SimpleNamespace(camera_id='den'), live_config(**extra),
                             transport_factory=lambda *_: (lambda *a: None), lease=SimpleNamespace(ready=lambda: False))

    def test_opt_in_config_and_refresh_path_binding(self):
        from live_detector_app import validate_config, PROCESS_STARTED_AT
        from test_ha_provisioning import live_config
        self.assertTrue(validate_config(live_config(caption_history=True))['caption_history'])
        for invalid in (False, 'yes', 1):
            with self.assertRaises(ValueError):
                validate_config(live_config(caption_history=invalid))
        runtime = self.build()
        try:
            self.assertEqual(runtime.caption_sources, {})
        finally:
            runtime.close()
        with patch('caption_history.refresh_publisher_binding', return_value=('/state/shared-caption.json', 'rev-1')) as binding:
            runtime = self.build(caption_history=True)
        try:
            source = runtime.caption_sources['den']
            self.assertIs(type(source), CaptionHistory)
            self.assertEqual((source.path, source.model_revision, source.revision_since),
                             ('/state/shared-caption.json', 'rev-1', PROCESS_STARTED_AT))
            binding.assert_called_once_with(PROCESS_STARTED_AT)
        finally:
            runtime.close()
        # Outside the managed refresh composition the binding fails closed.
        with patch.dict('os.environ'):
            os.environ.pop('PERCEPTION_LAB_REFRESH_CONFIG', None)
            runtime = self.build(caption_history=True)
        try:
            self.assertIs(type(runtime.caption_sources['den']), UnavailableCaption)
        finally:
            runtime.close()

    def test_recipe_granting_captions_requires_reader_and_household_grant(self):
        from live_detector_app import check_caption_provisioning
        recipe = {'id': 'ablation', 'a': {'caption_evidence': False}, 'b': {'caption_evidence': True}}
        grant = lambda value: SimpleNamespace(readiness=lambda: {'available': True, 'historical_caption': value})
        with self.assertRaisesRegex(ValueError, 'without provisioned caption history'):
            check_caption_provisioning(recipe, {}, grant('granted'))
        for factory in (grant('not_granted'), SimpleNamespace(), SimpleNamespace(readiness=lambda: {'available': False})):
            with self.assertRaisesRegex(ValueError, 'without household caption egress'):
                check_caption_provisioning(recipe, {'caption_history': True}, factory)
        check_caption_provisioning(recipe, {'caption_history': True}, grant('granted'))
        # No JEV means nothing can leave; withheld recipes need nothing.
        check_caption_provisioning(recipe, {'caption_history': True}, None)
        withheld = {'id': 'same', 'a': {}, 'b': {'caption_evidence': False}}
        check_caption_provisioning(withheld, {}, grant('not_granted'))
        # Through build_runtime: preparation fails before any source is built.
        from pathlib import Path
        catalog = json.loads(Path(__file__).with_name('experiments.json').read_text())
        same = next(r for r in catalog['experiments'] if r['id'] == 'placement-same')
        same['b']['caption_evidence'] = True
        original = Path.read_text
        read = lambda path, *a, **k: json.dumps(catalog) if path.name == 'experiments.json' else original(path, *a, **k)
        with patch('pathlib.Path.read_text', read):
            with self.assertRaisesRegex(ValueError, 'placement-same grants captions without provisioned'):
                self.build()


if __name__ == '__main__':
    unittest.main()
