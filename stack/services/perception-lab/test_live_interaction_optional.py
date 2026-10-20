"""Actual v3 acquisition graph with the existing LA terminal-receipt boundary.

Only external camera/model/HA/LA data is fictional. No completed interaction
interpretation is injected, no Lab recording exists and no scheduler is started.
"""
from copy import deepcopy
from pathlib import Path
import unittest

import la_fact_log
import live_capture_fixtures as capture
from journal import digest
from live_interaction_sources import ExistingReceiptSources
import test_live_interaction as interaction_tests
import test_live_interaction_sources as source_tests


class OptionalReceiptIntegrationTests(unittest.TestCase):
    setUp = interaction_tests.LiveInteractionTests.setUp
    control_clock = interaction_tests.LiveInteractionTests.control_clock
    camera_pair = interaction_tests.LiveInteractionTests.camera_pair
    snapshot = interaction_tests.LiveInteractionTests.snapshot
    advance = interaction_tests.LiveInteractionTests.advance
    construct = interaction_tests.LiveInteractionTests.construct
    facts = interaction_tests.LiveInteractionTests.facts
    original = interaction_tests.LiveInteractionTests.original

    def prepare(self, *, pin_model=True):
        interaction_tests.LiveInteractionTests.prepare(self)
        self.la_model = capture.pin('synthetic-optional-la-model-manifest')
        self.la_artifact = 'a' * 64
        self.la_identity = {'engine_epoch': 'synthetic-la-engine', 'artifact_sha256': self.la_artifact,
                            'session_id': source_tests.SESSION}
        self.la_granted = True
        if pin_model:
            source = self.live_config['manifest']['sources'][capture.SOURCE]
            source['model_refs'].append(deepcopy(self.la_model))
            self.interaction_policy['semantic_model_refs'] = [deepcopy(self.la_model)]
            old_method = deepcopy(self.interaction_method)
            self.interaction_method['digest'] = digest(self.interaction_policy)
            source['method_refs'] = [deepcopy(self.interaction_method) if row == old_method else row
                                     for row in source['method_refs']]
            self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
            self.journal_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.live_config['manifest_ref'])
            self.config['interaction_method_ref'] = deepcopy(self.interaction_method)
            self.local_grant['configuration_digest'] = digest(self.config)
        directory = Path(self.work.name) / 'synthetic-la-log'
        directory.mkdir()
        self.log = la_fact_log.FactLog(directory, private=False, clock=lambda: self.now)
        self.addCleanup(self.log.close)
        def identity():
            if isinstance(self.la_identity, Exception):
                raise self.la_identity
            return deepcopy(self.la_identity)
        self.sources = ExistingReceiptSources(site_id=self.config['session_ref']['site_id'],
            camera_id=self.policy['camera_id'], model_ref=self.la_model, artifact_sha256=self.la_artifact,
            granted=lambda: self.la_granted, identity=identity, clock=lambda: self.now)
        self.sources.attach(self.log)
        self.addCleanup(self.sources.clear)
        self.previous = []

    def collect(self):
        capture_id = self.original(self.facts()['object_interaction'])['capture_id']
        value = self.graph.capture.measurement_input(capture_id)
        self.previous.append(value)
        return value

    def with_terminal_before_interaction(self, *, facts=None, mutate_receipt=None, terminal_state='available', after_terminal=None):
        """External LA completion during the next real pose SDK invocation."""
        pose = self.pose_worker.model
        def call(image, bboxes):
            result = pose(image, bboxes)
            current = self.graph.capture.measurement_input(self.graph.current_capture['id'])
            self.now += .2
            request, receipt = source_tests.accepted([*self.previous[-1:], current], self.now,
                site_id=self.config['session_ref']['site_id'], camera_id=self.policy['camera_id'],
                artifact_sha256=self.la_artifact, engine_epoch='synthetic-la-engine', facts=facts)
            if mutate_receipt is not None:
                mutate_receipt(request, receipt)
            draft = {key: value for key, value in receipt.items()
                     if key not in {'available_at', 'first_received_at', 'fingerprint'}}
            self.last_receipt = deepcopy(receipt)
            self.assertEqual(self.log.terminal(request, 'completed', draft)['state'], terminal_state)
            if after_terminal is not None:
                after_terminal()
            self.pose_worker.model = pose
            return result
        self.pose_worker.model = call

    def first_local(self):
        graph = self.construct(interaction_sources=self.sources)
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.collect()
        self.assertEqual(self.facts()['hand_material_relation']['value'], 'supported_holding')
        return graph

    def test_fresh_existing_la_terminal_receipt_can_refine_exact_local_holding_without_adding_independence(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        self.with_terminal_before_interaction()
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        facts = self.facts()
        self.assertEqual(facts['material_state']['value'], 'open', facts)
        self.assertEqual(facts['activity_refinement']['value'], 'reading', facts)
        self.assertEqual(facts['activity_refinement']['model_ref'], self.la_model)
        self.assertIsNone(facts['activity_refinement']['probability'])
        self.assertEqual(facts['activity_refinement']['sensor_roots'], ['camera:synthetic-oak'])
        self.assertEqual(facts['activity_refinement']['observed_at'], capture.NOW + 1)
        self.assertEqual(len(facts['activity_refinement']['observation_roots']), 3)
        self.assertLessEqual(self.last_receipt['available_at'], facts['activity_refinement']['available_at'])
        original = self.original(facts['object_interaction'])
        self.assertEqual(original['supplemental']['la']['person_association'], 'qualified')
        self.assertEqual(original['supplemental']['la']['receipt'], self.last_receipt)
        self.assertFalse(cycle['focused_output'])

    def test_initial_la_absence_identity_loss_and_backoff_do_not_gate_new_local_facts(self):
        self.prepare()
        self.la_identity = None
        graph = self.first_local()
        previous = self.facts()['object_interaction']['observation_roots']
        for unavailable in (OSError('synthetic LA disconnected'), {'reason': 'identity_lost'}, {'reason': 'backoff'}):
            self.la_identity = unavailable
            self.advance()
            cycle = graph.step()
            self.assertEqual(cycle['interaction'], 'completed', cycle)
            facts = self.facts()
            self.assertTrue(facts['object_interaction']['value'])
            self.assertNotEqual(facts['object_interaction']['observation_roots'], previous)
            self.assertIsNone(facts['activity_refinement']['value'])
            previous = facts['object_interaction']['observation_roots']

    def test_actual_fact_log_closure_does_not_stop_local_capture_or_reuse_old_labels(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        self.with_terminal_before_interaction()
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.assertEqual(self.facts()['activity_refinement']['value'], 'reading')
        self.log.close()
        self.assertFalse(self.log.writable())
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        facts = self.facts()
        self.assertTrue(facts['object_interaction']['value'])
        self.assertIsNone(facts['activity_refinement']['value'])
        self.assertNotIn('pc_input_interaction', facts)

    def test_unpinned_la_model_keeps_its_original_proposal_out_of_semantic_truth(self):
        self.prepare(pin_model=False)
        graph = self.first_local()
        self.advance()
        self.with_terminal_before_interaction()
        self.assertEqual(graph.step()['interaction'], 'completed')
        facts = self.facts()
        self.assertEqual(facts['material_state']['value'], 'visible_state_unknown')
        self.assertIsNone(facts['activity_refinement']['value'])
        self.assertEqual(self.original(facts['object_interaction'])['supplemental']['la']['status'], 'fresh_proposal')

    def test_reading_label_without_visible_basis_cannot_become_refinement(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        facts = deepcopy(source_tests.FACTS)
        facts['visible_basis'] = ['person_visible']
        self.with_terminal_before_interaction(facts=facts)
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.assertIsNone(self.facts()['activity_refinement']['value'])
        self.assertTrue(self.facts()['object_interaction']['value'])

    def test_proximity_with_open_book_remains_proximity_despite_reading_proposal(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        self.materials[:] = [('book', [350, 190, 390, 210])]
        self.with_terminal_before_interaction()
        self.assertEqual(graph.step()['interaction'], 'completed')
        facts = self.facts()
        self.assertEqual(facts['material_state']['value'], 'open')
        self.assertEqual(facts['hand_material_relation']['value'], 'near')
        self.assertIsNone(facts['object_interaction']['value'])
        self.assertIsNone(facts['activity_refinement']['value'])

    def test_visible_unused_open_book_does_not_become_use(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        self.materials[:] = [('book', [100, 190, 140, 210])]
        self.with_terminal_before_interaction()
        self.assertEqual(graph.step()['interaction'], 'completed')
        facts = self.facts()
        self.assertEqual(facts['material_state']['value'], 'open')
        self.assertIsNone(facts['object_interaction']['value'])
        self.assertIsNone(facts['activity_refinement']['value'])

    def test_closed_held_book_retains_generic_holding_but_cannot_be_reading(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        facts = deepcopy(source_tests.FACTS)
        facts.update(book_state='closed', visible_basis=['book_in_hands', 'book_closed'])
        self.with_terminal_before_interaction(facts=facts)
        self.assertEqual(graph.step()['interaction'], 'completed')
        facts = self.facts()
        self.assertEqual(facts['material_state']['value'], 'closed')
        self.assertEqual(facts['hand_material_relation']['value'], 'supported_holding')
        self.assertIsNone(facts['activity_refinement']['value'])

    def test_la_expiry_inside_clock_uncertainty_cannot_stop_independent_local_facts(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        def shorten(request, receipt):
            receipt['original_valid_until'] = self.now + .0005
            receipt['valid_until'] = self.now + .0005
        self.with_terminal_before_interaction(mutate_receipt=shorten)
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        self.assertTrue(self.facts()['object_interaction']['value'])
        self.assertIsNone(self.facts()['activity_refinement']['value'])
        self.assertEqual(self.facts()['material_state']['value'], 'visible_state_unknown')

    def test_la_grant_revocation_does_not_revoke_local_and_cannot_refresh_old_proposal(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        self.with_terminal_before_interaction()
        self.assertEqual(graph.step()['interaction'], 'completed')
        self.la_granted = False
        self.advance()
        self.assertEqual(graph.step()['interaction'], 'completed')
        facts = self.facts()
        self.assertTrue(facts['object_interaction']['value'])
        self.assertIsNone(facts['activity_refinement']['value'])
        self.assertEqual(self.original(facts['object_interaction'])['supplemental']['la']['reason'], 'la_grant_unavailable')

    def test_wrong_la_artifact_receipt_cannot_refine_current_local_capture(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        self.with_terminal_before_interaction(mutate_receipt=lambda request, receipt:
                                              receipt.update(artifact_sha256='f' * 64))
        self.assertEqual(graph.step()['interaction'], 'completed')
        facts = self.facts()
        self.assertTrue(facts['object_interaction']['value'])
        self.assertEqual(facts['material_state']['value'], 'visible_state_unknown')
        self.assertIsNone(facts['activity_refinement']['value'])

    def test_expired_on_receipt_la_is_excluded_while_current_local_holding_survives(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        def expire(request, receipt):
            receipt['original_valid_until'] = receipt['observed_at'] + .1
            receipt['valid_until'] = receipt['original_valid_until']
        self.with_terminal_before_interaction(mutate_receipt=expire, terminal_state='expired_on_receipt')
        self.assertEqual(graph.step()['interaction'], 'completed')
        facts = self.facts()
        self.assertTrue(facts['object_interaction']['value'])
        self.assertIsNone(facts['activity_refinement']['value'])
        self.assertEqual(self.original(facts['object_interaction'])['supplemental']['la']['reason'], 'la_receipt_stale')

    def test_qualified_current_phone_interpretation_is_distinct_from_reading(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        self.materials[:] = [('cell phone', [300, 190, 340, 210])]
        facts = deepcopy(source_tests.FACTS)
        facts.update(held_object='phone', book_state='not_visible', activity_hypothesis='phone_use',
                     gaze_target='phone', visible_basis=['phone_in_hands', 'phone_screen_toward_face'])
        self.with_terminal_before_interaction(facts=facts)
        self.assertEqual(graph.step()['interaction'], 'completed')
        facts = self.facts()
        self.assertEqual(facts['activity_refinement']['value'], 'phone_use')
        self.assertEqual(facts['hand_material_relation']['value'], 'supported_holding')
        self.assertEqual(facts['material_state']['value'], 'visible_state_unknown')

    def test_competing_current_la_receipts_remain_inspectable_without_winning_refinement(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        competing = deepcopy(source_tests.FACTS)
        competing.update(held_object='phone', book_state='not_visible', activity_hypothesis='phone_use',
                         gaze_target='phone', visible_basis=['phone_in_hands', 'phone_screen_toward_face'])
        def second_result(request, receipt):
            current = graph.capture.measurement_input(graph.current_capture['id'])
            req, reply = source_tests.accepted([*self.previous[-1:], current], self.now, index=2,
                site_id=self.config['session_ref']['site_id'], camera_id=self.policy['camera_id'],
                artifact_sha256=self.la_artifact, engine_epoch='synthetic-la-engine', facts=competing)
            draft = {key: value for key, value in reply.items()
                     if key not in {'available_at', 'first_received_at', 'fingerprint'}}
            self.assertEqual(self.log.terminal(req, 'completed', draft)['state'], 'available')
        self.with_terminal_before_interaction(mutate_receipt=second_result)
        self.assertEqual(graph.step()['interaction'], 'completed')
        facts = self.facts()
        self.assertTrue(facts['object_interaction']['value'])
        self.assertIsNone(facts['activity_refinement']['value'])
        self.assertEqual(facts['material_state']['value'], 'visible_state_unknown')
        supplemental = self.original(facts['object_interaction'])['supplemental']['la']
        self.assertEqual(supplemental['status'], 'conflict')
        self.assertEqual({row['facts']['activity_hypothesis'] for row in supplemental['alternatives']}, {'reading', 'phone_use'})

    def test_optional_reader_time_is_part_of_derived_availability(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        reads = []
        def delayed_grant():
            self.now += .05
            reads.append(('grant', self.now))
            return self.la_granted
        def delayed_identity():
            self.now += .05
            reads.append(('identity', self.now))
            return deepcopy(self.la_identity)
        def enable_delay():
            self.sources.granted = delayed_grant
            self.sources.identity = delayed_identity
        self.with_terminal_before_interaction(after_terminal=enable_delay)
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        self.assertGreaterEqual(len(reads), 2)
        first_derive_finished = reads[1][1]
        facts = self.facts()
        self.assertEqual(facts['activity_refinement']['value'], 'reading')
        self.assertGreaterEqual(facts['material_state']['available_at'], first_derive_finished)
        self.assertGreaterEqual(facts['activity_refinement']['available_at'], first_derive_finished)

    def test_la_revoked_after_initial_optional_read_falls_back_without_poisoning_local_heads(self):
        self.prepare()
        graph = self.first_local()
        self.advance()
        checks = []
        def revoke_after_one_read():
            checks.append(self.now)
            return len(checks) == 1
        self.with_terminal_before_interaction(after_terminal=lambda:
                                              setattr(self.sources, 'granted', revoke_after_one_read))
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        facts = self.facts()
        self.assertTrue(facts['object_interaction']['value'])
        self.assertEqual(facts['material_state']['value'], 'visible_state_unknown')
        self.assertIsNone(facts['activity_refinement']['value'])
        self.assertGreaterEqual(len(checks), 2)
        self.la_granted = False
        self.sources.granted = lambda: self.la_granted
        self.advance()
        cycle = graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        self.assertTrue(self.facts()['object_interaction']['value'])
        self.assertIsNone(self.facts()['activity_refinement']['value'])


if __name__ == '__main__':
    unittest.main()
