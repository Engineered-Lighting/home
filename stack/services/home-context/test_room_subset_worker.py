"""Authored config13 through the actual factory, Journal HTTP and sole worker.

Only external camera/model/HA/clock inputs are fictional. No publication,
checkpoint, positive task or original PC proof is seeded into the Journal.
Coordinator owns the bounded fixed-Node/loopback-only execution gate.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tools/den-live-runtime'))
import test_pc_support_worker as pc_fixture
from controlled_external_io import build_base_configuration, assemble_journal_grants, SOURCE, CAMERA_EPOCH
from journal import Journal, digest
from live_acquisition import create_production_acquisition
from live_ingress_validation import validate_config
from live_worker_test_helpers import WorkerProbe
from room_configuration import upgrade_configuration as room_configuration
from pc_support_configuration import upgrade_configuration as pc_configuration
from room_subset_configuration import upgrade_configuration as subset_configuration
from schema_registry import SchemaRegistry, schema_digest


def subset_policy(policy):
    result = deepcopy(policy)
    result.update(schema='home-live-room-policy-2', version=2,
        policy_ref=dict(id='authored-one-assessed-room-task', digest='a'*64))
    result['fixtures'] = [dict(fixture_id=f['fixture_id'], mount_id=f['mount_id'],
        profile_ref=deepcopy(f['profile_ref']), assessment=dict(pose_ref=deepcopy(f['pose_ref']),
            valid_until=f['valid_until'], max_power_w=f['max_power_w'],
            task=dict(delivery_policy=deepcopy(f['task_policy']), power_w_at_full=f['spot_power_w_at_full'],
                      exposure_upper_at_full=f['spot_exposure_upper_at_full']),
            ambient=[dict(domain=row['domain'], assessment=None) for row in f['ambient']]) if i == 0 else None)
        for i, f in enumerate(policy['fixtures'])]
    return result


def authored_pc_base(case):
    """Use the existing source/config12 fixture recipe before any acquisition."""
    identity = deepcopy(pc_fixture.SCOPE)
    bundle = build_base_configuration(session_ref=identity, node_runtime=shutil.which('node'),
        wall_clock=lambda: case.now, monotonic=lambda: case.now-1799999000.)
    worker = bundle['worker_config']
    worker['engagement_policy']['allow_experimental'] = False
    room = json.loads(subprocess.check_output(['node', '-e',
        "process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"],
        cwd=ROOT, text=True, timeout=12))
    room['registration'] = deepcopy(worker['delivery_policy']['registration'])
    for fixture in room['fixtures']:
        fixture['task_policy'] = deepcopy(worker['delivery_policy'])
        fixture['task_policy']['fixture'].update(id=fixture['fixture_id'], fixture_pose_ref=deepcopy(fixture['pose_ref']))
    def current(value):
        if isinstance(value, dict):
            for key in value:
                if key == 'valid_until':value[key] = case.now+180
                else:current(value[key])
        elif isinstance(value, list):
            for row in value:current(row)
    current(room)
    config, worker = room_configuration(bundle['configuration'], worker, room)
    pc_source = deepcopy(config['manifest']['sources'][SOURCE])
    pc_source['method_refs'].append(deepcopy(pc_fixture.PC_METHOD))
    pc_source['sensor_roots'].append('device:authored-pc')
    pc_source['receipt_store_ids'].append('authored-pc-receipts')
    pc_source['receipt_component_ids'].append('pc-proof')
    config['manifest']['sources'][pc_fixture.PC_SOURCE] = pc_source
    position = dict(capture_epoch=CAMERA_EPOCH, capture_seq=1, revision=0)
    method = bundle['acquisition_config']['spatial_method_ref']
    track = dict(**identity, pid=1,
        track_epoch='track:'+digest([identity, SOURCE, method, 0, position, 1]))
    subject = dict(subject_id='wrist_working_region', subject_epoch='subject:'+digest([track, 0]))
    policy = pc_fixture.authored_pc_fixture()['policy']
    policy['bindings'][0].update(source_id=pc_fixture.PC_SOURCE,
        association_method_ref=deepcopy(bundle['lifecycle_policy']['continuity']['method_ref']),
        geometry_source_id=SOURCE, geometry_method_ref=deepcopy(method), subject_ref=deepcopy(subject))
    configuration, worker = pc_configuration(config, worker, policy)
    return bundle, identity, configuration, worker, subject


class RoomSubsetConfigurationTests(unittest.TestCase):
    def test_actual_config12_upgrade_is_explicit_preserves_inputs_and_refuses_crossed_base(self):
        self.now = float(pc_fixture.NOW)
        _, _, original, worker, _ = authored_pc_base(self)
        before, worker_before = deepcopy(original), deepcopy(worker)
        policy = subset_policy(worker['room_policy'])
        for flag in (None, False, 1):
            with self.subTest(flag=flag),self.assertRaisesRegex(ValueError, 'explicit_assessed_subset'):
                subset_configuration(original, worker, policy, allow_assessed_subset=flag)
        with self.assertRaisesRegex(ValueError, 'explicit_assessed_subset'):
            subset_configuration(original, worker, worker['room_policy'], allow_assessed_subset=True)
        for changed in ('source_pins', 'pc_policy', 'live_manifest', 'manifest_ref'):
            altered = deepcopy(worker)
            if changed == 'source_pins':altered['source_pins'][SOURCE] = 'f'*64
            elif changed == 'pc_policy':altered['pc_support_policy']['bindings'][0]['source_id'] = 'foreign'
            elif changed == 'live_manifest':altered['live_manifest']['version'] = 13
            else:altered['manifest_ref']['digest'] = 'f'*64
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError, 'exact_config12_worker_manifest'):
                subset_configuration(original, altered, policy, allow_assessed_subset=True)
        result, selected = subset_configuration(original, worker, policy, allow_assessed_subset=True)
        validate_config(result, SchemaRegistry((), profile='home-live-shadow-1'))
        self.assertEqual((result['version'], result['manifest']['version']), (13, 13))
        self.assertIs(selected['room_subset_mode'], True)
        self.assertEqual(result['manifest']['worker']['room_policy'], policy)
        self.assertEqual(result['manifest']['sources'], before['manifest']['sources'])
        self.assertEqual(selected['pc_support_policy'], worker_before['pc_support_policy'])
        self.assertEqual(selected['live_manifest'], result['manifest'])
        self.assertEqual(original, before);self.assertEqual(worker, worker_before)


class RoomSubsetWorkerTests(unittest.TestCase):
    # Reuse the actual immutable PC proof construction and public HTTP/probe
    # methods; import only a module alias so discovery cannot add old cases.
    wait_occupancy = pc_fixture.PCSupportWorkerTests.wait_occupancy
    request = pc_fixture.PCSupportWorkerTests.request
    checkpoint = pc_fixture.PCSupportWorkerTests.checkpoint
    tick = pc_fixture.PCSupportWorkerTests.tick
    relation = pc_fixture.PCSupportWorkerTests.relation
    advance = pc_fixture.PCSupportWorkerTests.advance
    reject_unchanged = pc_fixture.PCSupportWorkerTests.reject_unchanged

    def setUp(self):
        self.now = float(pc_fixture.NOW)
        self.directory = tempfile.TemporaryDirectory(prefix='authored-room-subset-', dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.bundle, self.identity, self.old_configuration, self.old_worker, self.subject = authored_pc_base(self)
        policy = subset_policy(self.old_worker['room_policy'])
        self.bundle['configuration'], self.config = subset_configuration(
            self.old_configuration, self.old_worker, policy, allow_assessed_subset=True)
        self.bundle['worker_config'] = self.config
        self.pc_source = self.bundle['configuration']['manifest']['sources'][pc_fixture.PC_SOURCE]
        self.grants = assemble_journal_grants(self.bundle['configuration'], self.config,
            publication_token=pc_fixture.PRODUCER, worker_token=pc_fixture.WORKER, human_token=pc_fixture.HUMAN)
        self.bundle['occupancy'].start();self.addCleanup(self.bundle['occupancy'].stop)
        self.wait_occupancy()
        self.graph = create_production_acquisition(self.bundle['acquisition_config'], live_config=self.bundle['configuration'],
            journal_path=Path(self.directory.name)/'joined.sqlite', journal_grants=self.grants,
            publication_token=pc_fixture.PRODUCER, clock=lambda:self.now, monotonic=lambda:self.now-1799999000.,
            **{key:self.bundle[key] for key in ('capture_policy','capture_method','spatial_policy','interaction_policy','lifecycle_policy',
                'detector','pose','local_authority','household_enabled','occupancy','camera_dependencies','control_clock','assessment_recorder','rss')})
        self.addCleanup(self.graph.close)
        self.originals = {}
        actual_resolver = self.graph.journal.live_receipt_resolver
        def resolve(store, receipt):
            key = (store, receipt)
            return deepcopy(self.originals[key]) if key in self.originals else actual_resolver(store, receipt)
        self.graph.journal.live_receipt_resolver = resolve
        self.config.update(service_url='http://127.0.0.1:'+str(self.graph.address[1]), token=pc_fixture.WORKER)
        self.scope = '?site_id='+self.identity['site_id']+'&room_id='+self.identity['room_id']
        self.probe = WorkerProbe();self.addCleanup(self.probe.close)
        self.probe.command('start', config=self.config, t=self.now)
        first = self.tick()
        # The existing Room fixture's real initial inspection happens BEFORE
        # source acquisition, so it cannot contradict fresh supplied geometry.
        self.assertTrue(first['person_hazards']['items'], first)
        self.now = float(pc_fixture.NOW+1)
        value = {key:deepcopy(value) for key,value in self.config['lifecycle_policy']['operator_clearance'].items() if key != 'max_age_s'}
        value.update(hazard_refs=[item['hazard_ref'] for item in first['person_hazards']['items']], inspection_at=self.now)
        intent = dict(schema='home-live-intent-event-2', version=2, id='authored-subset-initial-inspection', **self.identity,
            manifest_ref=deepcopy(self.config['manifest_ref']), actor_ref='controlled-owner', origin='human_declaration',
            truth_role='runtime_intent', kind='clear_person_hazards', target=None, set_at=self.now, available_at=self.now,
            valid_until=self.now+5, reply_to=None, value=value)
        status, result = self.request('POST', '/intent-events', intent, pc_fixture.HUMAN)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.tick()['intent_results'][0]['status'], 'applied')
        self.now = float(pc_fixture.NOW+2)
        self.bundle['external_io'].set_scenario('stationary_work');self.wait_occupancy()
        warmup = self.graph.step();self.assertEqual(warmup['interaction'], 'completed', warmup)
        self.tick()
        # Inherited advance uses NOW+seq; preserve strictly increasing time.
        self.seq = 2
        self.prior_fact_ref = None
        self.last_fixture = None

    def test_one_actual_assessed_task_commits_with_three_unavailable_members_and_restores(self):
        for _ in range(12):
            decision = self.advance()
            if (decision['execution']['status'] == 'virtual'
                    and decision['execution']['readback'][0]['brightness_fraction'] > 0):break
        else:self.fail('authored advancing PC proof did not establish sampled task: '+json.dumps(decision))
        self.assertEqual(decision['schema'], 'home-live-shadow-decision-12')
        room = decision['room_output'];checkpoint = self.checkpoint()['checkpoint']
        self.assertEqual(room['task']['fixture_id'], 'fixture-1')
        self.assertEqual(len(room['fixtures']), 4);self.assertEqual(len(room['task_candidates']), 1)
        self.assertEqual(len(checkpoint['state']['room_execution']['tasks']), 1)
        self.assertEqual(checkpoint['room_subset_policy_digest'], digest(self.config['room_policy']))
        self.assertEqual(checkpoint['state']['room_execution']['policy_digest'], digest(self.config['room_policy']))
        self.assertEqual(checkpoint['schema'], 'home-live-worker-checkpoint-12')
        relation = self.relation(checkpoint)
        self.assertEqual(relation['state'], 'supported');self.assertGreaterEqual(relation['enter_dwell'], 5)
        self.assertEqual(relation['chosen_clause']['qualification'], 'qualified_pc_input')
        self.assertTrue(checkpoint['state']['pc_input_originals'])
        for fixture in room['fixtures']:
            for row in fixture['domains']:
                if fixture['fixture_id'] == 'fixture-1' and row['domain'] == 'spotlight':continue
                self.assertEqual(row['status'], 'unavailable');self.assertIsNone(row['sample'])
                self.assertIsNone(row['desired']);self.assertEqual(row['valid_until'], decision['t'])
                self.assertEqual(row['assessment_refs'], [])
        self.probe.command('stop')
        restored = self.probe.command('start', config=self.config, t=self.now)
        self.assertFalse(any(row.get('status') == 'cold_start' for row in restored['statuses']), restored)
        restored_decision = self.advance()
        self.assertEqual(self.relation()['episode_id'], relation['episode_id'])
        public_worker = deepcopy(self.config)
        for key in ('token', 'service_url'):public_worker.pop(key, None)
        self.retained_evidence = dict(schema='home-authored-room-subset-boundary-evidence-1', version=1,
            fictional_external_inputs=True, configuration=deepcopy(self.bundle['configuration']),
            worker_configuration=public_worker, accepted_input_cut=deepcopy(decision['input_cut']),
            accepted_decision=deepcopy(decision), accepted_checkpoint=deepcopy(checkpoint),
            restored_decision=deepcopy(restored_decision), restored_checkpoint=deepcopy(self.checkpoint()['checkpoint']),
            limits=dict(real_room_qualification=False, command_dispatch=False, physical_permission=False))

    def test_new_profile_rejects_private_PC11_and_room8_grants_before_opening_database(self):
        for old_type in ('home-live-worker-checkpoint-11', 'home-live-input-cut-8'):
            grants = deepcopy(self.grants)
            grant = next(row for row in grants if 'worker_commit' in row['operations'])
            grant['payload_types'].append(old_type)
            grant['schema_pins'][old_type] = schema_digest(old_type, profile='home-live-shadow-1')
            path = Path(self.directory.name)/(old_type+'.sqlite')
            with self.subTest(old_type=old_type),self.assertRaisesRegex(ValueError, 'predecessor'):
                Journal(path, grants=grants, profile='home-live-shadow-1', clock=lambda:self.now,
                    live_config=self.bundle['configuration'], live_receipt_resolver=self.graph.journal.live_receipt_resolver,
                    live_wall_clock=lambda:self.now, live_monotonic=lambda:self.now-1799999000.)
            self.assertFalse(path.exists())
        self.assertIn('home-live-pc-support-policy-1', next(g for g in self.grants if 'worker_commit' in g['operations'])['payload_types'])

    def test_changed_raw_policy_checkpoint_and_forged_unassessed_off_refuse_atomically(self):
        self.advance()
        prepared = self.advance(prepare=True)
        for attack in ('policy_digest', 'schema_pin', 'unassessed_off'):
            forged = deepcopy(prepared)
            if attack == 'policy_digest':forged['checkpoint']['room_subset_policy_digest'] = 'f'*64
            elif attack == 'schema_pin':
                key = next(iter(forged['checkpoint']['room_subset_schema_pins']))
                forged['checkpoint']['room_subset_schema_pins'][key] = 'f'*64
            else:
                decision = forged['publications'][-1]['payload']
                row = decision['room_output']['fixtures'][1]['domains'][0]
                row.update(status='off', sample=dict(brightness_fraction=0, cct_kelvin=3000))
                body = {key:value for key,value in decision['room_output'].items() if key != 'revision_id'}
                revision = 'room:'+digest([self.identity, decision['seq'], body])[:48]
                decision['room_output']['revision_id'] = revision
                decision['background_delivery']['revision_id'] = revision
            with self.subTest(attack=attack):
                self.reject_unchanged(forged, 'invalid_room_output' if attack == 'unassessed_off' else 'checkpoint_conflict')


if __name__ == '__main__':unittest.main()
