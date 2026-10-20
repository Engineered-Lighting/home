"""Retained qualified desk originals -> exact current viewing-person witness.

This adapter does not solve association or commission a method. It joins the
independent stereo association's observation-local reference through the real
projection to the raw detector instance, then binds that instance to current P1
membership. The incumbent desk method remains one person / one pose only.
No thread, model, capture owner, Journal writer or physical authority is added.
"""
from copy import deepcopy
import json
import threading

from journal import Journal, digest, finite
from live_capture import _original_digest
from lab_live_desk_admission import LabLiveDeskAdmission, _original
from lab_live_desk_anchor import _projected_pose
from lab_person_semantic_admission import LabPersonSemanticAdmission, TrustedPersonBridge
from jev_person_source import freeze_person_context
from persisted_evidence import observation_record
from reasoning import digest as lab_digest
from store import Store


class DeskPersonBridgeRefusal(ValueError):
    pass


def _need(condition, reason):
    if not condition:
        raise DeskPersonBridgeRefusal(reason)


class DeskPersonBridgeSource:
    """Fixed, bounded server capability; bind once to the existing graph.

    prepare_context must finish before the scheduler samples its JEV cutoff.
    Its synchronous retainer must acknowledge the exact complete record. A
    failed/lost ACK reserves that exact record for retry, never another witness.
    """
    def __init__(self, scope, bridge_policy, *, desk_admission, original_retainer,
                 clock, max_originals=24):
        self.capability = TrustedPersonBridge(scope, bridge_policy, self._resolve_current)
        _need(isinstance(desk_admission, LabLiveDeskAdmission)
              and desk_admission.scope == scope and desk_admission.policy['version'] in (2,3,4)
              and bridge_policy['source_id'] == desk_admission.policy['source_id']
              and bridge_policy['method_ref'] == desk_admission.policy['association_method_ref'],
              'fixed_qualified_stereo_desk_source_required')
        _need(callable(original_retainer) and callable(clock) and type(max_originals) is int
              and 1 <= max_originals <= 24, 'bounded_person_bridge_ports_required')
        self.scope, self.policy = deepcopy(scope), deepcopy(bridge_policy)
        self.desk, self.retain, self.clock = desk_admission, original_retainer, clock
        self.max_originals = max_originals
        self._pin = digest([self.scope,self.policy,desk_admission.binding_digest,max_originals])
        self.lock = threading.RLock()
        self.records, self.prepared = {}, {}
        self.retained = set()
        self.pending, self.in_flight, self.quiesced = None, False, False
        self.store = self.journal = self.grant = self.semantic = self.semantic_admission = None

    def bind(self, *, store, journal, grant, semantic_admission):
        with self.lock:
            _need(isinstance(store, Store) and isinstance(journal, Journal)
                  and isinstance(semantic_admission, LabPersonSemanticAdmission)
                  and semantic_admission.person_bridge is self.capability
                  and semantic_admission.scope == self.scope
                  and semantic_admission.person_policy['bridge'] == self.policy
                  and semantic_admission.config == self.desk.config == journal.live_config
                  and semantic_admission.session['id'] == self.scope['session_id'],
                  'fixed_actual_person_desk_graph_required')
            _need(self.store is None or (self.store is store and self.journal is journal
                  and self.grant is grant and self.semantic is semantic_admission), 'person_bridge_graph_rebind_refused')
            self.store, self.journal, self.grant, self.semantic = store, journal, grant, semantic_admission
            self.semantic_admission = semantic_admission
            return self

    def _raw(self):
        # Same actual Store, bounded latest raw originals; no caller projection.
        with self.store.lock:
            rows = self.store.db.execute("SELECT * FROM events WHERE session_id=? AND kind IN "
                "('perception.output','qwen.person_observation') ORDER BY id DESC LIMIT 512",
                (self.scope['session_id'],)).fetchall()
        return [{**dict(row),'payload':json.loads(row['payload'])} for row in rows]

    def _check(self, context):
        _need(not self.quiesced and self.store is not None, 'person_bridge_quiesced_or_unbound')
        _need(digest([self.scope,self.policy,self.desk.binding_digest,self.max_originals]) == self._pin
              and self.capability.binding() == dict(scope=self.scope,bridge_policy_digest=digest(self.policy)),
              'person_bridge_configuration_changed')
        now = self.clock()
        _need(finite(now) and now >= 0, 'person_bridge_clock_unavailable')
        session = self.store.get_session(self.scope['session_id'])
        _need(session['state'] == 'recording' and session['site_id'] == self.scope['site_id']
              and not self.desk.quiesced and not self.semantic.quiesced, 'person_bridge_source_not_current')
        _need(isinstance(context,dict) and isinstance(context.get('slots'),list)
              and len(context['slots']) == 1, 'one_qualified_desk_person_required')
        rows = self._raw()
        frozen = freeze_person_context(rows,site_id=session['site_id'],session_id=session['id'],
            arm=self.semantic.policy['arm'],generation=self.semantic.policy['generation'],cutoff=now)
        _need(frozen == context, 'person_bridge_current_originals_mismatch')
        # Also enforce the existing qualified consumer's exact source/schema,
        # media, dimensions, flags, clock and current experiment constraints.
        indexed = {row['id']:row for row in rows}
        self.semantic._context(context,indexed,now)
        _need(context['packet']['basis']['scope']['session_generation'] == session['generation'],
              'person_bridge_session_generation_changed')
        with self.store.lock:
            active=self.store.db.execute('SELECT generation FROM lab_experiment_state WHERE session_id=?',
                                         (session['id'],)).fetchone()
        _need(active is not None and active[0] == self.semantic.policy['generation'],
              'person_bridge_model_generation_changed')
        return now, indexed

    def _basis(self, context):
        now,indexed = self._check(context)
        ref = self.semantic.current_work_parent(self.journal,self.grant,now)
        _need(ref is not None, 'qualified_current_desk_parent_unavailable')
        parent = self.semantic._parent(ref,self.journal,self.grant,now)
        p = parent['payload']
        _need(p['source_id'] == self.policy['source_id'] and p['association']['method_ref'] == self.policy['method_ref'],
              'person_bridge_desk_method_mismatch')
        refs = p['original_receipt_refs']
        _need(isinstance(refs,list) and len(refs) == 1, 'exact_desk_original_required')
        original_ref = refs[0]
        record = self.desk.resolve_receipt(original_ref['store_id'],original_ref['receipt_id'])
        original = self.desk.original_receipt(original_ref['store_id'],original_ref['receipt_id'])
        _need(record['ref'] == original_ref and _original(original) == original_ref['digest']
              and original['policy'] == self.desk.policy, 'retained_desk_original_mismatch')
        value = original['inputs']
        _need(_original(value) == original['inputs_digest'], 'retained_desk_inputs_mismatch')
        visual = indexed[context['visual_event_id']]
        captured = value['capture_original']['event']
        _need(captured['id'] == visual['id'] and captured['session_id'] == visual['session_id']
              and captured['kind'] == visual['kind'] == 'perception.output'
              and lab_digest(captured['payload']) == context['visual_payload_digest'] == lab_digest(visual['payload'])
              and str(visual['id']) in p['observation_roots'], 'person_bridge_exact_capture_required')
        a = value['stereo_qualification']['association']
        _need(a['status'] == 'qualified' and a['scope'] == self.scope
              and a['method_ref'] == self.policy['method_ref'] and a['assessment_ref'] == self.policy['qualification_ref']
              and a['person_ref'] == p['person_ref'] and a['subject_ref'] == p['subject_ref']
              and a['source_generation'] == p['association']['generation']
              and a['source_position'] == value['anchor']['source_position']
              and a['capture_ref'] == value['capture_ref'] and a['observed_at'] == visual['payload']['observed_at']
              and a['state'] == 'seated_unique' and type(a['occupant_count']) is int and a['occupant_count'] == 1
              and a['nose_visible'] is True, 'independent_person_association_mismatch')
        projected_ref,_ = _projected_pose(value['capture_original'])
        _need(a['instance_ref'] == projected_ref, 'independent_person_instance_mismatch')
        # Reproduce only the existing projection's admitted object order, while
        # preserving the raw ID join. Neither index zero nor uniqueness grants
        # a person identity. The independent association must name this ref.
        from jev_evidence import OBJECTS
        raw = observation_record(visual['id'],visual['kind'],visual['payload'],self.semantic.session)
        _need(raw is not None, 'original_person_projection_required')
        objects = [o for o in raw.get('objects',[])[:128] if isinstance(o,dict) and o.get('label') in OBJECTS]
        people = [(i,o) for i,o in enumerate(objects) if o['label'] == 'person']
        poses = raw.get('pose_instances',[])
        _need(len(people) == len(poses) == 1, 'one_qualified_desk_person_required')
        index,person = people[0]
        identity = person.get('instance_id',person.get('instance_ref'))
        _need(isinstance(identity,str) and identity and projected_ref == 'object-'+str(index)
              and sum(o.get('instance_id',o.get('instance_ref')) == identity for o in objects) == 1
              and poses[0].get('instance_id',poses[0].get('instance_ref')) == identity
              and context['slots'][0]['instance_id'] == identity, 'independent_person_instance_mismatch')
        source = context['packet']['basis']['source']
        _need(source['frame_id'] == visual['payload']['frame_id'] and p['observed_at'] <= source['observed_at']
              and parent['committed_at'] <= now and p['available_at'] <= now
              and a['observed_at'] <= a['available_at'] <= now < a['valid_until'], 'person_bridge_original_lease_invalid')
        expiry = min(context['packet']['valid_until'],p['valid_until'],p['parts']['work']['valid_until'],a['valid_until'])
        _need(now+self.semantic.policy['jev']['clock_uncertainty_s'] < expiry, 'person_bridge_original_expired')
        return now,parent,ref,expiry

    def prepare_context(self, context):
        if not self.lock.acquire(False):
            return dict(status='unavailable',reason='person_bridge_busy')
        try:
            now,parent,parent_ref,expiry = self._basis(context)
            query = self.semantic.bridge_query(context,context['slots'][0])
            identity = 'person-work-bridge:'+digest(query)
            if identity in self.records:
                record = self.records[identity]
                _need(record['original']['work_parent_ref'] == parent_ref, 'person_bridge_parent_changed')
                _need(record['original']['available_at'] <= now
                      and now+self.semantic.policy['jev']['clock_uncertainty_s'] < record['original']['valid_until'],
                      'person_bridge_original_expired')
                return dict(status='available',record=deepcopy(record))
            _need(self.pending in (None,identity), 'person_bridge_pending_exact_retry_required')
            if identity not in self.prepared:
                _need(len(self.prepared) < self.max_originals, 'person_bridge_original_budget_exhausted')
                original = dict(schema='home-lab-person-work-bridge-1',version=1,
                    **deepcopy(query),**{k:deepcopy(self.policy[k]) for k in ('source_id','method_ref','qualification_ref')},
                    work_parent_ref=deepcopy(parent_ref),qualified_original_refs=deepcopy(parent['payload']['original_receipt_refs']),
                    observed_at=context['packet']['observed_at'],available_at=max(now,parent['payload']['available_at'],context['packet']['available_at']),
                    valid_until=expiry)
                self.prepared[identity]=dict(ref=dict(store_id=self.policy['receipt_store_id'],receipt_id=identity,
                    component_id=self.policy['receipt_component_id'],digest=digest(original)),original=original)
            record = self.prepared[identity]
            _need(record['original']['work_parent_ref'] == parent_ref, 'person_bridge_parent_changed')
            if identity not in self.retained:
                self.pending,self.in_flight = identity,True
                try:
                    ack = self.retain(deepcopy(record))
                    _need(ack == dict(status='retained',digest=_original_digest(record)), 'person_bridge_retention_unconfirmed')
                    self.retained.add(identity)
                    self.pending = None
                except Exception:
                    return dict(status='unavailable',reason='person_bridge_retention_unconfirmed')
                finally:
                    self.in_flight = False
            # Slow retention may cross Stop, expiry, scope or a newer frame.
            _,_,current_ref,_ = self._basis(context)
            _need(current_ref == parent_ref, 'person_bridge_parent_changed')
            self.records[identity] = deepcopy(record)
            self.pending = None
            return dict(status='available',record=deepcopy(record))
        except (KeyError,TypeError,ValueError,AttributeError) as error:
            # Reasons are bounded symbolic categories, never originals or IDs.
            reason = str(error) if isinstance(error,DeskPersonBridgeRefusal) else 'person_bridge_originals_unavailable'
            return dict(status='unavailable',reason=reason)
        finally:
            self.lock.release()

    def _resolve_current(self, store_id, receipt_id):
        # A nonblocking read avoids reversing the semantic→bridge lock order.
        if self.quiesced or not self.lock.acquire(False):
            return None
        try:
            record=self.records.get(receipt_id)
            if store_id != self.policy['receipt_store_id'] or record is None:
                return None
            original=record['original']
            context=dict(schema='home-lab-person-jev-input-1',version=1,
                **{k:original[k] for k in ('visual_event_id','visual_payload_digest','semantics_event_id','semantics_payload_digest')})
            # Current raw context is rederived, never reconstructed as facts.
            now=self.clock()
            if not finite(now):return None
            rows=self._raw()
            frozen=freeze_person_context(rows,site_id=self.scope['site_id'],session_id=self.scope['session_id'],
                arm=self.semantic.policy['arm'],generation=self.semantic.policy['generation'],cutoff=now)
            if frozen is None or any(frozen[k]!=v for k,v in context.items()):return None
            _,_,ref,_=self._basis(frozen)
            if (ref!=original['work_parent_ref'] or now<original['available_at']
                    or now+self.semantic.policy['jev']['clock_uncertainty_s']>=original['valid_until']):
                return None
            return deepcopy(record)
        except (KeyError,TypeError,ValueError,AttributeError):
            return None
        finally:
            self.lock.release()

    def original_receipt(self, store_id, receipt_id):
        """Completed retained original for archival only; never a new witness."""
        with self.lock:
            _need(store_id == self.policy['receipt_store_id'], 'person_bridge_receipt_store_mismatch')
            return deepcopy(self.records[receipt_id])

    def quiesce(self):
        self.quiesced = True
        return dict(status='unconfirmed' if self.pending is not None or self.in_flight else 'quiescent',
                    in_flight=self.in_flight,pending=self.pending,retained_originals=len(self.records))
