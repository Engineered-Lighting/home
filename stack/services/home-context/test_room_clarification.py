"""Independent clarification contract checks using authored public inputs."""
from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest

from room_clarification import validate_clarification_policy
from live_test_helpers import reference
from journal import Journal, PREFIX, digest
from live_test_helpers import LiveHttp, NOW, PROFILE, SCOPE, barrier, event, fact, publication_ref
from live_scene_test_helpers import HUMAN_ACTOR, HUMAN_TOKEN
from live_delivery_test_helpers import delivery_geometry
from live_worker_test_helpers import WorkerProbe, WORKER_TOKEN, WORKER_ID, worker_receipt_resolver


def clarification_fixture():
    """Public config10 fixture with real room, journal and worker interfaces."""
    import subprocess
    import sys
    from live_conservative_test_helpers import conservative_fixture
    from live_test_helpers import MANIFEST, METHOD, PROFILE
    from schema_registry import schema_digest
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / 'tools/den-live-runtime'))
    from room_configuration import upgrade_configuration
    service, grants, config = conservative_fixture()
    room = json.loads(subprocess.check_output(['node', '-e', "process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"], cwd=root, text=True))
    room['registration'] = deepcopy(config['delivery_policy']['registration'])
    def current(value):
        if isinstance(value, dict):
            for key in value:
                if key == 'valid_until': value[key] = NOW + 180
                else: current(value[key])
        elif isinstance(value, list):
            for row in value: current(row)
    current(room)
    policy = dict(schema='home-live-clarification-policy-1', version=1,
                  proposal_ttl_s=30, max_pending=1, max_history=128,
                  repeat_policy='one_per_scope', material_methods=[deepcopy(METHOD)],
                  interaction_methods=[deepcopy(METHOD)])
    service, config = upgrade_configuration(service, config, room, clarification_policy=policy)
    MANIFEST.update(service['manifest_ref'])
    retired = {'home-live-shadow-decision-6','home-live-worker-checkpoint-6','home-live-worker-commit-6','home-live-input-cut-6'}
    schemas = {'home-live-shadow-decision-9','home-live-worker-checkpoint-9','home-live-worker-commit-9','home-live-input-cut-9',
               'home-live-room-output-1','home-live-room-snapshot-1','home-live-intent-event-3','home-live-intent-event-2','home-live-person-lifecycle-1'}
    for grant in grants:
        grant['payload_types'] = [name for name in grant['payload_types'] if name not in retired]
        if 'worker_commit' in grant['operations']:
            grant['payload_types'] += sorted(schemas)
        elif 'intent' in grant['operations']:
            grant['payload_types'] += ['home-live-intent-event-3', 'home-live-shadow-decision-9']
        if {'intent','worker_commit'} & set(grant['operations']):
            grant['revisions'] = list(dict.fromkeys([value for value in grant['revisions'] if value not in {'6.0','8.0'}] + ['3.0','9.0']))
        grant['payload_types'] = list(dict.fromkeys(grant['payload_types']))
        grant['schema_pins'] = {name: schema_digest(name, profile=PROFILE) for name in grant['payload_types']}
        for binding in grant['live_bindings']: binding['manifest_ref'] = deepcopy(service['manifest_ref'])
    return service, grants, config


class RoomClarificationPolicyTests(unittest.TestCase):
    def test_policy_is_closed_and_only_accepts_commissioned_methods(self):
        method = reference('authored-clarification-method')
        policy = dict(schema='home-live-clarification-policy-1', version=1,
                      proposal_ttl_s=30, max_pending=1, max_history=128,
                      repeat_policy='one_per_scope', material_methods=[method],
                      interaction_methods=[method])
        validate_clarification_policy(policy, [method])
        for mutation in ({'proposal_ttl_s': 31}, {'arbitrary_model': 'no'},
                         {'material_methods': [reference('uncommissioned')]}):
            invalid = deepcopy(policy)
            invalid.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_clarification_policy(invalid, [method])


class RoomClarificationPublicBoundaryTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix='authored-clarification-', dir=Path(__file__).parent)
        self.addCleanup(work.cleanup)
        self.service, self.grants, self.config = clarification_fixture()
        self.now, self.seq, self.heads = NOW + .2, 1, {}
        self.j = Journal(Path(work.name) / 'authored.sqlite', grants=self.grants, profile=PROFILE,
                         clock=lambda: self.now, live_config=self.service,
                         live_receipt_resolver=worker_receipt_resolver,
                         live_wall_clock=lambda: self.now, live_monotonic=lambda: self.now-NOW)
        self.addCleanup(self.j.close)
        self.http = LiveHttp(self.j)
        self.addCleanup(self.http.close)
        self.config['service_url'] = f'http://127.0.0.1:{self.http.server.server_port}'
        self.probe = WorkerProbe()
        self.addCleanup(self.probe.close)
        self.probe.command('start', config=self.config, t=self.now)

    def publish(self, *, geometry=True, interaction='near', missing_protection=False):
        material = fact('clarification-material-' + str(self.seq), seq=self.seq)
        material.update(kind='material_state', value='open', reason='qualified_material_state')
        material['head']['key']['predicate'] = 'material_state'
        near = fact('clarification-interaction-' + str(self.seq), seq=self.seq)
        near.update(kind='hand_material_relation', value=interaction, reason='qualified_hand_material_relation')
        near['head']['key']['predicate'] = 'hand_material_relation'
        payloads = [material, near]
        if geometry:
            payloads.append(delivery_geometry(self.seq, 'missing_protection' if missing_protection else 'supplied'))
        for payload in payloads:
            key = digest(payload['head']['key'])
            if key in self.heads:
                payload['head']['supersedes'] = publication_ref(self.heads[key])
            status, result = self.http.request('POST', PREFIX+'/events', event(payload))
            self.assertEqual(status, 200, result)
            self.heads[key] = result

    def tick(self, *, prepare=False):
        if prepare:
            self.probe.command('fault', mode='before_commit')
        result = self.probe.command('tick', t=self.now)
        if prepare:
            return json.loads(result['commit_posts'][-1])
        self.assertEqual(len(result['decisions']), 1, result)
        return result['decisions'][0]

    def advance(self, **fields):
        self.seq += 1
        self.now = NOW + self.seq - 1 + .2
        self.publish(**fields)
        return self.tick()

    def reply(self, proposal, answer='using_material', suffix='reply'):
        return dict(schema='home-live-intent-event-3', version=3, id='intent:'+suffix, **deepcopy(SCOPE),
                    manifest_ref=deepcopy(self.service['manifest_ref']), actor_ref=HUMAN_ACTOR,
                    origin='human_declaration', truth_role='runtime_intent', kind='activity_reply',
                    target={k: deepcopy(proposal[k]) for k in ('person_ref', 'subject_ref', 'episode_id', 'scene_id')} | {'pending_ref': proposal['proposal_id']},
                    set_at=self.now, available_at=self.now, valid_until=self.now+30,
                    value={'answer': answer}, reply_to=proposal['proposal_id'])

    def test_committed_question_reply_has_durable_attributed_effect_only(self):
        self.publish()
        decision = self.tick()
        self.assertEqual(len(decision['pending']), 1, decision)
        self.assertEqual(len(decision['room_output']['fixtures']), 4)
        self.assertIsNone(decision['room_output']['task']['fixture_id'])
        proposal = decision['pending'][0]
        self.assertIsNone(proposal['episode_id'])
        self.assertIsNone(proposal['scene_id'])
        self.now = NOW + 1.2
        command = self.reply(proposal)
        status, accepted = self.http.request('POST', PREFIX+'/intent-events', command, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, accepted)
        self.assertEqual(accepted['status'], 'accepted')
        result = self.tick()
        self.assertEqual(result['pending'], [])
        self.assertEqual(result['intent_results'], [dict(id=command['id'], status='applied', reason='activity_declaration_recorded', decision_ref=result['id'])])
        self.assertNotEqual(result['scene']['status'], 'selected')
        self.assertEqual(result['display_lease']['status'], 'withdrawn')
        status, lookup = self.http.request('GET', PREFIX+'/intent-events/'+command['id']+'?site_id='+SCOPE['site_id']+'&room_id='+SCOPE['room_id'], token=HUMAN_TOKEN)
        self.assertEqual(status, 200, lookup)
        self.assertEqual(lookup['application_result']['status'], 'applied')
        self.assertEqual(lookup['receipt'], accepted)
        self.assertIsNone(result['room_output']['task']['fixture_id'])
        self.assertEqual(result['background'], decision['background'])

    def test_forged_pending_deadline_is_refused_atomically(self):
        self.publish()
        original = self.tick()['pending'][0]
        route = '/worker-commits/' + WORKER_ID + '?site_id=' + SCOPE['site_id'] + '&room_id=' + SCOPE['room_id']
        self.now += 1
        status, before = self.http.request('GET', PREFIX + route, token=WORKER_TOKEN)
        self.assertEqual(status, 200, before)
        forged = self.tick(prepare=True)
        forged['checkpoint']['state']['pending'][0]['deadline'] += 1
        forged['publications'][-1]['payload']['pending'][0]['deadline'] += 1
        status, rejected = self.http.request('POST', PREFIX + '/worker-commits', forged, token=WORKER_TOKEN)
        self.assertEqual(status, 409, rejected)
        self.assertEqual(rejected['error']['code'], 'invalid_clarification', rejected)
        status, after = self.http.request('GET', PREFIX + route, token=WORKER_TOKEN)
        self.assertEqual(status, 200, after)
        self.assertEqual(after, before)
        self.assertEqual(after['checkpoint']['state']['pending'], [original])

    def test_rehashed_room_sample_still_requires_independent_room_admission(self):
        self.publish()
        self.assertEqual(len(self.tick()['pending']), 1)
        self.now += 1
        route = '/worker-commits/' + WORKER_ID + '?site_id=' + SCOPE['site_id'] + '&room_id=' + SCOPE['room_id']
        status, before = self.http.request('GET', PREFIX + route, token=WORKER_TOKEN)
        self.assertEqual(status, 200, before)
        forged = self.tick(prepare=True)
        decision = forged['publications'][-1]['payload']
        decision['room_output']['fixtures'][0]['domains'][0]['sample'] = dict(brightness_fraction=.5, cct_kelvin=3200)
        body = {key: value for key, value in decision['room_output'].items() if key != 'revision_id'}
        revision = 'room:' + digest([SCOPE, decision['seq'], body])[:48]
        decision['room_output']['revision_id'] = revision
        decision['background_delivery']['revision_id'] = revision
        status, rejected = self.http.request('POST', PREFIX + '/worker-commits', forged, token=WORKER_TOKEN)
        self.assertEqual(status, 409, rejected)
        self.assertEqual(rejected['error']['code'], 'invalid_room_output', rejected)
        status, after = self.http.request('GET', PREFIX + route, token=WORKER_TOKEN)
        self.assertEqual(status, 200, after)
        self.assertEqual(after, before)

    def test_invented_answered_history_without_original_reply_is_refused_atomically(self):
        self.publish()
        proposal = self.tick()['pending'][0]
        self.now += 1
        route = '/worker-commits/' + WORKER_ID + '?site_id=' + SCOPE['site_id'] + '&room_id=' + SCOPE['room_id']
        status, before = self.http.request('GET', PREFIX + route, token=WORKER_TOKEN)
        self.assertEqual(status, 200, before)
        forged = self.tick(prepare=True)
        forged['checkpoint']['state']['pending'] = []
        forged['checkpoint']['state']['clarification_history'] = [dict(
            proposal=proposal, outcome='answered', reason='activity_declaration_recorded', settled_at=self.now,
            reply_ref=proposal['evidence_refs'][0], answer='using_material')]
        forged['publications'][-1]['payload']['pending'] = []
        status, rejected = self.http.request('POST', PREFIX + '/worker-commits', forged, token=WORKER_TOKEN)
        self.assertEqual(status, 409, rejected)
        self.assertEqual(rejected['error']['code'], 'invalid_clarification', rejected)
        status, after = self.http.request('GET', PREFIX + route, token=WORKER_TOKEN)
        self.assertEqual(status, 200, after)
        self.assertEqual(after, before)

    def test_question_survives_expired_work_ancestry_then_expires_at_original_deadline(self):
        self.publish()
        original = self.tick()['pending'][0]
        # New semantics arrive through the producer route after the original
        # working geometry expires. The question keeps its original ancestry.
        self.seq, self.now = 5, NOW + 4.2
        self.publish(geometry=False)
        retained = self.tick()
        self.assertEqual(retained['pending'], [original])
        self.assertIsNone(retained['room_output']['task']['fixture_id'])
        self.seq, self.now = 31, original['deadline']
        self.publish(geometry=False)
        expired = self.tick()
        self.assertEqual(expired['pending'], [])
        status, state = self.http.request('GET', PREFIX + '/worker-commits/' + WORKER_ID
            + '?site_id=' + SCOPE['site_id'] + '&room_id=' + SCOPE['room_id'], token=WORKER_TOKEN)
        self.assertEqual(status, 200, state)
        history = state['checkpoint']['state']['clarification_history']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['proposal'], original)
        self.assertEqual(history[0]['outcome'], 'expired')
        self.assertEqual(history[0]['reason'], 'proposal_deadline_expired')

    def test_unsure_is_nonterminal_and_first_resolving_reply_wins_with_exact_retry(self):
        self.publish()
        proposal = self.tick()['pending'][0]
        self.now += 1
        unsure = self.reply(proposal, 'unsure', 'unsure')
        status, accepted = self.http.request('POST', PREFIX + '/intent-events', unsure, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, accepted)
        acknowledged = self.tick()
        self.assertEqual(acknowledged['pending'], [proposal])
        self.assertEqual(acknowledged['intent_results'][0]['reason'], 'clarification_unsure_acknowledged')
        self.now += 1
        first = self.reply(proposal, suffix='first-resolving')
        second = self.reply(proposal, 'not_using_material', 'second-resolving')
        receipts = []
        for reply in (first, second):
            status, receipt = self.http.request('POST', PREFIX + '/intent-events', reply, token=HUMAN_TOKEN)
            self.assertEqual(status, 200, receipt)
            receipts.append(receipt)
        settled = self.tick()
        self.assertEqual(settled['pending'], [])
        self.assertEqual([(r['status'], r['reason']) for r in settled['intent_results']],
                         [('applied', 'activity_declaration_recorded'), ('rejected', 'clarification_already_resolved')])
        status, retried = self.http.request('POST', PREFIX + '/intent-events', first, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, retried)
        self.assertEqual(retried, receipts[0])
        changed = deepcopy(first)
        changed['value']['answer'] = 'not_using_material'
        status, conflict = self.http.request('POST', PREFIX + '/intent-events', changed, token=HUMAN_TOKEN)
        self.assertEqual(status, 409, conflict)
        self.assertEqual(conflict['error']['code'], 'identity_conflict')

    def test_wrong_nullable_scope_settles_rejected_without_resolving_question(self):
        self.publish()
        proposal = self.tick()['pending'][0]
        self.now += 1
        wrong = self.reply(proposal)
        wrong['target']['scene_id'] = 'authored-other-scene'
        status, accepted = self.http.request('POST', PREFIX + '/intent-events', wrong, token=HUMAN_TOKEN)
        self.assertEqual(status, 200, accepted)
        rejected = self.tick()
        self.assertEqual(rejected['pending'], [proposal])
        self.assertEqual(rejected['intent_results'][0]['status'], 'rejected')
        self.assertEqual(rejected['intent_results'][0]['reason'], 'clarification_scope_mismatch')
        self.assertIsNone(rejected['room_output']['task']['fixture_id'])

    def test_missing_protection_retains_question_but_negative_use_cancels_and_rejects_late_reply(self):
        self.publish()
        proposal = self.tick()['pending'][0]
        unprotected = self.advance(missing_protection=True)
        self.assertEqual(unprotected['pending'], [proposal])
        self.assertIsNone(unprotected['room_output']['task']['fixture_id'])
        cancelled = self.advance(interaction='not_interacting')
        self.assertEqual(cancelled['pending'], [])
        self.now += 1
        status, accepted = self.http.request('POST', PREFIX + '/intent-events', self.reply(proposal), token=HUMAN_TOKEN)
        self.assertEqual(status, 200, accepted)
        late = self.tick()
        self.assertEqual(late['pending'], [])
        self.assertEqual(late['intent_results'][0]['status'], 'rejected')
        self.assertEqual(late['intent_results'][0]['reason'], 'clarification_not_pending')

    def test_source_barrier_cancels_authority_and_late_reply_has_no_effect(self):
        self.publish()
        proposal = self.tick()['pending'][0]
        self.now = NOW + 2
        status, stopped = self.http.request('POST', PREFIX + '/events', event(barrier(
            list(self.heads.values()), event_id='clarification-source-stop', at=self.now)))
        self.assertEqual(status, 200, stopped)
        cancelled = self.tick()
        self.assertEqual(cancelled['pending'], [])
        status, checkpoint = self.http.request('GET', PREFIX + '/worker-commits/' + WORKER_ID
            + '?site_id=' + SCOPE['site_id'] + '&room_id=' + SCOPE['room_id'], token=WORKER_TOKEN)
        self.assertEqual(status, 200, checkpoint)
        history = checkpoint['checkpoint']['state']['clarification_history']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['outcome'], 'cancelled')
        # A public source barrier also advances room authority. Authority
        # invalidation has the earlier frozen cancellation precedence.
        self.assertEqual(history[0]['reason'], 'authority_changed')
        self.now += 1
        status, accepted = self.http.request('POST', PREFIX + '/intent-events', self.reply(proposal), token=HUMAN_TOKEN)
        self.assertEqual(status, 200, accepted)
        late = self.tick()
        self.assertEqual(late['intent_results'][0]['reason'], 'clarification_not_pending')
        self.assertIsNone(late['room_output']['task']['fixture_id'])


if __name__ == '__main__':
    unittest.main()
