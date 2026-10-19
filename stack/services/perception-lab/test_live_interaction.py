"""Interface A: actual acquisition/model/interaction factory to public HTTP."""
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import unittest

import live_capture_fixtures as capture
import live_interaction_fixtures as interaction
from journal import digest
from producer_development import run_development_session
import test_live_spatial as spatial_tests


class LiveInteractionTests(unittest.TestCase):
    setUp = spatial_tests.LiveSpatialTests.setUp
    control_clock = spatial_tests.LiveSpatialTests.control_clock
    camera_pair = spatial_tests.LiveSpatialTests.camera_pair
    snapshot = spatial_tests.LiveSpatialTests.snapshot
    advance = spatial_tests.LiveSpatialTests.advance

    def prepare(self):
        spatial_tests.LiveSpatialTests.prepare(self)
        (self.live_config, self.journal_grant, self.interaction_policy,
         self.interaction_method) = interaction.commission(self.live_config, self.method,
            self.spatial_method, self.journal_grant, self.refs)
        self.config.update(schema='home-production-acquisition-3', version=3,
                           interaction_method_ref=deepcopy(self.interaction_method))
        self.local_grant['operations'].append('interaction')
        self.local_grant['configuration_digest'] = digest(self.config)
        self.materials = [('book', [300, 190, 340, 210])]
        def predict(**kwargs):
            self.trace.append('detector')
            self.sdk_calls.append(('detector', kwargs['source'].copy()))
            return interaction.detector_result(self.boxes, self.materials)
        self.detector.model.predict = predict

    def construct(self, **options):
        from live_acquisition import create_production_acquisition
        self.graph = create_production_acquisition(self.config, live_config=self.live_config,
            capture_policy=self.policy, capture_method=self.method, spatial_policy=self.spatial_policy,
            interaction_policy=self.interaction_policy,
            journal_path=Path(self.work.name) / 'synthetic-interaction.sqlite', journal_grants=[self.journal_grant],
            publication_token=capture.TOKEN, detector=self.detector, pose=self.pose_worker,
            local_authority=lambda: deepcopy(self.local_grant) if self.permitted else None,
            household_enabled=lambda: self.enabled, occupancy=self.occupancy,
            clock=lambda: self.now, monotonic=lambda: self.mono, camera_dependencies=self.camera,
            control_clock=self.control_clock, rss=lambda: 64 * 1024**2, **options)
        self.addCleanup(self.graph.close)
        return self.graph

    def facts(self):
        return {item['payload']['kind']: item['payload'] for item in self.snapshot()['items']
                if item['payload'].get('method_ref') == self.interaction_method}

    def original(self, fact):
        ref = next(ref for ref in fact['original_receipt_refs'] if ref['component_id'] == 'interaction')
        return self.graph.interaction.original_receipt(ref['store_id'], ref['receipt_id'])

    def test_real_factory_stationary_two_hand_support_is_not_reading_or_dwell(self):
        self.prepare()
        cycle = self.construct().step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        facts = self.facts()
        self.assertEqual(facts['hand_material_relation']['value'], 'supported_holding', facts)
        self.assertIs(facts['object_interaction']['value'], True)
        self.assertEqual(facts['material_state']['value'], 'visible_state_unknown')
        self.assertEqual(facts['activity_refinement']['status'], 'unobservable')
        self.assertNotIn('pc_input_interaction', facts)
        for fact in facts.values():
            self.assertEqual(fact['sensor_roots'], ['camera:synthetic-oak'])
            self.assertEqual(fact['observed_at'], capture.NOW)
            self.assertIsNone(fact['probability'])
            self.assertEqual(fact['person_ref']['pid'], 1)
            self.assertEqual(fact['subject_ref']['subject_id'], 'wrist_working_region')
            self.assertNotIn('engagement', fact)
        first = facts['object_interaction']
        self.advance()
        self.assertEqual(self.graph.step()['interaction'], 'completed')
        second = self.facts()['object_interaction']
        self.assertEqual(first['person_ref'], second['person_ref'])
        self.assertNotEqual(first['observation_roots'], second['observation_roots'])
        self.assertTrue(second['value'])
        self.assertFalse(cycle['focused_output'])

    def test_visible_unused_or_closed_unknown_material_cannot_establish_use(self):
        self.prepare()
        self.materials[:] = [('book', [100, 190, 140, 210])]
        cycle = self.construct().step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        facts = self.facts()
        self.assertEqual(facts['material_state']['value'], 'visible_state_unknown')
        self.assertIsNone(facts['object_interaction']['value'])
        self.assertEqual(facts['object_interaction']['status'], 'unobservable')
        self.assertIsNone(facts['activity_refinement']['value'])
        self.assertEqual(self.original(facts['material_state'])['estimate']['relations'][0]['status'], 'visible_only')

    def test_hand_near_material_stays_proximity_and_no_false_negative(self):
        self.prepare()
        self.materials[:] = [('book', [350, 190, 390, 210])]
        self.assertEqual(self.construct().step()['interaction'], 'completed')
        facts = self.facts()
        self.assertEqual(facts['hand_material_relation']['value'], 'near')
        self.assertEqual(facts['hand_material_relation']['reason'], 'proximity_only')
        self.assertIsNone(facts['object_interaction']['value'])
        self.assertNotIn('negative_engagement', facts)

    def test_one_occluded_hand_keeps_material_visibility_without_manufactured_holding(self):
        self.prepare()
        self.joint_scores[9] = 0
        cycle = self.construct().step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        facts = self.facts()
        self.assertEqual(facts['material_state']['value'], 'visible_state_unknown')
        self.assertIsNone(facts['object_interaction']['value'])
        self.assertNotEqual(facts['hand_material_relation']['value'], 'supported_holding')
        self.assertIsNone(facts['material_state']['subject_ref'])

    def test_competing_material_candidates_abstain_and_preserve_alternatives(self):
        self.prepare()
        self.materials.append(('cell phone', [302, 192, 338, 208]))
        self.assertEqual(self.construct().step()['interaction'], 'completed')
        facts = self.facts()
        self.assertIsNone(facts['object_interaction']['value'])
        self.assertEqual(facts['hand_material_relation']['reason'], 'subject_association_ambiguous')
        original = self.original(facts['object_interaction'])
        self.assertEqual(original['estimate']['status'], 'conflicted')
        self.assertEqual({item['category'] for item in original['estimate']['alternatives']}, {'book', 'phone'})
        self.assertIsNone(facts['object_interaction']['probability'])

    def test_phone_holding_does_not_become_phone_use_or_reading(self):
        self.prepare()
        self.materials[:] = [('cell phone', [300, 190, 340, 210])]
        self.assertEqual(self.construct().step()['interaction'], 'completed')
        facts = self.facts()
        self.assertEqual(facts['hand_material_relation']['value'], 'supported_holding')
        self.assertIsNone(facts['activity_refinement']['value'])
        self.assertEqual(self.original(facts['object_interaction'])['estimate']['materials'][0]['category'], 'phone')

    def test_empty_material_detection_is_not_observed_disengagement(self):
        self.prepare()
        self.materials.clear()
        self.assertEqual(self.construct().step()['interaction'], 'completed')
        facts = self.facts()
        self.assertEqual(facts['person_presence']['value'], 'present')
        self.assertEqual(facts['material_state']['status'], 'unobservable')
        self.assertIsNone(facts['object_interaction']['value'])
        self.assertNotIn('negative_engagement', facts)

    def test_source_gap_retires_use_then_recovery_has_fresh_person_epoch(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['interaction'], 'completed')
        first = self.facts()['object_interaction']
        self.camera_down = True
        self.advance()
        self.assertEqual(graph.step()['status'], 'failed')
        self.assertFalse(self.snapshot()['items'])
        self.camera_down = False
        self.advance()
        self.assertEqual(graph.step()['interaction'], 'completed')
        recovered = self.facts()['object_interaction']
        self.assertNotEqual(first['person_ref']['track_epoch'], recovered['person_ref']['track_epoch'])
        self.assertNotEqual(first['observation_roots'], recovered['observation_roots'])

    def test_expiry_is_half_open_and_never_refreshed_by_public_reads(self):
        self.prepare()
        self.assertEqual(self.construct().step()['interaction'], 'completed')
        fact = self.facts()['object_interaction']
        self.now = fact['valid_until']
        self.assertNotIn('object_interaction', self.facts())
        self.assertNotIn('object_interaction', self.facts())

    def test_all_qualified_facts_expire_with_person_association_bound(self):
        self.prepare()
        source = self.live_config['manifest']['sources'][capture.SOURCE]
        source['part_policies']['body']['age']['max_age_s'] = .4
        self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
        self.journal_grant['live_bindings'][0]['manifest_ref'] = deepcopy(self.live_config['manifest_ref'])
        self.assertEqual(self.construct().step()['interaction'], 'completed')
        facts = self.facts()
        for fact in facts.values():
            self.assertLess(fact['valid_until'], capture.NOW + .4)
        self.now = capture.NOW + .4
        self.assertEqual(self.facts(), {})

    def test_duplicate_original_observation_cannot_refresh_use_or_dwell(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['interaction'], 'completed')
        first = self.facts()
        self.mono += .6
        self.assertEqual(graph.step()['reason'], 'duplicate_capture')
        self.assertEqual(self.facts(), first)

    def test_local_kill_switch_clears_use_and_owned_originals(self):
        self.prepare()
        graph = self.construct()
        self.assertEqual(graph.step()['interaction'], 'completed')
        fact = self.facts()['object_interaction']
        ref = next(ref for ref in fact['original_receipt_refs'] if ref['component_id'] == 'interaction')
        self.enabled = False
        self.advance()
        self.assertEqual(graph.step()['reason'], 'local_acquisition_revoked')
        with self.assertRaises(KeyError):
            graph.interaction.original_receipt(ref['store_id'], ref['receipt_id'])

    def test_actual_v3_graph_runs_in_existing_bounded_launcher(self):
        self.prepare()
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node is required for the real commissioning kit')
        record = Path(self.work.name) / 'synthetic-interaction-owner-record.json'
        kit = Path(__file__).resolve().parents[3] / 'tools' / 'den-commissioning' / 'commission.mjs'
        subprocess.run([node, str(kit), 'template', '--output', str(record)],
                       check=True, capture_output=True, timeout=10)
        graph = self.construct()
        run_config = {'schema': 'home-producer-development-1', 'version': 1,
            'session_ref': deepcopy(self.config['session_ref']), 'source_id': self.config['source_id'],
            'configuration_digest': digest(self.config), 'max_runtime_s': 5, 'max_cycles': 1, 'interval_s': .5,
            'commissioning_record': str(record), 'commissioning_freeze': None, 'measurement_summary': None}
        report = run_development_session(graph, run_config, clock=lambda: self.mono,
            sleep=lambda _: None, stop_requested=lambda: False, node_runtime=node)
        self.assertEqual(report['status'], 'development_complete', report)
        self.assertEqual(report['commissioning']['status'], 'incomplete')
        self.assertEqual(report['missing_stages'], [])
        self.assertEqual(report['counts']['spatial_completed_results'], 1)
        self.assertEqual(report['counts']['interaction_completed_results'], 1)
        self.assertFalse(report['focused_output'])
        self.assertFalse(report['physical_authority'])


if __name__ == '__main__':
    unittest.main()
