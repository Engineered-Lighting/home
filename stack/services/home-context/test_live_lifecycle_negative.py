"""Qualified negative semantics at the real journal/Node worker boundary.

Captures, methods and coverage are fictional. Only the generated loopback
journal endpoint is contacted; no camera, model, household or host data runs.
"""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from journal import Journal, JournalError, PREFIX
from live_delivery_test_helpers import delivery_geometry
from live_lifecycle import qualified_negative, resolve_basis
from live_lifecycle_test_helpers import lifecycle_fixture, lifecycle, clearance
from live_scene_test_helpers import HUMAN_TOKEN
from live_test_helpers import LiveHttp, NOW, PROFILE, TOKEN, event, fact, publication_ref, person, subject, reference
from live_worker_test_helpers import WorkerProbe, worker_receipt_resolver


class LifecycleNegativeScopeTests(unittest.TestCase):
    def test_only_assessed_absence_or_departure_can_cover_prior_subjects_of_exact_person(self):
        _, _, config = lifecycle_fixture()
        policy = config['lifecycle_policy']
        for disposition in ('departure_qualified', 'absence_qualified'):
            p = lifecycle(1, [], disposition)
            p['target_subject_ref'] = None
            self.assertTrue(qualified_negative(p, policy, person(), subject()))
            self.assertTrue(qualified_negative(p, policy, person(), subject('other-existing-subject')))
            self.assertFalse(qualified_negative(p, policy, person('new-person-epoch'), subject()))
            for field in ('method_ref', 'qualification_ref', 'applicability_ref', 'coverage_assessment_ref'):
                wrong = copy.deepcopy(p)
                wrong[field] = reference('uncommissioned-' + field)
                self.assertFalse(qualified_negative(wrong, policy, person(), subject()))
        for disposition in ('use_ended_qualified', 'continuity_qualified', 'observation_gap', 'association_reset'):
            p = lifecycle(1, [], disposition)
            p['target_subject_ref'] = None
            self.assertFalse(qualified_negative(p, policy, person(), subject()))
        p = lifecycle(1, [], 'use_ended_qualified')
        self.assertTrue(qualified_negative(p, policy, person(), subject()))
        self.assertFalse(qualified_negative(p, policy, person(), subject('other-existing-subject')))


class LifecycleNegativeWorkerTests(unittest.TestCase):
    def setUp(self):
        self.allowed_ports = set()
        connect = socket.create_connection
        def local_only(address, *args, **kwargs):
            if address[0] != '127.0.0.1' or address[1] not in self.allowed_ports:
                raise AssertionError('nonfixture_connection_forbidden')
            return connect(address, *args, **kwargs)
        guard = patch('socket.create_connection', side_effect=local_only)
        guard.start()
        self.addCleanup(guard.stop)
        work = tempfile.TemporaryDirectory(prefix='authored-negative-worker-')
        self.addCleanup(work.cleanup)
        self.now, self.seq, self.heads, self.originals = NOW - 2.2, 0, {}, {}
        self.service, self.grants, self.config = lifecycle_fixture()
        self.journal = Journal(Path(work.name) / 'authored.sqlite', grants=self.grants, profile=PROFILE,
            clock=lambda: self.now, live_config=self.service, live_receipt_resolver=worker_receipt_resolver)
        self.addCleanup(self.journal.close)
        self.http = LiveHttp(self.journal)
        self.allowed_ports.add(self.http.server.server_port)
        self.addCleanup(self.http.close)
        self.config['service_url'] = f'http://127.0.0.1:{self.http.server.server_port}'
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.probe.command('start', config=self.config, t=self.now)
        initial = self.tick()
        self.now = NOW - 1.8
        command = clearance('authored-bootstrap-clearance', initial['person_hazards']['items'], self.config['lifecycle_policy'], self.now)
        status, result = self.http.request('POST', PREFIX + '/intent-events', command, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, result)
        self.now = NOW - 1.2
        self.assertEqual(self.tick()['person_hazards']['items'], [])

    def tick(self):
        result = self.probe.command('tick', t=self.now)
        self.assertEqual(len(result['decisions']), 1,
            {k: result.get(k) for k in ('statuses', 'http_errors')})
        return result['decisions'][0]

    def publish(self, payload):
        key = json.dumps(payload['head']['key'], sort_keys=True)
        if key in self.heads:
            payload['head']['supersedes'] = publication_ref(self.heads[key])
        status, receipt = self.http.request('POST', PREFIX + '/events', event(payload))
        self.assertEqual(status, 200, receipt)
        self.heads[key] = receipt
        self.originals[payload['id']] = copy.deepcopy(payload)
        return publication_ref(receipt)

    def advance(self, disposition=None, person_wide=False):
        self.seq += 1
        self.now = NOW + self.seq - 1 + .2
        f = fact('authored-use-' + str(self.seq), seq=self.seq)
        if disposition:
            f.update(status='unobservable', value=None, reason='material_occluded')
        self.last_fact = self.publish(f)
        geometry_ref = self.publish(delivery_geometry(self.seq))
        if disposition:
            proof = lifecycle(self.seq, [geometry_ref], disposition)
            proof['available_at'] = self.now
            if person_wide:
                proof['target_subject_ref'] = None
                proof['head']['key']['subject_ref'] = None
            self.publish(proof)
        return self.tick()

    def latest_relation(self):
        row = self.journal.db.execute("SELECT body FROM publications WHERE payload_type='home-live-engagement-state-1' ORDER BY cursor DESC LIMIT 1").fetchone()
        return json.loads(row['body'])['payload']

    def test_person_wide_absence_ends_prior_episode_with_observed_exit_dwell_and_restores(self):
        for _ in range(8):
            initial = self.advance()
        self.assertEqual(initial['execution']['status'], 'virtual')
        original_episode = self.latest_relation()['episode_id']
        for n in range(21):
            self.advance('absence_qualified', person_wide=True)
            relation = self.latest_relation()
            self.assertEqual(relation['person_ref'], person())
            self.assertEqual(relation['subject_ref'], subject())
            self.assertEqual(relation['refinement']['activity'], 'unknown')
            self.assertNotEqual(relation['refinement']['status'], 'supported')
            if n < 20:
                self.assertEqual(relation['state'], 'ending', relation)
                self.assertEqual(relation['episode_id'], original_episode)
            else:
                self.assertEqual(relation['state'], 'unknown', relation)
                self.assertIsNone(relation['episode_id'])
            if n == 5:
                self.probe.command('stop')
                self.probe.command('start', config=self.config, t=self.now + .1)
        checkpoint = json.loads(self.journal.db.execute('SELECT checkpoint FROM workers').fetchone()['checkpoint'])
        private = next(iter(checkpoint['state']['engagement']['reducer']['state']['relations'].values()))
        self.assertEqual(private['exit_dwell'], 20)
        self.assertEqual(private['chosen_clause']['qualification'], 'qualified_lifecycle')

    def test_historical_positive_basis_survives_ordinary_supersession_then_source_barrier_fences_it(self):
        for _ in range(8):
            self.advance()
        prior_ref = self.last_fact
        prior = self.originals[prior_ref['id']]
        self.seq += 1
        self.now = NOW + self.seq - 1 + .2
        current_geometry = self.publish(delivery_geometry(self.seq))
        proof = lifecycle(self.seq, [prior_ref, current_geometry], 'use_ended_qualified')
        proof['available_at'] = self.now
        for key in ('observation_root_times', 'sensor_root_bindings'):
            proof[key] = {**prior[key], **proof[key]}
        proof['observation_roots'] = sorted(proof['observation_root_times'])
        proof['sensor_roots'] = sorted(set(prior['sensor_roots'] + proof['sensor_roots']))
        proof_ref = self.publish(proof)
        other_source = copy.deepcopy(proof)
        other_source['source_id'] = 'other-commissioned-source'
        with self.assertRaises(JournalError) as mismatch:
            resolve_basis(self.journal.dynamic, prior_ref, self.journal.authenticate(TOKEN), event(other_source))
        self.assertEqual(mismatch.exception.code, 'invalid_lifecycle_basis')
        missing = fact('ordinary-nonholding-' + str(self.seq), seq=self.seq)
        missing.update(status='unobservable', value=None, reason='material_occluded')
        self.publish(missing)
        row = self.journal.db.execute('SELECT body FROM live_heads WHERE cursor=?', (proof_ref['cursor'],)).fetchone()
        self.assertIsNotNone(row['body'], 'superseded historical identity basis must not withdraw the later proof')
        self.tick()
        self.assertEqual(self.latest_relation()['state'], 'ending')
        from live_test_helpers import barrier
        self.now = NOW + self.seq
        stop = barrier(list(self.heads.values()), event_id='historical-basis-source-stop', at=self.now)
        status, receipt = self.http.request('POST', PREFIX + '/events', event(stop))
        self.assertEqual(status, 200, receipt)
        self.assertIsNone(self.journal.db.execute('SELECT body FROM live_heads WHERE cursor=?', (proof_ref['cursor'],)).fetchone()['body'])
        self.now += 1
        self.assertEqual(self.tick()['execution']['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
