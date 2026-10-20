"""Independent config10 room clarification authority; never observation or scene truth."""
from __future__ import annotations

import copy
import json

from journal import bounded_json, canonical, digest, finite, identifier
from live_ingress_validation import SCOPE_KEYS, _person
from room_worker import RoomLiveDeliveryWorkerBoundary
from room_delivery import validate_room_worker_policy
from live_scene import validate_scene_worker_grant
from live_worker import reject


INTENT = 'home-live-intent-event-3'
CLARIFICATION_SCHEMAS = frozenset({'home-live-shadow-decision-9', 'home-live-worker-checkpoint-9',
    'home-live-worker-commit-9', 'home-live-input-cut-9', 'home-live-room-output-1', 'home-live-room-snapshot-1', INTENT,
    'home-live-intent-event-2', 'home-live-person-lifecycle-1'})
CLARIFICATION_OUTPUTS = frozenset({'home-live-engagement-state-1', 'home-live-shadow-decision-9'})


def validate_clarification_policy(policy, methods):
    keys = {'schema', 'version', 'proposal_ttl_s', 'max_pending', 'max_history',
            'repeat_policy', 'material_methods', 'interaction_methods'}
    if (not isinstance(policy, dict) or set(policy) != keys
            or policy['schema'] != 'home-live-clarification-policy-1'
            or type(policy['version']) is not int or policy['version'] != 1
            or type(policy['proposal_ttl_s']) not in {int, float} or policy['proposal_ttl_s'] != 30
            or type(policy['max_pending']) is not int or policy['max_pending'] != 1
            or type(policy['max_history']) is not int or policy['max_history'] != 128
            or policy['repeat_policy'] != 'one_per_scope'):
        raise ValueError('closed bounded clarification policy required')
    for key in ('material_methods', 'interaction_methods'):
        pins = policy[key]
        if (not isinstance(pins, list) or not 1 <= len(pins) <= 16
                or len({canonical(pin) for pin in pins}) != len(pins)
                or any(not isinstance(pin, dict) or set(pin) != {'id', 'digest'}
                       or pin not in methods for pin in pins)):
            raise ValueError('clarification methods must resolve to exact commissioned pins')
    bounded_json(policy, 16384)


def validate_room_clarification_worker_policy(manifest, registry):
    worker = manifest['worker']
    if (not getattr(registry, 'room_clarification_interface_digest', None)
            or manifest.get('room_clarification_interface_digest') != registry.room_clarification_interface_digest
            or worker.get('room_clarification_schema_pins') != registry.room_clarification_schema_pins):
        raise ValueError('exact clarification interface and schema pins required')
    methods = [method for source in manifest['sources'].values() for method in source['method_refs']]
    validate_clarification_policy(worker['clarification_policy'], methods)
    prior = copy.deepcopy(manifest)
    del prior['room_clarification_interface_digest']
    del prior['worker']['clarification_policy']
    del prior['worker']['room_clarification_schema_pins']
    validate_room_worker_policy(prior, registry)


def validate_room_clarification_grant(grant, config):
    private = CLARIFICATION_SCHEMAS - CLARIFICATION_OUTPUTS - {INTENT, 'home-live-intent-event-2', 'home-live-person-lifecycle-1'}
    if grant.payload_types & private and 'worker_commit' not in grant.operations:
        raise ValueError('private clarification state requires the commissioned worker capability')
    if 'home-live-shadow-decision-9' in grant.payload_types and 'home-live-geometry-1' not in grant.payload_types:
        raise ValueError('complete clarification decision requires separate geometry authority')
    if 'intent' in grant.operations and INTENT in grant.payload_types:
        if (grant.operations != frozenset({'read', 'intent'}) or grant.expected_actor_ref is None
                or '3.0' not in grant.revisions or grant.expected_worker_id is not None):
            raise ValueError('clarification replies require independent attributed read/intent authority')


def validate_room_clarification_worker_grant(grant, config):
    validate_scene_worker_grant(grant, config)
    if not CLARIFICATION_SCHEMAS <= grant.payload_types:
        raise ValueError('clarification worker requires complete explicit contract closure')


def _scope_key(proposal):
    return canonical([proposal[k] for k in ('person_ref', 'subject_ref', 'episode_id', 'scene_id', 'distinction')])


def _publication_ref(publication):
    return {k: publication[k] for k in ('stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest')} | {'component_id': None}


def clarification_transition(*, identity, manifest_ref, policy, lifecycle_policy, t,
                             authority_generation, source_generations, publications,
                             input_receipts, engagements, scene, old=None, sources=None):
    """Recompute the closed transition from prior trusted state and causal originals.

    This is an independent admission implementation. It neither invokes the JS
    worker nor accepts its state as evidence for its own correctness.
    """
    prior = old['state'] if old else {}
    pending = copy.deepcopy(prior.get('pending', []))
    history = copy.deepcopy(prior.get('clarification_history', []))
    previous_sources = prior.get('engagement', {}).get('source_generations', {})
    receipts = {r['cursor']: r for r in input_receipts}
    originals = list(publications.values()) if isinstance(publications, dict) else list(publications)
    rows = []
    for p in originals:
        if p['payload_type'] not in {'home-live-fact-1', 'home-live-geometry-1', 'home-live-person-lifecycle-1'}:
            continue
        f = copy.deepcopy(p['payload'])
        if finite(f.get('available_at')):
            f['available_at'] = max(f['available_at'], p['committed_at'], receipts.get(p['cursor'], {}).get('received_at', float('inf')))
        if finite(f.get('valid_until')) and finite(f.get('observed_at')):
            max_age = (sources or {}).get(f.get('source_id'), {}).get('max_age_s', float('inf'))
            f['valid_until'] = min(f['valid_until'], f['observed_at'] + max_age) - f.get('clock', {}).get('uncertainty_s', 0)
        rows.append(f)

    def scoped(f):
        return isinstance(f, dict) and all(f.get(k) == identity[k] for k in SCOPE_KEYS)

    def fresh(f):
        return (scoped(f) and all(finite(f.get(k)) for k in ('observed_at', 'available_at', 'valid_until'))
                and f['observed_at'] <= f['available_at'] <= t < f['valid_until']
                and f.get('clock', {}).get('status') == 'commissioned'
                and f.get('source_id') in source_generations
                and f.get('source_generation') == source_generations[f['source_id']])

    def person(p):
        return (scoped(p) and set(p) == set(SCOPE_KEYS) | {'track_epoch', 'pid'}
                and isinstance(p.get('track_epoch'), str) and type(p.get('pid')) is int and p['pid'] >= 1)

    def qualified(f):
        a = f.get('association', {})
        return (person(f.get('person_ref')) and isinstance(f.get('subject_ref'), dict)
                and set(f['subject_ref']) == {'subject_id', 'subject_epoch'}
                and a.get('person_status') == a.get('subject_status') == 'qualified'
                and not a.get('candidate_person_refs') and not a.get('candidate_subject_refs'))

    facts = [f for f in rows if f.get('schema') == 'home-live-fact-1' and fresh(f) and qualified(f)]
    geometries = [f for f in rows if f.get('schema') == 'home-live-geometry-1']
    lifecycle = [f for f in rows if f.get('schema') == 'home-live-person-lifecycle-1']
    spatial = [f for f in rows if f.get('schema') in {'home-live-fact-1', 'home-live-geometry-1'}]
    materials = [f for f in facts if f.get('status') == 'supplied' and f.get('kind') == 'material_state'
                 and f.get('value') in {'open', 'visible_state_unknown'} and f.get('method_ref') in policy['material_methods']]
    interactions = [f for f in facts if f.get('method_ref') in policy['interaction_methods']
                    and (f.get('kind') == 'hand_material_relation' and f.get('status') == 'supplied' and f.get('value') == 'near'
                         or f.get('kind') == 'object_interaction' and f.get('status') == 'unobservable'
                         and f.get('value') is None and f.get('reason') == 'unsupported_distinction')]
    pair_map = {}
    for f in materials:
        if any(i['person_ref'] == f['person_ref'] and i['subject_ref'] == f['subject_ref'] for i in interactions):
            pair_map[canonical([f['person_ref'], f['subject_ref']])] = f
    pairs = list(pair_map.values())
    people = {canonical(f['person_ref']): f['person_ref'] for f in spatial
              if fresh(f) and person(f.get('person_ref')) and f.get('association', {}).get('person_status') == 'qualified'}

    def own(f, p):
        return f.get('person_ref') == p['person_ref'] and f.get('subject_ref') == p['subject_ref']

    def lifecycle_qualified(l):
        key = ('negative_methods' if l['disposition'] == 'use_ended_qualified' else
               'absence_methods' if l['disposition'] in {'departure_qualified', 'absence_qualified'} else None)
        keys = ('method_ref', 'qualification_ref', 'applicability_ref') + (('coverage_assessment_ref',) if key == 'absence_methods' else ())
        return key and any(all(m.get(k) == l.get(k) for k in keys) for m in (lifecycle_policy or {}).get(key, []))

    for question in list(pending):
        matching_lifecycle = [l for l in lifecycle if fresh(l) and l.get('target_person_ref') == question['person_ref']
                              and (l.get('target_subject_ref') is None or l['target_subject_ref'] == question['subject_ref'])]
        ambiguous = any(fresh(f) and (f.get('association', {}).get('person_status') == 'unresolved'
                                      or f.get('association', {}).get('subject_status') == 'unresolved')
                        and (f.get('person_ref') == question['person_ref']
                             or question['person_ref'] in f.get('association', {}).get('candidate_person_refs', [])
                             or question['subject_ref'] in f.get('association', {}).get('candidate_subject_refs', [])) for f in spatial)
        ambiguous |= any(fresh(l) and l['disposition'] == 'association_reset'
                         and l.get('target_person_ref') in (None, question['person_ref']) for l in lifecycle)
        changed = (any(p['pid'] == question['person_ref']['pid'] and p['track_epoch'] != question['person_ref']['track_epoch'] for p in people.values())
                   or any(f['person_ref'] == question['person_ref'] and f['subject_ref']['subject_id'] == question['subject_ref']['subject_id']
                          and f['subject_ref']['subject_epoch'] != question['subject_ref']['subject_epoch'] for f in facts)
                   or any(scoped(e) and e.get('person_ref') == question['person_ref'] and e.get('episode_id') is not None
                          and e['episode_id'] != question['episode_id'] for e in engagements)
                   or scene.get('status') == 'selected' and scene.get('person_ref') == question['person_ref'] and scene['scene_id'] != question['scene_id'])
        resolved = (any(own(f, question) and f.get('method_ref') in policy['interaction_methods'] and f.get('status') == 'supplied'
                        and (f.get('kind') == 'object_interaction' and type(f.get('value')) is bool
                             or f.get('kind') == 'hand_material_relation' and f.get('value') in {'supported_holding', 'contact', 'manipulation', 'not_interacting'}) for f in facts)
                    or any(scoped(e) and own(e, question) and e.get('state') in {'candidate', 'supported'} for e in engagements))
        reason = ('proposal_deadline_expired' if t >= question['deadline'] else
                  'authority_changed' if old and old['authority_generation'] != authority_generation else
                  'source_generation_changed' if old and previous_sources != source_generations else
                  'association_unavailable' if ambiguous or len(people) > 1 or len(pairs) > 1 else
                  'departure_qualified' if any(l['disposition'] in {'departure_qualified', 'absence_qualified'} and lifecycle_qualified(l) for l in matching_lifecycle) else
                  'use_ended_qualified' if any(l['disposition'] == 'use_ended_qualified' and lifecycle_qualified(l) for l in matching_lifecycle) else
                  'scope_changed' if changed else 'ordinary_resolution' if resolved else
                  'semantic_evidence_unavailable' if not any(own(f, question) for f in pairs) else None)
        if reason:
            history.append(dict(proposal=question, outcome='expired' if reason == 'proposal_deadline_expired' else 'cancelled',
                                reason=reason, settled_at=t, reply_ref=None, answer=None))
            pending.remove(question)

    if not pending and len(history) < policy['max_history'] and len(pairs) == len(people) == 1:
        candidate = pairs[0]
        work = [g for g in geometries if fresh(g) and qualified(g) and own(g, candidate) and g.get('status') == 'supplied'
                and g.get('parts', {}).get('work', {}).get('status') == 'supplied'
                and finite(g['parts']['work'].get('observed_at')) and g['parts']['work']['observed_at'] <= t
                and finite(g['parts']['work'].get('valid_until')) and t < g['parts']['work']['valid_until']]
        negative = any(own(f, candidate) and f.get('method_ref') in policy['interaction_methods'] and f.get('status') == 'supplied'
                       and (f.get('kind') == 'object_interaction' and f.get('value') is False
                            or f.get('kind') == 'hand_material_relation' and f.get('value') == 'not_interacting') for f in facts)
        engaged = any(scoped(e) and e.get('person_ref') == candidate['person_ref']
                      and (e.get('state') in {'candidate', 'supported'} or e.get('episode_id') is not None) for e in engagements)
        selected = scene.get('status') == 'selected' and scene.get('person_ref') == candidate['person_ref']
        ambiguous = any(fresh(f) and (f.get('association', {}).get('person_status') == 'unresolved'
                                      or f.get('association', {}).get('subject_status') == 'unresolved') for f in spatial)
        scope = dict(person_ref=candidate['person_ref'], subject_ref=candidate['subject_ref'], episode_id=None, scene_id=None, distinction='material_use')
        if work and not negative and not engaged and not selected and not ambiguous and not any(_scope_key(h['proposal']) == _scope_key(scope) for h in history):
            ids = {f['id'] for f in materials + interactions if own(f, candidate)} | {g['id'] for g in work}
            evidence = [_publication_ref(p) for p in sorted(originals, key=lambda p: p['cursor']) if p['id'] in ids]
            if len(evidence) != len(ids) or len(evidence) < 3:
                reject('invalid_clarification', 'question requires bounded exact original trigger ancestry')
            body = {**scope, 'evidence_refs': evidence, 'reason': 'material_use_unresolved',
                    'created_at': t, 'deadline': t + policy['proposal_ttl_s'], 'permitted_reply': 'activity_reply'}
            if len(evidence) <= 32:
                pending = [{'proposal_id': 'clarification:' + digest([identity, manifest_ref, authority_generation, source_generations, body]), **body}]

    results = []
    for p in sorted((p for p in originals if p['payload_type'] == INTENT), key=lambda p: p['cursor']):
        i = p['payload']
        available = max(i['available_at'], p['committed_at'], receipts.get(p['cursor'], {}).get('received_at', float('inf')))
        if available > t or i['set_at'] > t:
            continue
        question = next((q for q in pending if q['proposal_id'] == i['reply_to']), None)
        terminal = next((h for h in history if h['proposal']['proposal_id'] == i['reply_to']), None)
        target, answer = i['target'], i['value']['answer']
        reason = ('clarification_reply_expired' if t >= i['valid_until'] or terminal and terminal['outcome'] == 'expired' else
                  'clarification_already_resolved' if terminal and terminal['outcome'] == 'answered' else
                  'clarification_not_pending' if not question or question['created_at'] >= t else
                  'clarification_reply_expired' if t >= question['deadline'] else
                  'clarification_scope_mismatch' if i['set_at'] < question['created_at'] or target['pending_ref'] != i['reply_to']
                  or any(target[k] != question[k] for k in ('person_ref', 'subject_ref', 'episode_id', 'scene_id')) else None)
        if reason is None and answer != 'unsure':
            history.append(dict(proposal=question, outcome='answered', reason='activity_declaration_recorded',
                                settled_at=t, reply_ref=_publication_ref(p), answer=answer))
            pending.remove(question)
        results.append(dict(id=i['id'], status='rejected' if reason else 'applied',
                            reason=reason or ('clarification_unsure_acknowledged' if answer == 'unsure' else 'activity_declaration_recorded')))
    return dict(pending=pending, clarification_history=history, intent_results=results,
                clarification_status=dict(status='available' if len(history) < policy['max_history'] else 'unavailable',
                                          reason='ready' if len(history) < policy['max_history'] else 'history_budget_exhausted'))


class RoomClarificationLiveDeliveryWorkerBoundary(RoomLiveDeliveryWorkerBoundary):
    """Config10 independently admits declarations while retaining every enclosing room check."""
    schema_types = RoomLiveDeliveryWorkerBoundary.schema_types | CLARIFICATION_SCHEMAS
    output_types = CLARIFICATION_OUTPUTS
    input_types = RoomLiveDeliveryWorkerBoundary.input_types | {INTENT}
    intent_types = RoomLiveDeliveryWorkerBoundary.intent_types | {INTENT}
    decision_type = 'home-live-shadow-decision-9'
    checkpoint_type = 'home-live-worker-checkpoint-9'
    commit_type = 'home-live-worker-commit-9'
    cut_type = 'home-live-input-cut-9'
    version = 9
    worker_version = 'home-live-context-worker-9.0.0'
    module_names = RoomLiveDeliveryWorkerBoundary.module_names | {'app/src/home-live-clarification-state.js'}

    def configuration_description(self, nested):
        result = super().configuration_description(nested)
        result.update(clarification_policy=self.policy['clarification_policy'],
                      room_clarification_schema_pins=self.policy['room_clarification_schema_pins'],
                      room_clarification_interface_digest=self.j.schemas.room_clarification_interface_digest)
        return result

    def _checkpoint(self, cp, commit, next_seq, old, now):
        if cp['room_clarification_schema_pins'] != self.policy['room_clarification_schema_pins']:
            reject('checkpoint_conflict', 'clarification pins must match commissioned configuration')
        return super()._checkpoint(cp, commit, next_seq, old, now)

    def intent(self, payload, grant):
        if not isinstance(payload, dict) or payload.get('schema') != INTENT:
            return super().intent(payload, grant)
        scope = self.ingress.identity
        grant.require('intent', payload.get('site_id'), payload.get('room_id'), payload_type=INTENT, revision='3.0')
        if (grant.expected_actor_ref is None or payload.get('actor_ref') != grant.expected_actor_ref
                or payload.get('manifest_ref') != self.ingress.config['manifest_ref']
                or any(payload.get(k) != scope[k] for k in SCOPE_KEYS)):
            reject('forbidden', 'reply requires exact authenticated actor and current live scope', 403)
        self.j.schemas.validate(payload)
        _person(payload['target']['person_ref'], scope)
        if payload['target']['pending_ref'] != payload['reply_to']:
            reject('invalid_activity_reply', 'pending reference and reply origin must be identical', 400)

        def accept():
            prior = self.j.db.execute('SELECT event FROM live_scene_intents WHERE producer_id=? AND event_id=?',
                                     (grant.producer_id, payload['id'])).fetchone()
            if prior is not None:
                saved = json.loads(prior['event'])
                if saved['payload'] != payload:
                    reject('identity_conflict', 'intent retry changed its immutable payload')
                return self.j._receipt(saved)
            if self.j.db.execute('SELECT 1 FROM live_scene_intents WHERE event_id=?', (payload['id'],)).fetchone():
                reject('intent_id_conflict', 'intent id is reserved in this room session')
            if not 0 <= payload['set_at'] <= payload['available_at'] <= self.j.clock() < payload['valid_until'] or payload['valid_until']-payload['set_at'] > 86400:
                reject('invalid_clock', 'reply needs causal availability and bounded exclusive validity', 400)
            self._check_intent_capacity()
            event = self.j._command_event(payload, grant)
            if self.j._revoked(scope['site_id'], scope['room_id'], grant.producer_id, '3.0'):
                reject('revoked', 'intent origin or room authority has been revoked')
            receipt, publication = self.ingress._record(event)
            self.j.db.execute('INSERT INTO live_scene_intents VALUES(?,?,?,?,?,?,NULL,NULL)',
                             (receipt['cursor'], grant.producer_id, payload['id'], payload['actor_ref'], canonical(event), canonical(publication)))
            return receipt
        return self.j._transaction(accept)

    def additional_inputs(self, grant):
        for kind in self.intent_types:
            revision = '3.0' if kind == INTENT else '2.0' if kind == 'home-live-intent-event-2' else '1.0'
            grant.require('read', self.ingress.identity['site_id'], self.ingress.identity['room_id'], payload_type=kind, revision=revision)
        return [json.loads(row[0]) for row in self.j.db.execute('SELECT publication FROM live_scene_intents WHERE application_result IS NULL ORDER BY cursor')]

    def settle(self, commit, grant, publications, decision, engagements, old):
        cp, t = commit['checkpoint'], decision['t']
        expected = clarification_transition(identity=self.ingress.identity,
            manifest_ref=self.ingress.config['manifest_ref'], policy=self.policy['clarification_policy'],
            lifecycle_policy=self.policy['lifecycle_policy'], t=t,
            authority_generation=cp['authority_generation'],
            source_generations=cp['state']['engagement']['source_generations'],
            publications=publications, input_receipts=commit['input_receipts'], engagements=engagements,
            scene=decision['scene'], old=old, sources=self.ingress.sources)
        for key in ('pending', 'clarification_history'):
            if cp['state'][key] != expected[key]:
                reject('invalid_clarification', 'clarification state must preserve the independently recomputed causal transition')
        if decision['pending'] != expected['pending'] or decision['clarification_status'] != expected['clarification_status']:
            reject('invalid_clarification', 'public question state must exactly match trusted checkpoint state and budget')
        reply_ids = {p['id'] for p in publications.values() if p['payload_type'] == INTENT}
        expected_results = [{**r, 'decision_ref': decision['id']} for r in expected['intent_results']]
        if [r for r in decision['intent_results'] if r['id'] in reply_ids] != expected_results:
            reject('invalid_application_result', 'every reply settlement needs its original proposal and independently verified effect')
        if cp['state']['intent_settlements'] != decision['intent_results']:
            reject('invalid_application_result', 'checkpoint and decision reply settlements must agree exactly')

        # Run all incumbent independent scene/correction/coverage/hazard checks.
        # Only the new declaration branch is removed from that unchanged input.
        prior_commit, prior_decision = copy.deepcopy(commit), copy.deepcopy(decision)
        prior_decision['intent_results'] = [r for r in decision['intent_results'] if r['id'] not in reply_ids]
        prior_commit['checkpoint']['state']['intent_settlements'] = copy.deepcopy(prior_decision['intent_results'])
        super().settle(prior_commit, grant, {c: p for c, p in publications.items() if p['payload_type'] != INTENT},
                       prior_decision, engagements, old)
        for result in expected_results:
            matches = [p for p in publications.values() if p['payload_type'] == INTENT and p['id'] == result['id']]
            if len(matches) != 1:
                reject('invalid_application_result', 'reply settlement needs one original delivered declaration')
            p = matches[0]
            row = self.j.db.execute('SELECT * FROM live_scene_intents WHERE cursor=?', (p['cursor'],)).fetchone()
            if row is None or json.loads(row['publication']) != p:
                reject('invalid_application_result', 'reply must preserve its original durable publication')
            terminal = {'schema': 'home-intent-application-result-1', 'version': 1, **result}
            if row['application_result'] is not None and json.loads(row['application_result']) != terminal:
                reject('application_result_conflict', 'terminal declaration result and original decision are immutable')
            self.j.db.execute('UPDATE live_scene_intents SET application_result=?,settled_at=? WHERE cursor=?',
                              (canonical(terminal), t, p['cursor']))
