"""Config6 standalone lifecycle through actual authenticated journal HTTP.

All captures, qualifications, references and clocks are authored. Network access
is restricted to the one temporary local test journal; no producer/model runs.
"""
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from journal import Journal, JournalError, PREFIX
from live_lifecycle import TYPE, validate_lifecycle_policy
from live_lifecycle_test_helpers import lifecycle_fixture, lifecycle
from live_test_helpers import LiveHttp, NOW, PROFILE, event, fact, publication_ref, receipt_resolver, reference


class LifecycleIngressTests(unittest.TestCase):
    def setUp(self):
        self.allowed_ports = set()
        original = socket.create_connection
        def only_local(address, *args, **kwargs):
            if address[0] != '127.0.0.1' or address[1] not in self.allowed_ports:
                raise AssertionError('nonfixture_connection_forbidden')
            return original(address, *args, **kwargs)
        guard = patch('socket.create_connection', side_effect=only_local)
        guard.start()
        self.addCleanup(guard.stop)
        self.work = tempfile.TemporaryDirectory(prefix='authored-lifecycle-ingress-')
        self.addCleanup(self.work.cleanup)
        self.now = NOW + .2
        self.service, self.grants, self.config = lifecycle_fixture()
        self.journal = Journal(Path(self.work.name) / 'authored.sqlite', grants=self.grants, profile=PROFILE,
            clock=lambda: self.now, live_config=self.service, live_receipt_resolver=receipt_resolver)
        self.addCleanup(self.journal.close)
        self.http = LiveHttp(self.journal)
        self.allowed_ports.add(self.http.server.server_port)
        self.addCleanup(self.http.close)
        self.parents = {}

    def post(self, payload):
        return self.http.request('POST', PREFIX + '/events', event(payload))

    def basis(self, seq=1, track=None):
        self.now = NOW + seq - 1 + .2
        payload = fact('authored-lifecycle-basis-' + str(seq), seq=seq)
        if track:
            payload['person_ref']['track_epoch'] = track
            payload['head']['key']['person_ref'] = deepcopy(payload['person_ref'])
        status, receipt = self.post(payload)
        self.assertEqual(status, 200, receipt)
        self.parents[receipt['cursor']] = payload
        return receipt

    def proof(self, seq, receipt, disposition='continuity_qualified'):
        self.now = NOW + seq - 1 + .2
        payload = lifecycle(seq, [publication_ref(receipt)], disposition)
        parent = self.parents[receipt['cursor']]
        payload['target_person_ref'] = deepcopy(parent['person_ref'])
        payload['target_subject_ref'] = deepcopy(parent['subject_ref'])
        payload['head']['key'].update(person_ref=deepcopy(parent['person_ref']), subject_ref=deepcopy(parent['subject_ref']))
        payload['observation_root_times'].update(parent['observation_root_times'])
        payload['sensor_root_bindings'].update(parent['sensor_root_bindings'])
        payload['observation_roots'] = sorted(payload['observation_root_times'])
        payload['sensor_roots'] = sorted({root for roots in payload['sensor_root_bindings'].values() for root in roots})
        return payload

    def test_exact_capture_lineage_and_basis_are_durable_without_fact_projection(self):
        prior = self.basis()
        payload = self.proof(2, prior)
        status, receipt = self.post(payload)
        self.assertEqual(status, 200, receipt)
        self.assertEqual(self.post(payload), (200, receipt))
        row = self.journal.db.execute('SELECT body FROM publications WHERE cursor=?', (receipt['cursor'],)).fetchone()
        original = json.loads(row[0])['payload']
        self.assertEqual(original, payload)
        self.assertFalse({'status', 'person_ref', 'parent_refs'} & set(original))
        slot = self.journal.db.execute('SELECT * FROM live_identity_slots').fetchone()
        self.assertEqual(slot['capture_seq'], 1, 'lifecycle does not become a second tracker')

    def test_unknown_fields_missing_roots_receipt_or_capture_clock_never_admit(self):
        prior = self.basis()
        original = self.proof(2, prior)
        for mutate in (lambda p: p.update(status='supplied'), lambda p: p.update(person_ref=p['target_person_ref']),
                       lambda p: p.update(parent_refs=[]), lambda p: p.update(original_receipt_refs=[]),
                       lambda p: p['clock'].update(mapping_id='wrong-clock'),
                       lambda p: p['observation_root_times'].update({'invented': NOW + 1})):
            payload = deepcopy(original)
            mutate(payload)
            with self.subTest(mutate=mutate):
                status, result = self.post(payload)
                self.assertGreaterEqual(status, 400, result)
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM publications WHERE payload_type=?', (TYPE,)).fetchone()[0], 0)

    def test_unqualified_method_wrong_basis_and_missing_ancestral_roots_refuse(self):
        prior = self.basis()
        original = self.proof(2, prior)
        for mutate in (lambda p: p.update(qualification_ref=reference('not-commissioned')),
                       lambda p: p['basis_refs'][0].update(payload_digest='f' * 64),
                       lambda p: p.update(basis_refs=[]),
                       lambda p: (p['observation_root_times'].pop('synthetic-frame-1:rgb'), p['sensor_root_bindings'].pop('synthetic-frame-1:rgb'), p['observation_roots'].remove('synthetic-frame-1:rgb'))):
            payload = deepcopy(original)
            mutate(payload)
            with self.subTest(mutate=mutate):
                self.assertGreaterEqual(self.post(payload)[0], 400)

    def test_departure_needs_matching_assessed_coverage_and_cannot_flip_a_new_track(self):
        prior = self.basis()
        self.basis(2, track='new-active-track')
        payload = self.proof(3, prior, 'absence_qualified')
        bad = deepcopy(payload)
        bad['coverage_assessment_ref'] = reference('uncommissioned-coverage')
        self.assertGreaterEqual(self.post(bad)[0], 400)
        slot_before = dict(self.journal.db.execute('SELECT * FROM live_identity_slots').fetchone())
        status, result = self.post(payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(dict(self.journal.db.execute('SELECT * FROM live_identity_slots').fetchone()), slot_before)
        self.assertEqual(slot_before['track_epoch'], 'new-active-track')
        continuity = self.proof(4, prior)
        self.assertGreaterEqual(self.post(continuity)[0], 400, 'old epoch cannot regain continuity')

    def test_source_failure_cannot_fabricate_capture_and_historical_profile_rejects_lifecycle(self):
        prior = self.basis()
        payload = self.proof(2, prior, 'observation_gap')
        payload.update(observation_roots=[], observation_root_times={}, sensor_root_bindings={}, sensor_roots=[], original_receipt_refs=[])
        self.assertGreaterEqual(self.post(payload)[0], 400)
        from live_ingress import validate_grants
        from journal import Grant
        historical = deepcopy(self.service)
        historical['version'] = 5
        with self.assertRaises(ValueError):
            validate_grants([Grant.from_dict(g, profile=PROFILE) for g in self.grants], historical)

    def test_lifecycle_policy_cannot_smuggle_unregistered_methods_or_unbounded_gaps(self):
        policy = deepcopy(self.config['lifecycle_policy'])
        validate_lifecycle_policy(policy, self.service['manifest']['sources'])
        for mutate in (lambda p: p.update(extra=True), lambda p: p['continuity_methods'][0].update(max_gap_s=31),
                       lambda p: p['negative_methods'][0].update(method_ref=reference('unregistered'))):
            changed = deepcopy(policy)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(JournalError):
                validate_lifecycle_policy(changed, self.service['manifest']['sources'])


if __name__ == '__main__':
    unittest.main()
