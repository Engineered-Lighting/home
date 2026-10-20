"""Explicit person refinement bridge; no identity is inferred from viewing refs.

Only a server-provisioned fixed original resolver can supply a retained witness.
Qualified refs are copied from the actual current Journal work publication.
The incumbent semantic/PC paths and sole decision worker remain unchanged.
"""
from copy import deepcopy
import re

from journal import Journal, digest, finite
from lab_live_semantic_admission import LabLiveSemanticAdmission, SemanticRefusal, _original_digest
from reasoning import ACTIVITIES, validate_jev_response, digest as lab_digest
import qwen_person_scene as person_scene
import jev_person_wire as person_wire
from jev_person_source import freeze_person_context

SCHEMA = 'home-lab-person-semantic-admission-policy-1'
CONTEXT = 'home-lab-person-jev-input-1'
OUTPUT = 'home-lab-person-jev-output-1'
HEX = re.compile(r'[a-f0-9]{64}\Z')
BRIDGE_KEYS = {'source_id','method_ref','qualification_ref','receipt_store_id','receipt_component_id','max_bindings'}
CONTEXT_KEYS = {'schema','version','semantics_event_id','semantics_payload_digest','visual_event_id','visual_payload_digest','packet','slots'}


def _need(condition, reason):
    if not condition:
        raise SemanticRefusal(reason)


def _text(value, limit=256):
    return isinstance(value, str) and 0 < len(value) <= limit


def _pin(value):
    return isinstance(value, dict) and set(value) == {'id','digest'} and _text(value['id']) and isinstance(value['digest'], str) and HEX.fullmatch(value['digest'])


def is_person_policy(policy):
    return isinstance(policy, dict) and policy.get('schema') == SCHEMA


def base_semantic_policy(policy):
    return deepcopy(policy['base_policy'] if is_person_policy(policy) else policy)


class TrustedPersonBridge:
    """Server constructor capability, never a request/provider/browser port.

    resolve_original is the fixed retained-original resolver(store, receipt).
    The lookup ID is computed locally; no executable/path or qualified person
    supplied by an inference caller is evaluated or selected.
    """
    def __init__(self, scope, bridge_policy, resolve_original):
        _need(isinstance(scope, dict) and set(scope) == {'site_id','room_id','session_id','domain_epoch'}
              and all(_text(v) for v in scope.values()) and callable(resolve_original), 'fixed_person_bridge_capability_required')
        _need(isinstance(bridge_policy, dict) and set(bridge_policy) == BRIDGE_KEYS
              and all(_text(bridge_policy[k]) for k in ('source_id','receipt_store_id','receipt_component_id'))
              and all(_pin(bridge_policy[k]) for k in ('method_ref','qualification_ref'))
              and type(bridge_policy['max_bindings']) is int and bridge_policy['max_bindings'] == 1,
              'closed_person_bridge_policy_required')
        self._scope, self._policy, self._resolver = deepcopy(scope), deepcopy(bridge_policy), resolve_original

    def binding(self):
        return dict(scope=deepcopy(self._scope), bridge_policy_digest=digest(self._policy))

    def resolve(self, query):
        receipt = 'person-work-bridge:' + digest(query)
        try:
            return deepcopy(self._resolver(self._policy['receipt_store_id'], receipt))
        except KeyError:
            return None


class LabPersonSemanticAdmission(LabLiveSemanticAdmission):
    def __init__(self, session, *, live_config, revision, policy, original_retainer, person_bridge=None):
        _need(is_person_policy(policy) and set(policy) == {'schema','version','base_policy','bridge'}
              and type(policy['version']) is int and policy['version'] == 1,
              'explicit_person_semantic_policy_required')
        super().__init__(session, live_config=live_config, revision=revision,
                         policy=base_semantic_policy(policy), original_retainer=original_retainer)
        _need(self.persisted_roots, 'qualified_person_requires_persisted_work_policy')
        bridge = policy['bridge']
        # Validate the capability body even when no commissioned resolver is installed.
        TrustedPersonBridge(self.scope, bridge, lambda *_:None)
        source = self.config['manifest']['sources'].get(bridge['source_id'])
        _need(source is not None and bridge['source_id'] == self.pc_binding['geometry_source_id']
              and bridge['method_ref'] == self.pc_binding['association_method_ref']
              and bridge['method_ref'] in source['association_method_refs'], 'commissioned_person_bridge_source_required')
        self.person_policy = deepcopy(policy)
        self._base_binding_digest = self.binding_digest
        self.binding_digest = digest([self._base_binding_digest, self.person_policy])
        self.person_bridge = person_bridge
        self._require_bridge()
        self.person_requests, self._person_effect = {}, None

    def _require_bridge(self):
        _need(self.person_bridge is None or type(self.person_bridge) is TrustedPersonBridge
              and self.person_bridge.binding() == dict(scope=self.scope, bridge_policy_digest=digest(self.person_policy['bridge'])),
              'fixed_person_bridge_capability_required')

    def _check(self, journal, received_at):
        _need(isinstance(journal, Journal), 'actual_journal_authority_required')
        if self.authority is None:
            self.authority = journal
        _need(self.authority is journal and journal.live_config == self.config, 'semantic_journal_binding_changed')
        _need(digest([self.config,self.session,self.policy,self.revision]) == self._base_binding_digest
              and digest([self._base_binding_digest,self.person_policy]) == self.binding_digest,
              'semantic_configuration_changed')
        self._require_bridge()
        now = journal.clock()
        _need(finite(now) and finite(received_at) and 0 <= received_at <= now, 'actual_consumer_receipt_time_required')
        return now

    def bridge_query(self, context, subject):
        return dict(scope=deepcopy(self.scope), basis=deepcopy(context['packet']['basis']), **deepcopy(subject),
            **{k:context[k] for k in ('visual_event_id','visual_payload_digest','semantics_event_id','semantics_payload_digest')})

    def _context(self, context, indexed, at):
        _need(finite(at), 'person_original_lease_invalid')
        _need(isinstance(context, dict) and set(context) == CONTEXT_KEYS and context['schema'] == CONTEXT
              and type(context['version']) is int and context['version'] == 1,
              'exact_person_context_required')
        for family in ('visual','semantics'):
            identity, pin = context[family+'_event_id'], context[family+'_payload_digest']
            _need(type(identity) is int and identity in indexed and isinstance(pin,str) and HEX.fullmatch(pin)
                  and lab_digest(indexed[identity]['payload']) == pin, 'person_original_payload_mismatch')
        visual, semantic = indexed[context['visual_event_id']], indexed[context['semantics_event_id']]
        _need(freeze_person_context([visual,semantic],site_id=self.session['site_id'],session_id=self.session['id'],
              arm=self.policy['arm'],generation=self.policy['generation'],cutoff=at) == context, 'person_context_originals_mismatch')
        p, s, packet = visual['payload'], semantic['payload'], context['packet']
        _need(visual['kind'] == 'perception.output' and semantic['kind'] == 'qwen.person_observation'
              and s.get('packet') == packet and s.get('source_event_id') == visual['id']
              and s.get('source_payload_digest') == context['visual_payload_digest'], 'person_semantic_original_mismatch')
        _need(isinstance(packet,dict) and packet.get('schema') == 'home-lab-view-person-semantics-1'
              and type(packet.get('version')) is int and packet['version'] == 1 and packet.get('viewing_only') is True
              and all(packet.get(k) is False for k in ('identity_authority','geometry_authority','engagement_authority','physical_permission')),
              'person_semantic_original_mismatch')
        association = p.get('arm_snapshot',{}).get('persons2d',{}).get('association',{})
        basis = packet.get('basis',{})
        _need(association.get('schema') == 'home-lab-view-person-association-1' and type(association.get('version')) is int and association['version'] == 1
              and association.get('status') == 'available' and association.get('reason') is None
              and association.get('viewing_only') is True and all(association.get(k) is False for k in ('identity_authority','geometry_authority','physical_permission'))
              and isinstance(basis,dict) and set(basis) == {'scope','source','track_epoch'}
              and basis == {k:association.get(k) for k in ('scope','source','track_epoch')}, 'person_association_basis_mismatch')
        scope, source = basis.get('scope',{}), basis.get('source',{})
        expected = dict(site_id=self.session['site_id'],camera_id=self.session['camera_id'],session_id=self.session['id'],
                        session_generation=self.session.get('generation',0),arm=self.policy['arm'],generation=self.policy['generation'])
        _need(set(scope) == set(expected)|{'capture_epoch','engine_epoch','artifact_sha256','model_version'}
              and all(scope.get(k) == v for k,v in expected.items()) and _text(basis.get('track_epoch'))
              and all(scope.get(k) == p.get(k) for k in ('capture_epoch','engine_epoch','artifact_sha256','model_version'))
              and p.get('status') == 'completed' and p.get('current') is True, 'person_association_scope_mismatch')
        _need(set(source) == {'frame_id','request_id','observed_at','available_at','valid_until','width','height'}
              and all(source.get(k) == p.get(k) for k in ('frame_id','request_id','observed_at','available_at'))
              and (source.get('width'),source.get('height')) == (640,360), 'person_association_source_mismatch')
        observed, available, expiry = self._lease(packet)
        _need(all(finite(source.get(k)) for k in ('observed_at','available_at','valid_until'))
              and observed == source['observed_at'] and source['observed_at'] <= source['available_at'] <= available <= at < expiry
              and expiry <= source['valid_until'] <= p.get('valid_until',-1), 'person_original_lease_invalid')
        evidence = packet.get('evidence',{})
        shown = p['arm_snapshot'].get('frame',{})
        _need(set(evidence) == {'media_id','sha256','width','height','frame_id','capture_epoch'}
              and evidence['media_id'] == shown.get('media_id') and evidence['frame_id'] == source['frame_id']
              and evidence['capture_epoch'] == scope['capture_epoch'] and (evidence['width'],evidence['height']) == (640,360)
              and isinstance(evidence['sha256'],str) and HEX.fullmatch(evidence['sha256']), 'person_visual_evidence_mismatch')
        slots, subjects, assignments = context['slots'], packet.get('subjects'), association.get('assignments')
        _need(isinstance(slots,list) and 1 <= len(slots) <= 2 and isinstance(subjects,list) and len(subjects) == len(slots)
              and isinstance(assignments,list) and len(assignments) == len(slots), 'person_subject_membership_mismatch')
        ids, refs = set(), set()
        for i,slot in enumerate(slots,1):
            _need(isinstance(slot,dict) and set(slot) == {'subject_slot','instance_id','track_ref'}
                  and slot['subject_slot'] == 'person_'+str(i) and _text(slot['instance_id']) and _text(slot['track_ref'])
                  and slot['instance_id'] not in ids and slot['track_ref'] not in refs, 'person_subject_membership_mismatch')
            ids.add(slot['instance_id']);refs.add(slot['track_ref'])
            matches=[a for a in assignments if a.get('instance_id') == slot['instance_id'] and a.get('track_ref') == slot['track_ref']
                     and a.get('status') in ('new','continued') and a.get('reason') is None]
            values=[a for a in subjects if a.get('instance_id') == slot['instance_id'] and a.get('track_ref') == slot['track_ref']]
            _need(len(matches) == len(values) == 1, 'person_subject_membership_mismatch')
            subject=values[0]
            _need(set(subject) == {'instance_id','track_ref','status','reason','facts'}, 'person_subject_membership_mismatch')
            if subject['status'] == 'available':
                facts=subject['facts']
                _need(subject['reason'] is None and isinstance(facts,dict) and set(facts) == set(person_scene.KEY_ORDER)
                      and all(facts[k] in values for k,values in person_scene.FIELDS.items())
                      and isinstance(facts['visible_basis'],list) and len(facts['visible_basis']) <= person_scene.MAX_BASIS
                      and len(set(facts['visible_basis'])) == len(facts['visible_basis']) and all(v in person_scene.VISIBLE_BASIS for v in facts['visible_basis']),
                      'invalid_person_subject_facts')
            else:
                _need(subject['status'] == 'unknown' and subject['facts'] is None and _text(subject['reason'],128), 'invalid_person_subject_facts')
        return visual

    def _bridge(self, context, subject, request_at, journal, grant, now):
        if self.person_bridge is None:
            return None
        record = self.person_bridge.resolve(self.bridge_query(context,subject))
        if record is None:
            return None
        try:
            policy=self.person_policy['bridge'];ref,original=record['ref'],record['original']
            _need(set(record) == {'ref','original'} and set(ref) == {'store_id','receipt_id','component_id','digest'}
                  and ref['store_id'] == policy['receipt_store_id'] and ref['component_id'] == policy['receipt_component_id']
                  and ref['receipt_id'] == 'person-work-bridge:'+digest(self.bridge_query(context,subject))
                  and ref['digest'] == digest(original), 'person_bridge_original_mismatch')
            _need(set(original) == {'schema','version','scope','source_id','method_ref','qualification_ref','basis','subject_slot','instance_id','track_ref',
                'visual_event_id','visual_payload_digest','semantics_event_id','semantics_payload_digest','work_parent_ref','qualified_original_refs','observed_at','available_at','valid_until'}
                and original['schema'] == 'home-lab-person-work-bridge-1' and type(original['version']) is int and original['version'] == 1
                and all(original[k] == policy[k] for k in ('source_id','method_ref','qualification_ref'))
                and all(original[k] == value for k,value in self.bridge_query(context,subject).items()), 'person_bridge_original_mismatch')
            observed,available,expiry=self._lease(original)
            _need(observed == context['packet']['observed_at'] and available <= request_at
                  and now+self.policy['jev']['clock_uncertainty_s'] < expiry <= context['packet']['valid_until'], 'person_bridge_original_expired')
            parent=self._parent(original['work_parent_ref'],journal,grant,now)
            _need(self.current_work_parent(journal,grant,now) == original['work_parent_ref'], 'person_work_parent_not_unique')
            p=parent['payload']
            _need(parent['committed_at'] <= request_at and p['available_at'] <= request_at and p['observed_at'] <= observed
                  and available >= max(p['available_at'],context['packet']['available_at']) and expiry <= min(p['valid_until'],p['parts']['work']['valid_until'])
                  and original['qualified_original_refs'] == p['original_receipt_refs'] and bool(original['qualified_original_refs'])
                  and set(self._roots_from_context(context)) <= set(p['observation_roots']), 'person_bridge_work_evidence_mismatch')
            return dict(subject=deepcopy(subject),bridge=deepcopy(record),work_parent_ref=deepcopy(original['work_parent_ref']))
        except (KeyError,TypeError,ValueError):
            return None

    @staticmethod
    def _roots_from_context(context):
        return [str(context['visual_event_id'])]

    def remember_person_request(self,event,*,events,received_at,journal,grant):
        with self.lock:
            now=self._check(journal,received_at);indexed=self._events([*events,event]);p=event['payload']
            _need(event['kind'] == 'reasoning.request' and p.get('status') == 'admitted_before_transport'
                  and p.get('arm') == self.policy['arm'] and p.get('generation') == self.policy['generation']
                  and _text(p.get('request_id')) and finite(p.get('available_at')) and finite(p.get('cutoff'))
                  and p['cutoff'] <= p['available_at'] <= received_at,
                  'original_person_request_required')
            context=p.get('person_context');self._context(context,indexed,p['cutoff'])
            rid=p['request_id'];fingerprint=_original_digest(event)
            old=self.person_requests.get(rid)
            if old is not None:
                _need(old['request_digest'] == fingerprint,'person_request_binding_changed')
                return deepcopy(old)
            _need(not self.quiesced and len(self.requests)+len(self.person_requests) < self.policy['max_events'], 'semantic_request_budget_or_quiesce')
            bindings=[v for slot in context['slots'] if (v:=self._bridge(context,slot,p['cutoff'],journal,grant,now)) is not None]
            if len(bindings) != 1:
                bindings=[]
            result=dict(schema='home-lab-person-request-binding-1',version=1,request_event_id=event['id'],request_digest=fingerprint,cutoff=p['cutoff'],
                person_policy_digest=digest(self.person_policy),context_digest=digest(context),first_consumer_received_at=received_at,
                bindings=bindings,reason=None if bindings else 'qualified_person_work_bridge_unavailable')
            self.person_requests[rid]=result
            return deepcopy(result)

    def _retain(self,family,cached):
        super()._retain(family,cached)
        binding=cached.get('retention_record',{}).get('original',{}).get('request_binding')
        if family != 'jev' or not isinstance(binding,dict) or binding.get('schema') != 'home-lab-person-request-binding-1' or cached['append_attempted']:
            return
        effect=self._person_effect
        try:
            _need(effect is not None,'person_publish_effect_required')
            now=self._check(effect['journal'],effect['received_at'])
            selected=binding['bindings'][0]
            current=self._bridge(effect['context'],selected['subject'],effect['request_at'],effect['journal'],effect['grant'],now)
            _need(current == selected,'person_bridge_retired_before_append')
        except SemanticRefusal:
            cached['cancelled_before_append']=True;self.pending=None
            raise

    def publish_person_jev(self,events,*,output_id,received_at,journal,grant):
        with self.lock:
            now=self._check(journal,received_at);indexed=self._events(events)
            _need(output_id in indexed and indexed[output_id]['kind'] == 'reasoning.person_output','original_person_output_required')
            event=indexed[output_id];p=event['payload'];rid=p.get('request_id')
            requests=[e for e in events if e['kind'] == 'reasoning.request' and e['payload'].get('request_id') == rid]
            deliveries=[e for e in events if e['kind'] == 'reasoning.delivery' and e['payload'].get('request_id') == rid]
            _need(len(requests) == len(deliveries) == 1,'exact_person_request_delivery_required')
            request,delivery=requests[0],deliveries[0];r,d=request['payload'],delivery['payload'];context=p.get('person_context')
            visual=self._context(context,indexed,p.get('available_at'))
            _need(p.get('schema') == OUTPUT and type(p.get('version')) is int and p['version'] == 1 and p.get('site_id') == self.session['site_id']
                  and p.get('session_id') == self.session['id'] and p.get('arm') == self.policy['arm'] and p.get('generation') == self.policy['generation']
                  and p.get('viewing_only') is True and p.get('physical_permission') is False and p.get('status') in ('ready','abstain','stale')
                  and r.get('person_context') == context and r.get('arm') == p['arm'] and r.get('generation') == p['generation']
                  and r.get('status') == 'admitted_before_transport' and d.get('state') == 'completed' and d.get('current') is True
                  and d.get('arm') == p['arm'] and d.get('generation') == p['generation'], 'person_output_chain_mismatch')
            prov=p.get('provenance',{});wanted=[str(context['visual_event_id']),str(context['semantics_event_id'])]
            expected_packet=person_wire.build_packet(context)
            _need(all(prov.get(k) == p[k] for k in ('request_id','site_id','session_id','arm','generation'))
                  and prov.get('wire_version') == r.get('wire_version') == 'den-person-facts-1' and prov.get('response_version') == 'jev-1.13.0'
                  and prov.get('input_digest') == r.get('packet_sha256') == lab_digest(r.get('request'))
                  and r.get('request') == expected_packet and p.get('diagnostics',{}).get('request') == expected_packet
                  and prov.get('question_version') == person_wire.QUESTION_VERSION and prov.get('evidence_digest') == lab_digest(context)
                  and finite(r.get('cutoff')) and prov.get('cutoff') == r['cutoff'] and finite(prov.get('deadline'))
                  and finite(r.get('deadline')) and prov['deadline'] == r['deadline']
                  and r['cutoff'] <= r['available_at'] <= p['available_at'] <= prov['deadline'] <= r['cutoff']+15
                  and prov.get('evidence_ids') == r.get('evidence_ids') == wanted and prov.get('timeline_ids') == r.get('timeline_ids') == [],
                  'person_output_provenance_mismatch')
            try:
                response=validate_jev_response(dict(model=person_wire.MODEL,answers=p.get('diagnostics',{}).get('answers')),
                    expected_packet['questions'],expected_version=person_wire.MODEL)
            except (KeyError,TypeError,ValueError):
                raise SemanticRefusal('person_output_answers_mismatch') from None
            observed,available,expiry=self._lease(p)
            _need(observed == context['packet']['observed_at'] and context['packet']['available_at'] <= r['available_at'] <= d.get('available_at',-1) <= available <= received_at
                  and expiry <= context['packet']['valid_until'], 'person_output_original_lease_invalid')
            rows=p.get('subjects');_need(isinstance(rows,list) and len(rows) == len(context['slots']), 'person_output_membership_mismatch')
            by_slot={}
            for row in rows:
                _need(isinstance(row,dict) and set(row) == {'subject_slot','instance_id','track_ref','status','judgment'}
                      and row['subject_slot'] not in by_slot and {k:row[k] for k in ('subject_slot','instance_id','track_ref')} in context['slots']
                      and row['status'] in ('ready','abstain'),'person_output_membership_mismatch')
                j=row['judgment'];_need(isinstance(j,dict) and set(j) == {'activity','confidence','probabilities','model_version'}
                    and j['activity'] in ACTIVITIES and finite(j['confidence']) and 0 <= j['confidence'] <= 1 and j['model_version'] == 'jev-1.13.0'
                    and isinstance(j['probabilities'],dict) and set(j['probabilities']) == set(ACTIVITIES)
                    and all(finite(v) and 0 <= v <= 1 for v in j['probabilities'].values()) and abs(sum(j['probabilities'].values())-1) <= .005,
                    'invalid_person_judgment')
                by_slot[row['subject_slot']]=row
                answer=deepcopy(response['answers']['activity_'+row['subject_slot']])
                if expected_packet['state']['subjects'][row['subject_slot']]['coverage'] == 'unavailable':
                    answer.update(choice='insufficient_evidence',confidence=0.,probabilities={k:float(k == 'insufficient_evidence') for k in ACTIVITIES})
                expected=dict(activity=answer['choice'],confidence=answer['confidence'],probabilities=answer['probabilities'],model_version=person_wire.MODEL)
                _need(j == expected and row['status'] == ('abstain' if answer['choice'] == 'insufficient_evidence' else 'ready'), 'person_output_answers_mismatch')
            provider_status='ready' if any(row['status'] == 'ready' for row in rows) else 'abstain'
            _need(d.get('status') == provider_status and p['status'] in (provider_status,'stale'), 'person_output_chain_mismatch')
            binding=self.person_requests.get(rid)
            _need(binding is None or binding['request_digest'] == _original_digest(request) and binding['context_digest'] == digest(context), 'person_request_binding_changed')
            if binding is None or not binding['bindings']:
                return dict(status='unavailable',reason='qualified_person_work_bridge_unavailable',supports_engagement=False,receipts=[])
            cached=self.prepared.get(('jev',event['id']))
            if (p['status'] == 'stale' or now+self.policy['jev']['clock_uncertainty_s'] >= expiry) and not (cached and cached['append_attempted']):
                return dict(status='expired',reason='original_support_expired',supports_engagement=False,receipts=[])
            selected=binding['bindings'][0];row=by_slot[selected['subject']['subject_slot']]
            if ('jev',event['id']) in self.prepared:
                parent=None  # Frozen exact retry must not borrow a later work head.
            else:
                current=self._bridge(context,selected['subject'],r['cutoff'],journal,grant,now)
                _need(current == selected,'person_bridge_retired_before_append')
                parent=self._parent(selected['work_parent_ref'],journal,grant,received_at)
            subject=next(s for s in context['packet']['subjects'] if s['instance_id'] == row['instance_id'])
            value=row['judgment']['activity'] if row['status'] == 'ready' and subject['status'] == 'available' and row['judgment']['activity'] != 'insufficient_evidence' else None
            self._person_effect=dict(journal=journal,grant=grant,received_at=received_at,context=context,request_at=r['cutoff'])
            try:
                return self._publish('jev',event,sorted(events,key=lambda e:e['id']),[visual],received_at,now,journal,grant,
                    kind='activity_refinement',value=value,available=available,expiry=min(expiry,selected['bridge']['original']['valid_until']),
                    parent=parent,request_binding=binding)
            finally:
                self._person_effect=None


def build_semantic_admission(session,*,live_config,revision,policy,original_retainer,person_bridge=None):
    if is_person_policy(policy):
        return LabPersonSemanticAdmission(session,live_config=live_config,revision=revision,policy=policy,
            original_retainer=original_retainer,person_bridge=person_bridge)
    _need(person_bridge is None,'person_bridge_requires_explicit_policy')
    return LabLiveSemanticAdmission(session,live_config=live_config,revision=revision,policy=policy,original_retainer=original_retainer)
