"""Authored config6 through public HTTP and the actual persistent worker."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from journal import Journal, PREFIX
from live_test_helpers import LiveHttp, NOW, PROFILE, event, fact, publication_ref
from live_worker_test_helpers import WorkerProbe, worker_receipt_resolver, WORKER_TOKEN
from live_scene_test_helpers import HUMAN_TOKEN
from live_lifecycle_test_helpers import lifecycle_fixture, clearance, brightness
from live_delivery_test_helpers import delivery_geometry


class LifecycleDeliveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='authored-lifecycle-delivery-', dir=Path(__file__).parent)
        self.addCleanup(work.cleanup)
        self.service, self.grants, self.config = lifecycle_fixture()
        self.now, self.seq, self.heads = NOW + .2, 2, {}
        self.j = Journal(Path(work.name) / 'fictional.sqlite', grants=self.grants, profile=PROFILE,
                         clock=lambda: self.now, live_config=self.service, live_receipt_resolver=worker_receipt_resolver)
        self.addCleanup(self.j.close)
        self.http = LiveHttp(self.j)
        self.addCleanup(self.http.close)
        self.config['service_url'] = f'http://127.0.0.1:{self.http.server.server_port}'
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.probe.command('start', config=self.config, t=self.now)

    def tick(self):
        result = self.probe.command('tick', t=self.now)
        self.assertEqual(len(result['decisions']), 1, {'statuses':result['statuses'],'http_errors':result['http_errors']})
        return result['decisions'][0]

    def reconcile(self):
        first = self.tick()
        self.assertEqual(first['person_hazards']['items'][0]['kind'], 'unreconciled_session_history')
        self.now = NOW + 1.2
        intent = clearance('intent:bootstrap', first['person_hazards']['items'], self.config['lifecycle_policy'], self.now)
        status, receipt = self.http.request('POST', PREFIX + '/intent-events', intent, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, receipt)
        decision = self.tick()
        self.assertFalse(decision['person_hazards']['items'])
        self.assertEqual(decision['intent_results'][0]['status'], 'applied')

    def advance(self):
        self.seq += 1
        self.now = NOW + self.seq - 1 + .2
        for payload in [fact('authored-life-fact-' + str(self.seq), seq=self.seq), delivery_geometry(self.seq)]:
            key = json.dumps(payload['head']['key'], sort_keys=True)
            if key in self.heads:
                payload['head']['supersedes'] = publication_ref(self.heads[key])
            status, receipt = self.http.request('POST', PREFIX + '/events', event(payload))
            self.assertEqual(status, 200, receipt)
            self.heads[key] = receipt
        return self.tick()

    def test_brightness_acceptance_and_application_preserve_scene_aim_and_cct(self):
        self.reconcile()
        for _ in range(12):
            before = self.advance()
        self.assertEqual(before['execution']['readback'][0]['phase'], 'lit')
        self.now += .1
        intent = brightness('intent:dim', before['scene'], self.now, .2)
        status, receipt = self.http.request('POST', PREFIX + '/intent-events', intent, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, receipt)
        self.assertEqual(receipt['status'], 'accepted')
        after = self.advance()
        self.assertEqual(after['scene']['scene_id'], before['scene']['scene_id'])
        self.assertEqual(after['scene']['example_ref'], before['scene']['example_ref'])
        self.assertEqual(after['scene']['nominal']['cct_kelvin'], before['scene']['nominal']['cct_kelvin'])
        self.assertEqual(after['execution']['status'], 'virtual', {'execution': after['execution'], 'allocation': after['allocation']})
        self.assertEqual(after['execution']['readback'][0]['brightness_fraction'], before['execution']['readback'][0]['brightness_fraction'])
        for axis in ['pan_deg', 'tilt_deg']:
            self.assertEqual(after['execution']['readback'][0][axis], before['execution']['readback'][0][axis])
        query = '?site_id=' + intent['site_id'] + '&room_id=' + intent['room_id']
        status, found = self.http.request('GET', PREFIX + '/intent-events/' + intent['id'] + query, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, found)
        self.assertEqual(found['receipt']['status'], 'accepted')
        self.assertEqual(found['application_result']['status'], 'applied')

    def test_journal_rejects_erased_bootstrap_history(self):
        self.probe.command('fault', mode='before_commit')
        result = self.probe.command('tick', t=self.now)
        commit = json.loads(result['commit_posts'][-1])
        state = commit['checkpoint']['state']['virtual_execution']['state']
        state['hazards'] = []
        state['unresolved_person'] = False
        commit['publications'][-1]['payload']['person_hazards']['items'] = []
        status, response = self.http.request('POST', PREFIX + '/worker-commits', commit, token=WORKER_TOKEN)
        self.assertEqual(status, 409, response)
        self.assertEqual(response['error']['code'], 'invalid_delivery_hazards')
