"""PC support through actual authored capture/factory -> HTTP -> Node -> validator.

External RGB/depth/model and HA observations are generated test inputs. Geometry,
publication ancestry, worker cuts, temporal reduction and commits use the real
implementations. Nothing here qualifies a household source or writes hardware.
"""
from copy import deepcopy
from decimal import Decimal
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools/den-live-runtime'))
from controlled_external_io import build_base_configuration, assemble_journal_grants, SOURCE, CAMERA_EPOCH, REVISION
from room_configuration import upgrade_configuration as room_configuration
from pc_support_configuration import upgrade_configuration as pc_configuration
from journal import PREFIX, digest
from live_acquisition import create_production_acquisition
from live_worker_test_helpers import WorkerProbe
from live_test_helpers import SCOPE, NOW
from test_live_pc_support import authored_pc_fixture, PC_METHOD

PC_SOURCE = 'authored-pc-source'
PRODUCER, WORKER, HUMAN = ('authored-pc-worker-producer', 'authored-pc-worker', 'authored-pc-owner')


class PCSupportWorkerTests(unittest.TestCase):
    def setUp(self):
        self.now = float(NOW)
        self.identity = deepcopy(SCOPE)
        self.directory = tempfile.TemporaryDirectory(prefix='authored-pc-worker-')
        self.addCleanup(self.directory.cleanup)
        self.bundle = build_base_configuration(session_ref=self.identity, node_runtime=shutil.which('node'),
            wall_clock=lambda: self.now, monotonic=lambda: self.now - 1799999000.)
        worker = self.bundle['worker_config']
        # Existing material facts are experimental. They must not earn support
        # while this test proves the separate, qualified PC rule.
        worker['engagement_policy']['allow_experimental'] = False
        room = json.loads(subprocess.check_output(['node', '-e',
            "process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"], cwd=ROOT, text=True))
        room['registration'] = deepcopy(worker['delivery_policy']['registration'])
        for fixture in room['fixtures']:
            fixture['task_policy'] = deepcopy(worker['delivery_policy'])
            fixture['task_policy']['fixture'].update(id=fixture['fixture_id'], fixture_pose_ref=deepcopy(fixture['pose_ref']))
        def current(value):
            if isinstance(value, dict):
                for key in value:
                    if key == 'valid_until': value[key] = self.now + 180
                    else: current(value[key])
            elif isinstance(value, list):
                for row in value: current(row)
        current(room)
        config, worker = room_configuration(self.bundle['configuration'], worker, room)
        camera = config['manifest']['sources'][SOURCE]
        pc_source = deepcopy(camera)
        pc_source['method_refs'].append(deepcopy(PC_METHOD))
        pc_source['sensor_roots'].append('device:authored-pc')
        pc_source['receipt_store_ids'].append('authored-pc-receipts')
        pc_source['receipt_component_ids'].append('pc-proof')
        config['manifest']['sources'][PC_SOURCE] = pc_source
        # Commission the deterministic authored first-capture subject. Assert
        # this against the actual factory's output below; do not seed geometry.
        position = {'capture_epoch': CAMERA_EPOCH, 'capture_seq': 1, 'revision': 0}
        method = self.bundle['acquisition_config']['spatial_method_ref']
        track = {**self.identity, 'pid': 1,
                 'track_epoch': 'track:' + digest([self.identity, SOURCE, method, 0, position, 1])}
        self.subject = {'subject_id': 'wrist_working_region', 'subject_epoch': 'subject:' + digest([track, 0])}
        policy = authored_pc_fixture()['policy']
        policy['bindings'][0].update(source_id=PC_SOURCE,
            association_method_ref=deepcopy(self.bundle['lifecycle_policy']['continuity']['method_ref']),
            geometry_source_id=SOURCE, geometry_method_ref=deepcopy(method), subject_ref=deepcopy(self.subject))
        self.bundle['configuration'], self.config = pc_configuration(config, worker, policy)
        self.bundle['worker_config'] = self.config
        self.pc_source = self.bundle['configuration']['manifest']['sources'][PC_SOURCE]
        grants = assemble_journal_grants(self.bundle['configuration'], self.config,
            publication_token=PRODUCER, worker_token=WORKER, human_token=HUMAN)
        self.bundle['occupancy'].start()
        self.addCleanup(self.bundle['occupancy'].stop)
        self.wait_occupancy()
        self.graph = create_production_acquisition(self.bundle['acquisition_config'], live_config=self.bundle['configuration'],
            journal_path=Path(self.directory.name) / 'joined.sqlite', journal_grants=grants, publication_token=PRODUCER,
            clock=lambda: self.now, monotonic=lambda: self.now - 1799999000.,
            **{key: self.bundle[key] for key in ('capture_policy', 'capture_method', 'spatial_policy', 'interaction_policy', 'lifecycle_policy',
                'detector', 'pose', 'local_authority', 'household_enabled', 'occupancy', 'camera_dependencies', 'control_clock', 'assessment_recorder', 'rss')})
        self.addCleanup(self.graph.close)
        self.originals = {}
        actual_resolver = self.graph.journal.live_receipt_resolver
        def resolve(store, receipt):
            key = (store, receipt)
            return deepcopy(self.originals[key]) if key in self.originals else actual_resolver(store, receipt)
        self.graph.journal.live_receipt_resolver = resolve
        # Initial spatial acquisition establishes an epoch; the ordinary next
        # capture establishes qualified continuity under its separate method.
        # The PC policy explicitly consumes that method, not a renamed initial
        # association. Both captures still run through the actual factory.
        self.bundle['external_io'].set_scenario('stationary_work')
        warmup = self.graph.step()
        self.assertEqual(warmup['interaction'], 'completed', warmup)
        self.config.update(service_url='http://127.0.0.1:' + str(self.graph.address[1]), token=WORKER)
        self.scope = '?site_id=' + self.identity['site_id'] + '&room_id=' + self.identity['room_id']
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.probe.command('start', config=self.config, t=self.now)
        self.tick()
        self.seq = 0
        self.prior_fact_ref = None
        self.last_fixture = None

    def wait_occupancy(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            value = self.bundle['occupancy'].reading()
            if value['state'] == 'occupied' and value['observed_at'] >= self.now: return
            time.sleep(.01)
        self.fail('authored occupancy did not reach incumbent reader')

    def request(self, method, route, body=None, token=WORKER):
        connection = http.client.HTTPConnection('127.0.0.1', self.graph.address[1], timeout=4)
        try:
            headers = {'Authorization': 'Bearer ' + token}
            if body is not None: headers['Content-Type'] = 'application/json'
            connection.request(method, PREFIX + route, None if body is None else json.dumps(body), headers)
            result = connection.getresponse()
            return result.status, json.loads(result.read())
        finally:
            connection.close()

    def checkpoint(self):
        status, value = self.request('GET', '/worker-commits/' + self.config['worker_id'] + self.scope)
        self.assertEqual(status, 200, value)
        return value

    def tick(self, *, prepare=False):
        if prepare: self.probe.command('fault', mode='before_commit')
        result = self.probe.command('tick', t=self.now)
        if prepare:
            self.assertTrue(result['commit_posts'], result)
            return json.loads(result['commit_posts'][-1])
        self.assertEqual(len(result['decisions']), 1, result)
        self.assertFalse(result['http_errors'], result)
        return result['decisions'][0]

    def relation(self, checkpoint=None):
        checkpoint = self.checkpoint()['checkpoint'] if checkpoint is None else checkpoint
        relations = list(checkpoint['state']['engagement']['reducer']['state']['relations'].values())
        selected = [row for row in relations if row['subject_ref'] == self.subject]
        self.assertEqual(len(selected), 1, relations)
        return selected[0]

    def advance(self, *, input_at=None, prepare=False, expect=200, forged_fresh_lease=False):
        self.seq += 1
        self.now = float(NOW + self.seq)
        self.bundle['external_io'].set_scenario('stationary_work')
        self.wait_occupancy()
        cycle = self.graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        status, cut = self.request('GET', '/live-input-cut' + self.scope + '&consumer_id=' + self.config['worker_id'])
        self.assertEqual(status, 200, cut)
        candidates = [row for row in cut['items'] if row['payload_type'] == 'home-live-geometry-1'
            and row['payload']['source_id'] == SOURCE and row['payload']['parts']['work']['status'] == 'supplied']
        self.assertEqual(len(candidates), 1, cut)
        publication = candidates[0]
        parent = publication['payload']
        self.assertEqual(parent['subject_ref'], self.subject)
        ref = {key: publication[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')}
        ref['component_id'] = 'parts.work'
        f = authored_pc_fixture(seq=self.seq, snapshot_at=self.now, input_at=input_at)
        fact, original, record = f['fact'], f['original'], f['record']
        original.update(source_id=PC_SOURCE, person_ref=deepcopy(parent['person_ref']), subject_ref=deepcopy(self.subject),
            association_method_ref=deepcopy(parent['association']['method_ref']), association_ref=deepcopy(ref))
        fact.update(manifest_ref=deepcopy(self.config['manifest_ref']), source_id=PC_SOURCE,
            person_ref=deepcopy(parent['person_ref']), subject_ref=deepcopy(self.subject), association=deepcopy(parent['association']),
            parent_refs=[deepcopy(ref)], sensor_roots=deepcopy(self.pc_source['sensor_roots']))
        fact['head']['key'].update(source_id=PC_SOURCE, person_ref=deepcopy(parent['person_ref']), subject_ref=deepcopy(self.subject))
        fact['head']['position']['capture_epoch'] = self.pc_source['capture_epoch']
        fact['head']['supersedes'] = deepcopy(self.prior_fact_ref)
        fact['observation_root_times'] = {**deepcopy(parent['observation_root_times']), **record['observation_root_times']}
        fact['sensor_root_bindings'] = {**deepcopy(parent['sensor_root_bindings']), **record['sensor_root_bindings']}
        fact['observation_roots'] = sorted(fact['observation_root_times'])
        fact['valid_until'] = min(fact['valid_until'], parent['valid_until'], parent['parts']['work']['valid_until'])
        if forged_fresh_lease:
            # Negative case: an otherwise current envelope claims a fresh lease
            # while retaining the expired physical input and its original proof.
            fact['valid_until'] = min(self.now + 2, parent['valid_until'], parent['parts']['work']['valid_until'])
        capture_clock = self.pc_source['capture_clock']
        fact['clock'].update({key: capture_clock[key] for key in ('mapping_id', 'mapping_digest', 'source_epoch', 'original_unit')})
        fact['clock']['original_value'] = str(int(Decimal(str(fact['observed_at'])) * Decimal(1000000000)))
        original_ref = fact['original_receipt_refs'][0]
        original_ref.update(store_id='authored-pc-receipts', digest=digest(original), clock_mapping_id=capture_clock['mapping_id'])
        record.update(ref=deepcopy(original_ref), capture_epoch=self.pc_source['capture_epoch'], pc_input_original=deepcopy(original))
        self.originals[(original_ref['store_id'], original_ref['receipt_id'])] = deepcopy(record)
        self.now += .3
        event = dict(schema='home-context-event-1', schema_version='1.0', id=fact['id'],
            producer_id=self.pc_source['producer_id'], stream_epoch=self.pc_source['producer_epoch'], **self.identity,
            observed_at=fact['observed_at'], available_at=fact['available_at'], valid_until=fact['valid_until'],
            payload_type=fact['schema'], payload_version='1.0', revision=REVISION, payload=fact)
        status, result = self.request('POST', '/events', event, PRODUCER)
        self.assertEqual(status, expect, result)
        self.last_fixture = f
        if status != 200: return result
        self.prior_fact_ref = {key: result[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')}
        self.prior_fact_ref['component_id'] = None
        return self.tick(prepare=prepare)

    def reject_unchanged(self, commit, expected='invalid_pc_support'):
        before = self.checkpoint()
        status, result = self.request('POST', '/worker-commits', commit)
        self.assertEqual(status, 409, result)
        self.assertEqual(result['error']['code'], expected, result)
        self.assertEqual(self.checkpoint(), before)

    def test_same_lastactive_new_snapshots_do_not_earn_dwell_or_extend_input_lease(self):
        fixed_input = NOW + .5
        horizon = None
        for _ in range(8):
            self.advance(input_at=fixed_input)
            relation = self.relation()
            self.assertEqual(relation['state'], 'candidate')
            self.assertEqual(relation['enter_dwell'], 0)
            self.assertEqual(relation['chosen_clause']['progression_roots'], [self.last_fixture['original']['input_root']])
            current = self.last_fixture['original']['input_valid_until']
            horizon = current if horizon is None else horizon
            self.assertEqual(current, horizon)
            self.assertLessEqual(relation['chosen_clause']['valid_until'], horizon)
        # New, source-fresh snapshots cannot revive an expired physical input.
        for _ in range(7): self.advance(input_at=fixed_input)
        rejected = self.advance(input_at=fixed_input, expect=409, forged_fresh_lease=True)
        self.assertEqual(rejected['error']['code'], 'invalid_pc_support')

    def test_advancing_physical_inputs_reach_five_seconds_and_exact_worker_restart_restores(self):
        for _ in range(7): self.advance()
        before = self.checkpoint()['checkpoint']
        relation = self.relation(before)
        self.assertEqual(relation['state'], 'supported')
        self.assertGreaterEqual(relation['enter_dwell'], 5)
        self.assertEqual(relation['chosen_clause']['qualification'], 'qualified_pc_input')
        self.assertTrue(before['state']['pc_input_originals'])
        self.probe.command('stop')
        restored = self.probe.command('start', config=self.config, t=self.now)
        self.assertFalse(any(row.get('status') == 'cold_start' for row in restored['statuses']), restored)
        self.advance()
        after = self.relation()
        self.assertEqual(after['episode_id'], relation['episode_id'])
        self.assertEqual(after['state'], 'supported')
        self.assertGreater(after['enter_dwell'], relation['enter_dwell'])

    def test_forged_snapshot_progression_and_generic_clause_are_atomically_refused(self):
        self.advance()
        prepared = self.advance(prepare=True)
        for attack in ('snapshot_root', 'generic_qualification'):
            with self.subTest(attack=attack):
                forged = deepcopy(prepared)
                chosen = self.relation(forged['checkpoint'])['chosen_clause']
                if attack == 'snapshot_root':
                    chosen['progression_roots'] = self.last_fixture['original']['ha_event']['payload']['evidence_roots']
                else:
                    chosen['qualification'] = 'experimental_shadow'
                self.reject_unchanged(forged)

    def test_forged_suspended_dwell_progress_tick_and_nested_reducer_pins_are_refused(self):
        self.advance()
        prepared = self.advance(prepare=True)
        for attack in ('suspended_dwell', 'seed_tick', 'reducer_digest', 'reducer_version'):
            with self.subTest(attack=attack):
                forged = deepcopy(prepared)
                reducer = forged['checkpoint']['state']['engagement']['reducer']
                relation = self.relation(forged['checkpoint'])
                if attack == 'suspended_dwell': relation.update(state='suspended', enter_dwell=600)
                elif attack == 'seed_tick': relation['enter_progress_tick'] = 0
                elif attack == 'reducer_digest': reducer['config_digest'] = 'f' * 64
                else: reducer['version'] = 'forged-reducer-version'
                self.reject_unchanged(forged, 'checkpoint_conflict' if attack.startswith('reducer_') else 'invalid_pc_support')

    def test_private_originals_are_worker_only_and_absent_from_public_stream(self):
        decision = self.advance()
        for route in ('/live-input-cut' + self.scope + '&consumer_id=' + self.config['worker_id'],
                      '/worker-commits/' + self.config['worker_id'] + self.scope):
            status, value = self.request('GET', route, token=HUMAN)
            self.assertEqual(status, 403, value)
        status, page = self.request('GET', '/events' + self.scope, token=HUMAN)
        self.assertEqual(status, 200, page)
        self.assertTrue(any(row['payload'].get('kind') == 'pc_input_interaction' for row in page['items']), page)
        public = json.dumps([page, decision])
        for private_key in ('pc_input_originals', 'ha_event', 'sensor.pc_lastactive', 'input_rules', 'clock_qualifications'):
            self.assertNotIn(private_key, public)
        self.assertTrue(self.checkpoint()['checkpoint']['state']['pc_input_originals'])


if __name__ == '__main__':
    unittest.main()
