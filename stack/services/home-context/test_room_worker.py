"""Adversarial room claims through the public controlled factory/journal graph.

Only external camera/SDK observations and the shared clock ports are authored.
No completed observations, scenes, commits or database rows are seeded.
"""
import copy
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
from controlled_external_io import build_base_configuration, assemble_journal_grants
from room_configuration import upgrade_configuration
from journal import Journal, PREFIX, digest
from schema_registry import schema_digest
from live_acquisition import create_production_acquisition
from live_worker_test_helpers import WorkerProbe

PRODUCER_TOKEN, WORKER_TOKEN, HUMAN_TOKEN = ('authored-room-producer', 'authored-room-worker', 'authored-room-owner')


class RoomWorkerTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000.
        self.identity = dict(site_id='authored-site', room_id='authored-room', session_id='authored-room-authority', domain_epoch='authored-room-domain')
        self.directory = tempfile.TemporaryDirectory(prefix='authored-room-authority-', dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.bundle = build_base_configuration(session_ref=self.identity, node_runtime=shutil.which('node'),
                                                wall_clock=lambda: self.now, monotonic=lambda: self.now - 1799999000.)
        self.old_configuration = copy.deepcopy(self.bundle['configuration'])
        self.old_worker = copy.deepcopy(self.bundle['worker_config'])
        policy = json.loads(subprocess.check_output(['node', '-e', "process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"], cwd=ROOT, text=True))
        policy['registration'] = copy.deepcopy(self.bundle['worker_config']['delivery_policy']['registration'])
        for fixture in policy['fixtures']:
            fixture['task_policy'] = copy.deepcopy(self.bundle['worker_config']['delivery_policy'])
            fixture['task_policy']['fixture'].update(id=fixture['fixture_id'], fixture_pose_ref=copy.deepcopy(fixture['pose_ref']))
        if self._testMethodName == 'test_concurrent_exposure_cannot_be_bypassed_by_valid_single_fixture_delivery':
            policy['exposure']['max_total'] = .005
        def current(value):
            if isinstance(value, dict):
                for key in value:
                    if key == 'valid_until': value[key] = self.now + 180
                    else: current(value[key])
            elif isinstance(value, list):
                for row in value: current(row)
        current(policy)
        self.bundle['configuration'], self.config = upgrade_configuration(self.bundle['configuration'], self.bundle['worker_config'], policy)
        self.bundle['worker_config'] = self.config
        self.grants = assemble_journal_grants(self.bundle['configuration'], self.config,
            publication_token=PRODUCER_TOKEN, worker_token=WORKER_TOKEN, human_token=HUMAN_TOKEN)
        self.bundle['occupancy'].start()
        self.addCleanup(self.bundle['occupancy'].stop)
        self.wait_occupancy()
        self.graph = create_production_acquisition(self.bundle['acquisition_config'], live_config=self.bundle['configuration'],
            journal_path=Path(self.directory.name) / 'authored.sqlite', journal_grants=self.grants, publication_token=PRODUCER_TOKEN,
            clock=lambda: self.now, monotonic=lambda: self.now - 1799999000.,
            **{key: self.bundle[key] for key in ('capture_policy', 'capture_method', 'spatial_policy', 'interaction_policy', 'lifecycle_policy',
                'detector', 'pose', 'local_authority', 'household_enabled', 'occupancy', 'camera_dependencies', 'control_clock', 'assessment_recorder', 'rss')})
        self.addCleanup(self.graph.close)
        self.config.update(service_url='http://127.0.0.1:' + str(self.graph.address[1]), token=WORKER_TOKEN)
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.scope = '?site_id=' + self.identity['site_id'] + '&room_id=' + self.identity['room_id']
        self.checkpoint()
        self.probe.command('start', config=self.config, t=self.now)
        first = self.tick()
        self.now += 1
        value = {key: copy.deepcopy(value) for key, value in self.config['lifecycle_policy']['operator_clearance'].items() if key != 'max_age_s'}
        value.update(hazard_refs=[item['hazard_ref'] for item in first['person_hazards']['items']], inspection_at=self.now)
        intent = dict(schema='home-live-intent-event-2', version=2, id='authored-room-inspection', **self.identity,
            manifest_ref=copy.deepcopy(self.config['manifest_ref']), actor_ref='controlled-owner', origin='human_declaration', truth_role='runtime_intent',
            kind='clear_person_hazards', target=None, set_at=self.now, available_at=self.now, valid_until=self.now+5, reply_to=None, value=value)
        status, result = self.request('POST', '/intent-events', intent, HUMAN_TOKEN)
        self.assertEqual(status, 200, result)
        self.assertEqual(self.tick()['intent_results'][0]['status'], 'applied')

    def wait_occupancy(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            value = self.bundle['occupancy'].reading()
            if value['state'] == 'occupied' and value['observed_at'] >= self.now: return
            time.sleep(.01)
        self.fail('authored occupancy input did not reach its public reader')

    def request(self, method, route, body=None, token=WORKER_TOKEN):
        connection = http.client.HTTPConnection('127.0.0.1', self.graph.address[1], timeout=4)
        try:
            headers = {'Authorization': 'Bearer ' + token}
            if body is not None: headers['Content-Type'] = 'application/json'
            connection.request(method, PREFIX + route, None if body is None else json.dumps(body), headers)
            result = connection.getresponse()
            return result.status, json.loads(result.read())
        finally:
            connection.close()

    def tick(self, prepare=False):
        if prepare: self.probe.command('fault', mode='before_commit')
        result = self.probe.command('tick', t=self.now)
        if prepare:
            self.assertTrue(result['commit_posts'], result)
            return json.loads(result['commit_posts'][-1])
        self.assertEqual(len(result['decisions']), 1, result)
        return result['decisions'][0]

    def advance(self, *, prepare=False, scenario='presence'):
        self.now += 1
        self.bundle['external_io'].set_scenario(scenario)
        self.wait_occupancy()
        cycle = self.graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        return self.tick(prepare)

    def checkpoint(self):
        status, result = self.request('GET', '/worker-commits/' + self.config['worker_id'] + self.scope)
        self.assertEqual(status, 200, result)
        return result

    def establish_task(self):
        for _ in range(12):
            decision = self.advance(scenario='stationary_work')
            if decision['execution']['status'] == 'virtual' and decision['execution']['readback'][0]['brightness_fraction'] > 0:
                self.assertEqual(decision['room_output']['task']['fixture_id'], 'fixture-1')
                return decision
        self.fail('controlled original evidence did not establish a sampled task: ' + json.dumps(decision))

    @staticmethod
    def decision(commit):
        return commit['publications'][-1]['payload']

    def resign_room(self, commit):
        decision = self.decision(commit)
        body = {key: value for key, value in decision['room_output'].items() if key != 'revision_id'}
        revision = 'room:' + digest([self.identity, decision['seq'], body])[:48]
        decision['room_output']['revision_id'] = revision
        decision['background_delivery']['revision_id'] = revision

    def reject_atomically(self, commit, expected='invalid_room_output'):
        before = self.checkpoint()
        status, result = self.request('POST', '/worker-commits', commit)
        self.assertEqual(status, 409, result)
        self.assertEqual(result['error']['code'], expected, result)
        self.assertEqual(self.checkpoint(), before)

    def test_rehashed_forged_ambient_sample_is_refused_without_advancing_public_checkpoint(self):
        first = self.advance()
        self.assertEqual(first['room_output']['fixtures'][0]['domains'][0]['status'], 'modeled')
        commit = self.advance(prepare=True)
        domain = self.decision(commit)['room_output']['fixtures'][0]['domains'][0]
        self.assertGreater(domain['sample']['brightness_fraction'], 0)
        domain['sample']['brightness_fraction'] = .5
        self.resign_room(commit)
        self.reject_atomically(commit)

    def test_nonselected_member_cannot_hide_a_plan_behind_dark_room_output(self):
        self.establish_task()
        commit = self.advance(prepare=True, scenario='stationary_work')
        room = commit['checkpoint']['state']['room_execution']
        selected = room['last_selected_fixture_id']
        actual = next(row for row in room['tasks'] if row['fixture_id'] == selected)
        hidden = next(row for row in room['tasks'] if row['fixture_id'] != selected)
        self.assertIsNone(hidden['snapshot']['state']['plan'])
        hidden['snapshot']['state']['plan'] = copy.deepcopy(actual['snapshot']['state']['plan'])
        self.reject_atomically(commit, 'invalid_delivery_withdrawal')

    def test_nonselected_member_cannot_erase_shared_person_hazards(self):
        self.establish_task()
        commit = self.advance(prepare=True, scenario='stationary_work')
        room = commit['checkpoint']['state']['room_execution']
        hidden = next(row for row in room['tasks'] if row['fixture_id'] != room['last_selected_fixture_id'])
        self.assertTrue(hidden['snapshot']['state']['hazards'])
        hidden['snapshot']['state'].update(hazards=[], known_persons=[], person_observed_through={}, unresolved_person=False)
        self.reject_atomically(commit, 'invalid_room_checkpoint')

    def test_selected_member_cannot_borrow_another_fixture_policy_digest(self):
        self.establish_task()
        commit = self.advance(prepare=True, scenario='stationary_work')
        room = commit['checkpoint']['state']['room_execution']
        selected = next(row for row in room['tasks'] if row['fixture_id'] == room['last_selected_fixture_id'])
        other = next(row for row in room['tasks'] if row['fixture_id'] != room['last_selected_fixture_id'])
        self.assertNotEqual(selected['snapshot']['config_digest'], other['snapshot']['config_digest'])
        selected['snapshot']['config_digest'] = other['snapshot']['config_digest']
        commit['checkpoint']['state']['virtual_execution'] = copy.deepcopy(selected['snapshot'])
        self.reject_atomically(commit, 'invalid_delivery_checkpoint')

    def test_config7_cannot_acquire_a_room8_grant(self):
        grants = assemble_journal_grants(self.old_configuration, self.old_worker,
            publication_token=PRODUCER_TOKEN, worker_token=WORKER_TOKEN, human_token=HUMAN_TOKEN)
        worker = next(grant for grant in grants if 'worker_commit' in grant['operations'])
        worker['payload_types'].append('home-live-shadow-decision-8')
        worker['schema_pins']['home-live-shadow-decision-8'] = schema_digest('home-live-shadow-decision-8', profile='home-live-shadow-1')
        with self.assertRaisesRegex(ValueError, 'room authority requires explicit config9'):
            Journal(Path(self.directory.name) / 'forbidden-config7.sqlite', grants=grants, profile='home-live-shadow-1',
                clock=lambda: self.now, live_config=self.old_configuration,
                live_wall_clock=lambda: self.now, live_monotonic=lambda: self.now-1799999000.)

    def test_unknown_exposure_assessment_cannot_substitute_for_commissioned_bounds(self):
        self.advance()
        commit = self.advance(prepare=True)
        domain = self.decision(commit)['room_output']['fixtures'][0]['domains'][0]
        domain['assessment_refs'][1] = dict(id='uncommissioned-exposure-assessment', digest='c'*64)
        self.resign_room(commit)
        self.reject_atomically(commit)

    def forge_single_fixture_task(self, commit):
        """Adversarially run the original task calculator without room budgeting.

        Inputs are the public original cut and trusted public checkpoint. The
        journal still checks all original task geometry/motion before room bounds.
        """
        before = self.checkpoint()['checkpoint']
        status, cut = self.request('GET', '/live-input-cut' + self.scope + '&consumer_id=' + self.config['worker_id'])
        self.assertEqual(status, 200, cut)
        self.assertEqual(cut['cursor'], commit['input_cursor'])
        decision = self.decision(commit)
        fixture = self.config['room_policy']['fixtures'][0]
        old = next(row['snapshot'] for row in before['state']['room_execution']['tasks'] if row['fixture_id'] == fixture['fixture_id'])
        receipts = {row['cursor']: row for row in commit['input_receipts']}
        publications = cut['items']
        def reference(row):
            return {**{key: row[key] for key in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')}, 'component_id': None}
        def normalized(row):
            payload = copy.deepcopy(row['payload'])
            source = self.config['live_manifest']['sources'][payload['source_id']]
            payload['available_at'] = max(payload['available_at'], row['committed_at'], receipts[row['cursor']]['received_at'])
            payload['valid_until'] = min(payload['valid_until'], payload['observed_at'] + source['max_age_s']) - payload['clock']['uncertainty_s']
            return payload
        data = dict(identity=self.identity, policy=fixture['task_policy'], lifecycle_policy=self.config['lifecycle_policy'], prior=old,
            input=dict(t=decision['t'], seq=decision['seq'], scene=decision['scene'], goal=decision['goal'],
                facts=[normalized(row) for row in publications if row['payload_type'] == 'home-live-fact-1'],
                geometries=[normalized(row) for row in publications if row['payload_type'] == 'home-live-geometry-1'],
                lifecycles=[normalized(row) for row in publications if row['payload_type'] == 'home-live-person-lifecycle-1'],
                geometry_refs=[reference(row) for row in publications if row['payload_type'] == 'home-live-geometry-1'],
                publication_refs={row['payload']['id']: reference(row) for row in publications}, pending_clearance_intents=[], intent_refs={},
                authority_generation=commit['checkpoint']['authority_generation'], input_cut_digest=decision['input_cut']['cut_digest']))
        program = "const fs=require('node:fs'),D=require('./app/src/home-live-task-delivery.js');const v=JSON.parse(fs.readFileSync(0,'utf8'));const d=D.createDelivery({identity:v.identity,policy:v.policy,lifecycle_policy:v.lifecycle_policy,max_output_age_s:3});d.restore(v.prior);const result=d.step(v.input);process.stdout.write(JSON.stringify({result,snapshot:d.snapshot()}));"
        generated = json.loads(subprocess.run(['node', '-e', program], cwd=ROOT, input=json.dumps(data), text=True, capture_output=True, check=True, timeout=10).stdout)
        delivered, snapshot = generated['result'], generated['snapshot']
        self.assertEqual(delivered['execution']['status'], 'virtual', delivered)
        for key in ('allocation', 'execution', 'display_lease'):
            decision[key] = copy.deepcopy(delivered[key])
        room = commit['checkpoint']['state']['room_execution']
        room['last_selected_fixture_id'] = fixture['fixture_id']
        next(row for row in room['tasks'] if row['fixture_id'] == fixture['fixture_id'])['snapshot'] = copy.deepcopy(snapshot)
        commit['checkpoint']['state']['virtual_execution'] = copy.deepcopy(snapshot)
        output = decision['room_output']
        output['task'] = dict(fixture_id=fixture['fixture_id'], **{key: copy.deepcopy(delivered[key]) for key in ('allocation', 'execution', 'display_lease')})
        output['task_candidates'][0] = copy.deepcopy(delivered['allocation']['candidates'][0])
        assignment, sample = delivered['allocation']['assignments'][0], delivered['execution']['readback'][0]
        output['fixtures'][0]['domains'][7] = dict(domain='spotlight', status='modeled', reason='qualified_modeled_task',
            goal_id=assignment['goal_id'], scene_id=decision['scene']['scene_id'],
            desired={key: assignment['nominal'][key] for key in ('brightness_fraction', 'cct_kelvin')},
            sample={key: sample[key] for key in ('brightness_fraction', 'cct_kelvin')}, valid_until=delivered['display_lease']['valid_until'],
            evidence_refs=copy.deepcopy(delivered['display_lease']['geometry_refs']), assessment_refs=copy.deepcopy(delivered['execution']['assessment_refs']))
        self.resign_room(commit)

    def test_concurrent_exposure_cannot_be_bypassed_by_valid_single_fixture_delivery(self):
        for _ in range(12):
            decision = self.advance(scenario='stationary_work')
            if decision['allocation']['reason'] == 'room_aggregate_exposure_exceeded': break
        self.assertEqual(decision['allocation']['reason'], 'room_aggregate_exposure_exceeded', decision)
        commit = self.advance(prepare=True, scenario='stationary_work')
        self.forge_single_fixture_task(commit)
        # Ambient .00224 and task .004 each fit .005; together .00624 does not.
        self.reject_atomically(commit)


if __name__ == '__main__':
    unittest.main()
