"""Finite actual bundled source7 with authored camera/model/qualification I/O."""
from copy import deepcopy
import threading
import unittest

import test_lab_live_acquisition as legacy
import test_lab_stereo_mount as mounted


class LabLivePollBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = mounted.PollBudgetStereoMountTests()
        cls.fixture.setUp()
        cls.addClassCleanup(cls.fixture.doCleanups)
        cls.initial_time = cls.fixture.source.f.now
        cls.seed = deepcopy(cls.fixture.source.f.row)

    def setUp(self):
        self.source = self.fixture.source
        self.source.f.now = self.initial_time
        self.mono = 0
        self.graph_value = self.composition = None
        self.fresh_fixture = None
        self.addCleanup(self.cleanup_graph)

    def graph(self, *, polls=5, originals=1, empty=False):
        producer, config = deepcopy(self.source.producer), deepcopy(self.fixture.config)
        producer['limits'].update(max_poll_attempts=polls, max_capture_originals=originals)
        name = self._testMethodName
        producer['journal_path'] = str(self.source.path / (name+'-journal.sqlite'))
        producer['original_archive']['filename'] = str(self.source.path / (name+'-originals.jsonl'))
        producer['after_id'] = (self.source.f.store.list_events(self.source.f.session['id'])[-1]['id']
                                if empty else self.seed['id']-1)
        config['media']['directory'] = str(self.source.path / (name+'-media'))
        self.composition = self.fixture.compose(producer, config)
        self.composition.monotonic = lambda: self.mono + (self.source.f.now - self.initial_time)
        self.source.composition = self.composition
        self.graph_value = self.source.graph = graph = self.composition.create()
        self.originals = self.composition.originals
        graph.journal.attach_recorder(lambda record: None)
        return graph

    def cleanup_graph(self):
        if self.graph_value and not self.graph_value.finished:
            withdrawal = self.graph_value.withdraw()
            self.graph_value.finalize(drain_receipt=self.source.drain(withdrawal,
                recorder='incomplete', worker='unconfirmed'))
        if self.composition:
            self.composition.release()
        if self.fresh_fixture is not None:
            self.fresh_fixture.doCleanups()
            self.fresh_fixture = None

    def publications(self):
        return self.graph_value.journal.db.execute('SELECT count(*) FROM publications').fetchone()[0]

    def finish_incomplete(self, graph):
        withdrawal = graph.withdraw()
        result = graph.finalize(drain_receipt=self.source.drain(withdrawal))
        self.assertEqual(result['status'], 'unconfirmed', result)
        return result

    def test_a_empty_polls_leave_one_original_slot_for_actual_journal_admission(self):
        # Only this case advances capture/model clocks. Keep every other case's
        # immutable source fixture independent of its order or failure.
        self.fresh_fixture = mounted.PollBudgetStereoMountTests()
        self.fresh_fixture.setUp()
        self.fixture = self.fresh_fixture
        self.source = self.fixture.source
        self.initial_time = self.source.f.now
        self.seed = deepcopy(self.source.f.row)
        graph = self.graph(empty=True)
        self.assertEqual((graph.config['schema'], graph.config['version']), ('home-incumbent-lab-acquisition-7', 7))
        self.assertEqual(graph.step()['status'], 'idle')
        self.assertEqual(graph.step()['status'], 'idle')
        self.assertEqual(graph.status()['counters']['capture_originals'], 0)
        # Retain the real 2Hz model cadence. These intermediate originals are
        # candidate-only (U>.1) and are genuinely dropped, never Journal inputs.
        self.source.f.append_pair(sequence=20, observed_at=mounted.NOW+.579, expect_output=False)
        self.source.f.append_pair(sequence=30, observed_at=mounted.NOW+.817, expect_output=False)
        event, _ = self.source.f.append_pair(sequence=40, observed_at=mounted.NOW+.955)
        result = graph.step()
        rows = self.composition.bundled_source.prepared[event['id']]['result']['rows']
        raw_depth = rows['bundle'][0]['metadata']['source_metadata']['frames']['depth']['device_timestamp_seconds']
        raw_b = rows['anchor_b'][0]['metadata']['source_timing_receipt']['frame_device_ns'] / 1e9
        commissioning = self.composition.bundled_source.depth_clock.clock
        self.assertEqual(commissioning, self.fixture.qualified['depth_clock_policy'])
        self.assertLessEqual(abs(raw_depth - raw_b), commissioning['max_delta_s'])
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(graph.status()['counters']['capture_originals'], 1)
        self.assertEqual(graph.status()['counters']['poll_attempts'], 3)
        original = next(iter(graph.join.originals.values()))
        uncertainty = original['clock_original']['clock']['uncertainty_s']
        depth = self.composition.bundled_source.selected[event['id']]['rows']['mapped_depth'][0]['metadata']
        self.assertLessEqual(uncertainty, .1)
        self.assertLessEqual(depth['observed_at'], event['payload']['observed_at'])
        self.assertLessEqual(event['payload']['observed_at'] - depth['observed_at']
                             + uncertainty + depth['uncertainty_seconds'], .1)
        self.assertEqual(self.source.f.runtime.bundled_rgb_sessions[self.source.f.session['id']].capture_count, 5)
        before = self.publications()
        self.assertEqual(graph.step()['reason'], 'lab_capture_original_budget_exhausted')
        self.assertEqual(self.publications(), before)
        self.assertIsNone(graph.pending_original)
        self.assertEqual(graph.after_id, event['id'])

    def test_poll_exhaustion_cannot_read_or_retain_a_later_original(self):
        graph = self.graph(polls=2, empty=True)
        for _ in range(2): self.assertEqual(graph.step()['status'], 'idle')
        self.assertEqual(graph.step()['reason'], 'lab_poll_budget_exhausted')
        self.assertEqual(len(self.originals.records), 0)
        self.assertEqual(graph.status()['counters']['capture_originals'], 0)
        self.assertEqual(graph.status()['counters']['poll_attempts'], 2)

    def test_lost_journal_ack_keeps_one_slot_and_the_exact_original_receipt(self):
        graph = self.graph()
        append, lost = graph.journal.append_event, [False]
        def lose(envelope, principal):
            ack = append(envelope, principal)
            if not lost[0]: lost[0] = True; raise TimeoutError('authored_lost_ack')
            return ack
        graph.journal.append_event = lose
        self.assertEqual(graph.step()['status'], 'failed')
        original = deepcopy(graph.pending_original)
        self.source.f.now += .01
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(graph.status()['counters']['capture_originals'], 1)
        self.assertEqual(graph.status()['counters']['poll_attempts'], 2)
        self.assertEqual(next(iter(graph.join.originals.values()))['first_consumer_received_at'], original['first_consumer_received_at'])
        self.finish_incomplete(graph)

    def test_stop_after_durable_original_before_ack_prevents_publication(self):
        graph = self.graph()
        entered, release, answers = threading.Event(), threading.Event(), []
        retain = graph.original_retainer
        def blocked(original):
            ack = retain(original)
            entered.set(); self.assertTrue(release.wait(2)); return ack
        blocked.finalize = retain.finalize
        graph.original_retainer = blocked
        thread = threading.Thread(target=lambda: answers.append(graph.step()))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(len(self.originals.records), 1)
            self.assertEqual(graph.quiesce()['status'], 'unconfirmed')
        finally:
            release.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(answers[0]['status'], 'stopped', answers)
        self.assertEqual(self.publications(), 0)
        self.assertEqual(graph.status()['counters']['capture_originals'], 1)
        self.assertEqual(graph.step()['reason'], 'lab_graph_quiesced')
        self.finish_incomplete(graph)

    def test_full_original_archive_refuses_without_refunding_capacity(self):
        graph = self.graph()
        self.originals.max_records = 0
        self.assertEqual(graph.step()['status'], 'failed')
        self.assertEqual(graph.status()['counters']['capture_originals'], 1)
        self.assertEqual(self.publications(), 0)
        self.finish_incomplete(graph)

    def unknown_original_ack(self, *, polls=5):
        graph = self.graph(polls=polls)
        retain = graph.original_retainer
        def unknown(original):
            retain(original); raise TimeoutError('authored_original_ack_unknown')
        unknown.finalize = retain.finalize
        graph.original_retainer = unknown
        self.assertEqual(graph.step()['status'], 'failed')
        return graph, deepcopy(graph.pending_original)

    def test_pending_original_ack_cannot_bypass_poll_exhaustion(self):
        graph, original = self.unknown_original_ack(polls=1)
        self.assertEqual(graph.step()['reason'], 'lab_poll_budget_exhausted')
        self.assertEqual(graph.pending_original, original)
        self.assertEqual(len(self.originals.records), 1)
        self.assertEqual(self.publications(), 0)
        self.finish_incomplete(graph)

    def test_pending_original_ack_cannot_bypass_original_wall_budget(self):
        graph, original = self.unknown_original_ack()
        self.mono += 120
        self.assertEqual(graph.step()['reason'], 'lab_graph_budget_exhausted')
        self.assertEqual(graph.pending_original, original)
        self.assertEqual(graph.status()['counters']['poll_attempts'], 1)
        self.assertEqual(self.publications(), 0)
        self.finish_incomplete(graph)

    def test_lost_media_ack_reserves_before_effect_and_retries_exact_frozen_original(self):
        graph = self.graph()
        media = self.composition.desk_capability.media_archive
        retain, calls = media.retain_media, []
        def lose(refs):
            calls.append(deepcopy(refs)); ack = retain(refs)
            if len(calls) == 1: raise TimeoutError('authored_media_ack_unknown')
            return ack
        media.retain_media = lose
        self.assertEqual(graph.step()['status'], 'failed')
        self.assertEqual(graph.status()['counters']['capture_originals'], 1)
        pending = deepcopy(graph.pending_capture)
        self.assertIsNotNone(pending)
        self.assertEqual(len(media.media_refs()), 7)
        self.assertEqual(len(self.originals.records), 0)
        self.source.f.now += .01
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(graph.status()['counters']['capture_originals'], 1)
        self.assertEqual(next(iter(graph.join.originals.values())), pending['record'])
        self.assertIsNone(graph.pending_capture)
        self.finish_incomplete(graph)


class PollBudgetConstructorTests(unittest.TestCase):
    def test_legacy_constructor_cannot_promote_to7_before_journal_effects(self):
        f = legacy.LabLiveAcquisitionTests(); f.setUp(); self.addCleanup(f.doCleanups)
        join = legacy.LabLiveJournalJoin(f.session, arm='a', generation=1, live_config=f.config,
            source_id='synthetic-source', revision='synthetic-r1', method_ref=legacy.METHOD,
            model_ref=f.model, lab_profile=f.lab_profile, model_manifests=f.model_manifests,
            receipt_store_id='synthetic-receipts', receipt_component_id='pose')
        with self.assertRaisesRegex(ValueError, 'bundled_source6'):
            f.graph = legacy.LabLiveAcquisition(join, f.store, journal_path=f.path/'refused.sqlite',
                journal_grants=[f.producer_grant], publication_token=legacy.TOKEN,
                original_retainer=lambda _: None, control_clock=f.control_clock,
                clock=lambda: f.now, monotonic=lambda: f.mono,
                max_poll_attempts=5, max_capture_originals=1)
        self.assertFalse((f.path/'refused.sqlite').exists())


if __name__ == '__main__': unittest.main()
