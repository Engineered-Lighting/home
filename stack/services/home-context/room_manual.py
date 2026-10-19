"""Independent finite manual ambient authority; never perception or physical I/O."""
from __future__ import annotations

from copy import deepcopy
import json
import math

from journal import JournalError, bounded_json, canonical, digest, finite
from live_worker import reject
from live_scene import validate_scene_worker_grant
from room_clarification import (RoomClarificationLiveDeliveryWorkerBoundary,
    validate_room_clarification_worker_policy, _publication_ref)
from room_delivery import DOMAINS, _exact, _pin, _number, _close, _presence_horizon, _vacancy_proof
from manual_control_authority import ManualControlAuthority

INTENT = 'home-live-intent-event-4'
MANUAL_SCHEMAS = frozenset({name+'-10' for name in
    ('home-live-shadow-decision','home-live-worker-checkpoint','home-live-worker-commit','home-live-input-cut')}) | frozenset({
    'home-live-room-output-2','home-live-room-snapshot-2','home-live-manual-policy-1',
    'home-live-manual-control-state-1','home-live-room-control-status-1',INTENT,
    'home-live-intent-event-2','home-live-intent-event-3','home-live-person-lifecycle-1'})
MANUAL_OUTPUTS = frozenset({'home-live-engagement-state-1','home-live-shadow-decision-10'})
SCOPE = ('site_id','room_id','session_id','domain_epoch')


def validate_manual_policy(policy, room_policy, *, operating_not_after=None):
    _exact(policy, {'schema','version','policy_ref','room_policy_ref','valid_until','max_duration_s',
                   'operating_not_after','max_active_holds','max_pending_per_domain','intent_capacity','resource_limits'})
    _exact(policy['resource_limits'],{'derived_event_bytes','worker_commit_bytes'})
    if (policy['schema'] != 'home-live-manual-policy-1' or type(policy['version']) is not int or policy['version'] != 1
        or not _pin(policy['policy_ref']) or policy['room_policy_ref'] != room_policy['policy_ref']
        or not _number(policy['valid_until'],positive=True) or not _number(policy['operating_not_after'],positive=True)
        or type(policy['max_duration_s']) not in {int,float} or policy['max_duration_s'] != 120
        or type(policy['max_active_holds']) is not int or policy['max_active_holds'] != 28
        or type(policy['max_pending_per_domain']) is not int or policy['max_pending_per_domain'] != 1
        or type(policy['intent_capacity']) is not int or policy['intent_capacity'] != 128
        or type(policy['resource_limits']['derived_event_bytes']) is not int or policy['resource_limits']['derived_event_bytes'] != 262144
        or type(policy['resource_limits']['worker_commit_bytes']) is not int or policy['resource_limits']['worker_commit_bytes'] != 1048576
        or operating_not_after is not None and policy['operating_not_after'] != operating_not_after):
        raise ValueError('exact bounded authored manual policy and operating horizon required')
    bounded_json(policy,16384)


def validate_manual_worker_policy(manifest, registry):
    worker = manifest['worker']
    if (not getattr(registry,'room_manual_interface_digest',None)
        or manifest.get('room_manual_interface_digest') != registry.room_manual_interface_digest
        or worker.get('room_manual_schema_pins') != registry.room_manual_schema_pins):
        raise ValueError('exact additive manual interface/schema pins required')
    validate_manual_policy(worker['manual_policy'],worker['room_policy'])
    prior = deepcopy(manifest)
    del prior['room_manual_interface_digest']
    del prior['worker']['manual_policy']
    del prior['worker']['room_manual_schema_pins']
    validate_room_clarification_worker_policy(prior,registry)


def validate_manual_grant(grant, config):
    private = MANUAL_SCHEMAS - MANUAL_OUTPUTS - {INTENT,'home-live-intent-event-2','home-live-intent-event-3','home-live-person-lifecycle-1'}
    if grant.payload_types & private and 'worker_commit' not in grant.operations:
        raise ValueError('private manual state requires explicit worker authority')
    if 'home-live-shadow-decision-10' in grant.payload_types and 'home-live-geometry-1' not in grant.payload_types:
        raise ValueError('full manual room decision requires separate geometry authority')
    if 'intent' in grant.operations and INTENT in grant.payload_types:
        if (grant.operations != frozenset({'read','intent'}) or grant.expected_actor_ref is None
            or '4.0' not in grant.revisions or grant.expected_worker_id is not None):
            raise ValueError('manual controls require an independently attributed human read/intent grant')


def validate_manual_worker_grant(grant, config):
    validate_scene_worker_grant(grant,config)
    if not MANUAL_SCHEMAS <= grant.payload_types:
        raise ValueError('manual worker requires complete explicit successor closure')


def _targets(room_policy):
    return [(f['fixture_id'],a['domain'],a) for f in room_policy['fixtures'] for a in f['ambient']]


def _state(identity,policy,room_policy):
    return dict(schema='home-live-manual-control-state-1',version=1,identity=deepcopy(identity),
        policy_ref=deepcopy(policy['policy_ref']),t=None,seq=-1,control_generation=0,control_active=False,
        domains=[dict(fixture_id=f,domain=d,generation=0,mode='auto',hold=None) for f,d,_ in _targets(room_policy)],settlements=[])


def _bounds(value, domain):
    return (finite(value.get('brightness_fraction')) and 0 <= value['brightness_fraction'] <= domain['max_fraction']
        and finite(value.get('cct_kelvin')) and domain['cct_min_k'] <= value['cct_kelvin'] <= domain['cct_max_k'])


def manual_transition(*, identity,manifest_ref,policy,room_policy,t,seq,control_generation,control_active,
                      publications,input_receipts,old=None):
    """Independent reference transition over prior trusted state and actual cut originals."""
    validate_manual_policy(policy,room_policy)
    prior = old.get('state',{}).get('manual_controls') if old else None
    state = deepcopy(prior) if prior else _state(identity,policy,room_policy)
    if (state['identity'] != identity or state['policy_ref'] != policy['policy_ref'] or not finite(t)
        or type(seq) is not int or seq < 0 or type(control_generation) is not int or control_generation < 0
        or type(control_active) is not bool or state['t'] is not None and (state['t'] >= t or state['seq'] >= seq)):
        raise ValueError('trusted same-scope manual predecessor and advancing cut required')
    originals = list(publications.values()) if isinstance(publications,dict) else list(publications)
    rows = sorted((p for p in originals if p['payload_type']==INTENT),key=lambda p:p['cursor'])
    receipts = {r['cursor']:r for r in input_receipts}
    settled = {r['id']:r for r in state['settlements']}
    fenced = state['control_generation'] != control_generation or not control_active
    for domain in state['domains']:
        if domain['mode']=='manual' and (fenced or t >= domain['hold']['hold_until']):
            domain.update(generation=domain['generation']+1,mode='auto',hold=None)
    # Every surviving owner requires its exact original, including after command
    # application-window expiry. It never acquires fresh observational support.
    for domain in state['domains']:
        if domain['hold'] is None: continue
        h = domain['hold']
        matches = [p for p in rows if _publication_ref(p)==h['intent_ref']]
        if len(matches)!=1 or matches[0]['cursor'] not in receipts:
            raise ValueError('active manual hold requires exact original delivered proof')
        p = matches[0]; value = p['payload']['value']
        if max(p['payload']['set_at'],p['payload']['available_at'],p['committed_at'],receipts[p['cursor']]['received_at'])>t:
            raise ValueError('active manual original is not causally available')
        if (p['payload']['kind']!='manual_ambient_hold' or p['payload']['actor_ref']!=h['actor_ref']
            or p['committed_at']!=h['accepted_at'] or h['hold_until']!=p['committed_at']+value['duration_s']
            or any(h[k]!=value[k] for k in ('brightness_fraction','cct_kelvin'))
            or p['payload']['manifest_ref']!=manifest_ref or any(p['payload'][k]!=identity[k] for k in SCOPE)
            or p['id'] not in settled or settled[p['id']]['digest']!=digest(p['payload'])):
            raise ValueError('retained manual ownership cannot rewrite its original admission')
    results = []
    commissioned = {(f,d):a for f,d,a in _targets(room_policy)}
    for p in rows:
        command = p['payload']; value = command['value']; command_digest = digest(command)
        if p['id'] in settled:
            if settled[p['id']]['digest']!=command_digest:
                raise ValueError('manual identity cannot change its original payload')
            continue
        receipt = receipts.get(p['cursor'])
        if receipt is None:
            raise ValueError('new manual command requires actual delivered receipt')
        available = max(command['set_at'],command['available_at'],p['committed_at'],receipt['received_at'])
        if t < available: continue
        key = (command['target'].get('fixture_id'),command['target'].get('domain'))
        domain = next((d for d in state['domains'] if (d['fixture_id'],d['domain'])==key),None)
        reason = None
        if (command.get('schema')!=INTENT or command.get('version')!=4 or command['manifest_ref']!=manifest_ref
            or any(command[k]!=identity[k] for k in SCOPE) or value['policy_ref']!=policy['policy_ref'] or domain is None):
            reason='manual_intent_invalid'
        elif value['expected_control_generation']!=control_generation: reason='manual_control_generation_changed'
        elif not control_active: reason='manual_control_inactive'
        elif t>=command['valid_until']: reason='manual_intent_expired'
        elif value['expected_generation']!=domain['generation']: reason='manual_generation_conflict'
        elif value['expected_control_id']!=(domain['hold']['intent_ref']['id'] if domain['hold'] else None): reason='manual_control_conflict'
        elif command['kind']=='return_ambient_auto' and domain['hold'] is None: reason='manual_not_held'
        elif command['kind']=='manual_ambient_hold':
            until=p['committed_at']+value['duration_s']
            if not _bounds(value,commissioned[key]): reason='manual_value_out_of_bounds'
            elif not finite(value['duration_s']) or not 0<value['duration_s']<=policy['max_duration_s'] or until>min(policy['valid_until'],policy['operating_not_after']): reason='manual_horizon_exceeded'
            elif t>=until: reason='manual_hold_expired'
        if reason is None:
            if command['kind']=='manual_ambient_hold':
                domain.update(mode='manual',generation=domain['generation']+1,hold=dict(
                    intent_ref=_publication_ref(p),actor_ref=command['actor_ref'],accepted_at=p['committed_at'],
                    applied_at=t,hold_until=p['committed_at']+value['duration_s'],
                    brightness_fraction=value['brightness_fraction'],cct_kelvin=value['cct_kelvin']))
                reason='manual_hold_applied'
            else:
                domain.update(mode='auto',generation=domain['generation']+1,hold=None)
                reason='manual_return_applied'
            status='applied'
        else: status='rejected'
        result=dict(id=command['id'],status=status,reason=reason)
        results.append(result); record={**result,'digest':command_digest}
        state['settlements'].append(record); settled[command['id']]=record
    if len(state['settlements'])>128: raise ValueError('manual history budget exhausted')
    state.update(t=t,seq=seq,control_generation=control_generation,control_active=control_active)
    demands=[dict(fixture_id=d['fixture_id'],domain=d['domain'],generation=d['generation'],**deepcopy(d['hold']))
             for d in state['domains'] if d['hold'] is not None]
    order={(f,d):i for i,(f,d,_) in enumerate(_targets(room_policy))}
    demands.sort(key=lambda d:(d['intent_ref']['cursor'],order[(d['fixture_id'],d['domain'])]))
    return dict(manual_controls=state,intent_results=results,manual_demands=demands)


class RoomManualLiveDeliveryWorkerBoundary(RoomClarificationLiveDeliveryWorkerBoundary):
    schema_types=RoomClarificationLiveDeliveryWorkerBoundary.schema_types | MANUAL_SCHEMAS
    output_types=MANUAL_OUTPUTS
    input_types=RoomClarificationLiveDeliveryWorkerBoundary.input_types | {INTENT}
    intent_types=RoomClarificationLiveDeliveryWorkerBoundary.intent_types | {INTENT}
    decision_type='home-live-shadow-decision-10'; checkpoint_type='home-live-worker-checkpoint-10'
    commit_type='home-live-worker-commit-10'; cut_type='home-live-input-cut-10'
    version=10; worker_version='home-live-context-worker-10.0.0'
    module_names=RoomClarificationLiveDeliveryWorkerBoundary.module_names | {'app/src/home-live-room-control-state.js'}

    def __init__(self,ingress):
        super().__init__(ingress)
        self.j.db.executescript('''
            CREATE TABLE IF NOT EXISTS live_manual_admissions(
                event_id TEXT PRIMARY KEY,cursor INTEGER UNIQUE NOT NULL,accepted_at REAL NOT NULL,
                hold_until REAL NOT NULL,request_digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS live_manual_reservations(
                hold_id TEXT PRIMARY KEY,fixture_id TEXT NOT NULL,domain TEXT NOT NULL,
                control_generation INTEGER NOT NULL,status TEXT NOT NULL,return_id TEXT);
        ''')
        self.control_authority=ManualControlAuthority(self.j,identity=ingress.identity,
            manifest_ref=ingress.config['manifest_ref'],policy=self.policy['manual_policy'],retire=self.retire_manual_controls)
        self._manual_context=None

    def configuration_description(self,nested):
        result=super().configuration_description(nested)
        result.update(version=self.worker_version,manual_policy=self.policy['manual_policy'],
            room_manual_schema_pins=self.policy['room_manual_schema_pins'],
            room_manual_interface_digest=self.j.schemas.room_manual_interface_digest)
        return result

    def cut_metadata(self): return self.control_authority.state()

    def validate_control_checkpoint(self,cp,commit):
        state=self.control_authority.state()
        if any(cp.get(k)!=v for k,v in state.items()):
            reject('manual_authority_conflict','checkpoint control authority must match actual recorded service DB state')
        if cp.get('room_manual_schema_pins')!=self.policy['room_manual_schema_pins']:
            reject('checkpoint_conflict','manual checkpoint pins differ from commissioned successor')

    def _check_intent_capacity(self,cost=1):
        count=self.j.db.execute('SELECT count(*) FROM live_scene_intents').fetchone()[0]
        reserved=self.j.db.execute("SELECT count(*) FROM live_manual_reservations WHERE status='reserved'").fetchone()[0]
        if count+reserved+cost>128:
            reject('journal_full','finite intent budget preserves exact reserved Return capacity',507)

    def _latest_controls(self):
        row=self.j.db.execute('SELECT checkpoint FROM workers WHERE worker_id=?',(self.policy['worker_id'],)).fetchone()
        return json.loads(row['checkpoint'])['state'].get('manual_controls') if row else None

    def intent(self,payload,grant):
        if not isinstance(payload,dict) or payload.get('schema')!=INTENT: return super().intent(payload,grant)
        grant.require('intent',payload.get('site_id'),payload.get('room_id'),payload_type=INTENT,revision='4.0')
        scope=self.ingress.identity
        if (grant.expected_actor_ref is None or payload.get('actor_ref')!=grant.expected_actor_ref
            or payload.get('manifest_ref')!=self.ingress.config['manifest_ref'] or any(payload.get(k)!=scope[k] for k in SCOPE)):
            reject('forbidden','manual intent requires current exact scope and authenticated actor',403)
        self.j.schemas.validate(payload)
        value,target=payload['value'],payload['target']
        if value['policy_ref']!=self.policy['manual_policy']['policy_ref']:
            reject('manual_policy_conflict','manual policy pin differs')
        domains={(f,d):a for f,d,a in _targets(self.policy['room_policy'])}
        key=(target['fixture_id'],target['domain'])
        if key not in domains: reject('manual_target_invalid','exact commissioned ambient target required',400)
        if payload['kind']=='manual_ambient_hold' and not _bounds(value,domains[key]):
            reject('manual_value_out_of_bounds','consume exact commissioned domain bounds without clamping',400)
        def accept():
            old=self.j.db.execute('SELECT event FROM live_scene_intents WHERE producer_id=? AND event_id=?',(grant.producer_id,payload['id'])).fetchone()
            if old is not None:
                original=json.loads(old['event'])
                if original['payload']!=payload: reject('identity_conflict','manual retry changed immutable request')
                return self.j._receipt(original)
            if self.j.db.execute('SELECT 1 FROM live_scene_intents WHERE event_id=?',(payload['id'],)).fetchone():
                reject('intent_id_conflict','intent ID already reserved in this exact room session')
            now=self.j.clock()
            if not 0<=payload['set_at']<=payload['available_at']<=now<payload['valid_until'] or payload['valid_until']-payload['set_at']>86400:
                reject('invalid_clock','causal current command application window required',400)
            authority=self.control_authority.state()
            if value['expected_control_generation']!=authority['control_generation']:
                reject('manual_control_generation_changed','manual control generation changed')
            if not authority['control_active']: reject('manual_control_inactive','operating session is inactive')
            pending=self.j.db.execute("SELECT event FROM live_scene_intents WHERE application_result IS NULL AND json_extract(event,'$.payload_type')=?",(INTENT,)).fetchall()
            if any(json.loads(row['event'])['payload']['target']==target for row in pending):
                reject('manual_pending_conflict','only one pending command per ambient domain')
            returning=payload['kind']=='return_ambient_auto'
            reservation=None
            if returning:
                controls=self._latest_controls()
                domain=next((d for d in controls['domains'] if (d['fixture_id'],d['domain'])==key),None) if controls else None
                if domain is None or domain['hold'] is None or value['expected_control_id'] is None:
                    reject('manual_not_held','Auto has no exact hold to release')
                if domain['generation']!=value['expected_generation']: reject('manual_generation_conflict','Return requires current domain CAS')
                if domain['hold']['intent_ref']['id']!=value['expected_control_id']: reject('manual_control_conflict','Return requires exact current original hold')
                reservation=self.j.db.execute("SELECT * FROM live_manual_reservations WHERE hold_id=? AND status='reserved'",(value['expected_control_id'],)).fetchone()
                if reservation is None or (reservation['fixture_id'],reservation['domain'])!=key or reservation['control_generation']!=authority['control_generation']:
                    reject('manual_return_reservation_unavailable','Return cannot consume another original hold reservation')
            else: self._check_intent_capacity(2)
            event=self.j._command_event(payload,grant)
            if self.j._revoked(scope['site_id'],scope['room_id'],grant.producer_id,'4.0'):
                reject('revoked','manual intent origin or room authority revoked')
            receipt,publication=self.ingress._record(event)
            if not returning:
                until=publication['committed_at']+value['duration_s']
                if until>min(self.policy['manual_policy']['valid_until'],self.policy['manual_policy']['operating_not_after']):
                    reject('manual_horizon_exceeded','requested duration exceeds original finite operating horizon')
                self.j.db.execute('INSERT INTO live_manual_admissions VALUES(?,?,?,?,?)',(payload['id'],receipt['cursor'],publication['committed_at'],until,digest(payload)))
                self.j.db.execute("INSERT INTO live_manual_reservations VALUES(?,?,?,?,'reserved',NULL)",(payload['id'],*key,authority['control_generation']))
            else:
                self.j.db.execute("UPDATE live_manual_reservations SET status='consumed',return_id=? WHERE hold_id=?",(payload['id'],value['expected_control_id']))
            self.j.db.execute('INSERT INTO live_scene_intents VALUES(?,?,?,?,?,?,NULL,NULL)',
                (receipt['cursor'],grant.producer_id,payload['id'],payload['actor_ref'],canonical(event),canonical(publication)))
            return receipt
        return self.j._transaction(accept)

    def lookup_intent(self,event_id,grant,site,room):
        result=super().lookup_intent(event_id,grant,site,room)
        if result['event']['schema']==INTENT:
            row=self.j.db.execute('SELECT accepted_at,hold_until FROM live_manual_admissions WHERE event_id=?',(event_id,)).fetchone()
            result['manual_acceptance']=dict(row) if row else None
        return result

    def additional_inputs(self,grant):
        scope=self.ingress.identity
        for kind in self.intent_types:
            revision={'home-live-intent-event-2':'2.0','home-live-intent-event-3':'3.0',INTENT:'4.0'}.get(kind,'1.0')
            grant.require('read',scope['site_id'],scope['room_id'],payload_type=kind,revision=revision)
        rows=self.j.db.execute('SELECT cursor,publication FROM live_scene_intents WHERE application_result IS NULL').fetchall()
        chosen={row['cursor']:json.loads(row['publication']) for row in rows}
        controls=self._latest_controls(); authority=self.control_authority.state()
        if controls and authority=={k:controls[k] for k in authority} and authority['control_active']:
            for domain in controls['domains']:
                if domain['hold'] is None or domain['hold']['hold_until']<=self.j.clock(): continue
                ref=domain['hold']['intent_ref']
                row=self.j.db.execute('SELECT publication FROM live_scene_intents WHERE cursor=?',(ref['cursor'],)).fetchone()
                if row is None: reject('manual_original_unavailable','active hold original unavailable')
                p=json.loads(row['publication'])
                if _publication_ref(p)!=ref: reject('manual_original_unavailable','active hold original differs')
                chosen[p['cursor']]=p
        return [chosen[c] for c in sorted(chosen)]

    def retire_manual_controls(self,*,control_generation,retired_at):
        self.j.db.execute("UPDATE live_manual_reservations SET status='released' WHERE status IN ('reserved','consumed')")
        self.j.db.execute('DELETE FROM live_worker_current WHERE payload_type=?',(self.decision_type,))

    def current(self,grant):
        authority=self.control_authority.state()
        return [p for p in super().current(grant) if p['payload_type']!=self.decision_type or
            all(p['payload']['room_control'].get(k)==v for k,v in authority.items())]

    def settle(self,commit,grant,publications,decision,engagements,old):
        cp=commit['checkpoint']; authority=self.control_authority.state()
        expected=manual_transition(identity=self.ingress.identity,manifest_ref=self.ingress.config['manifest_ref'],
            policy=self.policy['manual_policy'],room_policy=self.policy['room_policy'],t=decision['t'],seq=decision['seq'],
            **authority,publications=publications,input_receipts=commit['input_receipts'],old=old)
        if cp['state']['manual_controls']!=expected['manual_controls']:
            reject('invalid_manual_control','manual checkpoint differs from independent original transition')
        status=dict(schema='home-live-room-control-status-1',version=1,policy_ref=self.policy['manual_policy']['policy_ref'],
            room_revision_id=decision['room_output']['revision_id'],sample_at=decision['t'],
            valid_until=min(decision['t']+self.policy['max_output_age_s'],self.policy['manual_policy']['operating_not_after']),
            **authority,domains=expected['manual_controls']['domains'])
        if decision['room_control']!=status: reject('invalid_manual_control','public control state must match exact committed owners/currentness')
        manual_ids={p['id'] for p in publications.values() if p['payload_type']==INTENT}
        results=[{**r,'decision_ref':decision['id']} for r in expected['intent_results']]
        if [r for r in decision['intent_results'] if r['id'] in manual_ids]!=results or cp['state']['intent_settlements']!=decision['intent_results']:
            reject('invalid_application_result','manual settlements must match original exactly-once transition')
        prior_commit,prior_decision=deepcopy(commit),deepcopy(decision)
        prior_decision['intent_results']=[r for r in decision['intent_results'] if r['id'] not in manual_ids]
        prior_commit['checkpoint']['state']['intent_settlements']=deepcopy(prior_decision['intent_results'])
        self._manual_context=expected
        try:
            super().settle(prior_commit,grant,{c:p for c,p in publications.items() if p['payload_type']!=INTENT},prior_decision,engagements,old)
        finally: self._manual_context=None
        for result in results:
            p=next(p for p in publications.values() if p['payload_type']==INTENT and p['id']==result['id'])
            row=self.j.db.execute('SELECT * FROM live_scene_intents WHERE cursor=?',(p['cursor'],)).fetchone()
            terminal=dict(schema='home-intent-application-result-1',version=1,**result)
            if row is None or json.loads(row['publication'])!=p: reject('invalid_application_result','original manual publication required')
            if row['application_result'] is not None and json.loads(row['application_result'])!=terminal:
                reject('application_result_conflict','manual result is immutable')
            self.j.db.execute('UPDATE live_scene_intents SET application_result=?,settled_at=? WHERE cursor=?',(canonical(terminal),decision['t'],p['cursor']))
        active={d['hold']['intent_ref']['id'] for d in expected['manual_controls']['domains'] if d['hold'] is not None}
        pending={json.loads(r['event'])['payload']['id'] for r in self.j.db.execute("SELECT event FROM live_scene_intents WHERE application_result IS NULL AND json_extract(event,'$.payload.kind')='manual_ambient_hold'")}
        for row in self.j.db.execute("SELECT hold_id FROM live_manual_reservations WHERE status IN ('reserved','consumed')").fetchall():
            if row['hold_id'] not in active|pending:
                self.j.db.execute("UPDATE live_manual_reservations SET status='released' WHERE hold_id=?",(row['hold_id'],))

    def validate_room_output(self,**arguments):
        if self._manual_context is None: raise ValueError('independent manual transition context required')
        return validate_manual_room_output(**arguments,**self._manual_context)


def validate_manual_room_output(*, policy,identity,t,seq,input_cut_digest,authority_generation,
        source_generations,background,room_output,room_snapshot,prior_room_snapshot,publications,
        manual_controls,manual_demands,intent_results,max_output_age_s=3,lifecycle_policy=None):
    """Independently bound actual modeled trajectories and shared capacity.

    Manual desire is already verified from originals, separately from source
    semantics. Neither the proposed samples nor a JS controller establish their
    own origin, support horizon, priority, transition or capacity reservation.
    """
    from room_delivery import validate_room_policy
    validate_room_policy(policy)
    _exact(room_output,{'schema','version','revision_id','policy_ref','input_cut_digest','sample_at',
        'authority_generation','source_generations','fixtures','task','task_candidates'})
    _exact(room_snapshot,{'schema','version','identity','policy_digest','t','seq','authority_generation',
        'source_generations','ambient','tasks','fallback_snapshot','last_selected_fixture_id','task_capacity'})
    if (room_output['schema']!='home-live-room-output-2' or room_output['version']!=2
        or room_output['policy_ref']!=policy['policy_ref'] or room_output['input_cut_digest']!=input_cut_digest
        or room_output['sample_at']!=t or room_output['authority_generation']!=authority_generation
        or room_output['source_generations']!=source_generations or room_snapshot['schema']!='home-live-room-snapshot-2'
        or room_snapshot['version']!=2 or room_snapshot['identity']!=identity or room_snapshot['policy_digest']!=digest(policy)
        or room_snapshot['t']!=t or room_snapshot['seq']!=seq or room_snapshot['authority_generation']!=authority_generation
        or room_snapshot['source_generations']!=source_generations or len(room_snapshot['ambient'])!=28
        or len(room_output['fixtures'])!=4 or len(room_snapshot['task_capacity'])!=4):
        raise ValueError('exact independently bound manual room output/checkpoint required')
    previous=prior_room_snapshot
    if previous and (previous['identity']!=identity or previous['policy_digest']!=digest(policy)
        or previous['t'] is not None and (previous['t']>=t or previous['seq']>=seq)):
        raise ValueError('trusted preceding manual room checkpoint required')
    changed=not previous or previous['authority_generation']!=authority_generation or previous['source_generations']!=source_generations
    prior=previous['ambient'] if previous else [None]*28
    def qualified_old(row):
        return (row is not None and not changed and (row['authority']['mode']!='manual' or
            manual_controls['control_active'] and row['authority']['control_generation']==manual_controls['control_generation']
            and row['authority']['hold_until']>t))
    carry=[r['capacity_fraction'] if qualified_old(r) and r['capacity_valid_until']>t else 0 for r in prior]
    carry_until=[r['capacity_valid_until'] if qualified_old(r) and r['capacity_valid_until']>t else 0 for r in prior]
    old_tasks=previous['task_capacity'] if previous else [None]*4
    task_carry=[r['fraction'] if r and not changed and r['valid_until']>t else 0 for r in old_tasks]
    task_until=[r['valid_until'] if r and not changed and r['valid_until']>t else 0 for r in old_tasks]
    owners=manual_controls['domains']; generation=manual_controls['control_generation']
    actual=carry[:]
    priority=[n if owners[i]['mode']=='manual' and prior[i] and prior[i]['authority']['mode']=='manual' else 0 for i,n in enumerate(carry)]
    fixtures=policy['fixtures']
    slots=[(f,a,i,j,i*7+j) for i,f in enumerate(fixtures) for j,a in enumerate(f['ambient'])]

    def fit(ambient,spots):
        exposure=0
        for i,f in enumerate(fixtures):
            power=spots[i]*f['spot_power_w_at_full']; exposure+=spots[i]*f['spot_exposure_upper_at_full']
            for j,a in enumerate(f['ambient']):
                power+=ambient[i*7+j]*a['power_w_at_full']; exposure+=ambient[i*7+j]*a['exposure_upper_at_full']
            if power>f['max_power_w']+1e-10: return 'ambient_fixture_power_exceeded'
        return 'ambient_aggregate_exposure_exceeded' if exposure>policy['exposure']['max_total']+1e-10 else None

    requested=background.get('status')=='requested' and background.get('kind')=='illuminate_presence' and background.get('valid_until',0)>t
    horizon=_presence_horizon(background,publications,identity,t,source_generations) if requested else t
    vacancy=_vacancy_proof(previous,publications,identity,t,source_generations,lifecycle_policy,changed) if not requested else None
    if requested and not all(background['nominal']['brightness_fraction']*a['level_scale']<=a['max_fraction']
        and a['cct_min_k']<=background['nominal']['cct_kelvin']<=a['cct_max_k'] for _,a,_,_,_ in slots):
        requested=False
    for i,f in enumerate(fixtures):
        output=room_output['fixtures'][i]
        _exact(output,{'fixture_id','mount_id','profile_ref','domains'})
        if any(output[k]!=f[k] for k in ('fixture_id','mount_id','profile_ref')) or len(output['domains'])!=8:
            raise ValueError('exact commissioned fixture/domain output required')

    def authority(owner,spot=False):
        hold=owner['hold'] if owner else None
        return dict(mode='manual' if hold else 'auto',control_generation=generation,
            domain_generation=None if spot else owner['generation'],intent_ref=hold['intent_ref'] if hold else None,
            actor_ref=hold['actor_ref'] if hold else None,hold_until=hold['hold_until'] if hold else None)

    def verify_slot(slot):
        f,a,i,j,n=slot; owner=owners[n]; old=prior[n]; h=owner['hold']; manual=h is not None
        auth=authority(owner)
        row=room_output['fixtures'][i]['domains'][j]
        _exact(row,{'domain','status','reason','goal_id','scene_id','desired','sample','valid_until','evidence_refs','assessment_refs','authority'})
        if row['domain']!=a['domain'] or row['scene_id'] is not None or row['authority']!=auth:
            raise ValueError('ambient authority must match exact original owner')
        blank=dict(fixture_id=f['fixture_id'],domain=a['domain'],goal_id=None,cct_kelvin=None,
            from_fraction=0,to_fraction=0,started_at=0,duration_s=0,last_sample_at=t,last_fraction=0,
            last_valid_until=t,absence_since=None,basis_refs=[],authority=auth,
            capacity_fraction=carry[n],capacity_valid_until=carry_until[n])
        human_release=not manual and old and old['authority']['mode']=='manual'
        vacant=bool(not manual and vacancy and old and old['goal_id'] is not None and old['last_valid_until']>t)
        support=manual or requested or vacant
        support_until=h['hold_until'] if manual else min(background.get('valid_until',t),horizon) if requested else vacancy['valid_until'] if vacant else t
        until=min(t+max_output_age_s,support_until,policy['valid_until'],policy['exposure']['valid_until'],f['valid_until'],a['valid_until']) if support else t
        manual_goal='manual:'+digest(h['intent_ref'])[:48] if manual else None
        desired=dict(brightness_fraction=h['brightness_fraction'],cct_kelvin=h['cct_kelvin']) if manual else None
        row_expected=dict(domain=a['domain'],status='unavailable',reason='manual_room_assessment_expired' if manual else 'fresh_presence_required',
            goal_id=manual_goal,scene_id=None,desired=desired,sample=None,valid_until=t,evidence_refs=[],assessment_refs=[],authority=auth)
        expected=blank
        if support and until>t:
            target=h['brightness_fraction'] if manual else 0 if vacant else background['nominal']['brightness_fraction']*a['level_scale']
            cct=h['cct_kelvin'] if manual else old['cct_kelvin'] if vacant else background['nominal']['cct_kelvin']
            goal=manual_goal if manual else old['goal_id'] if vacant else background['goal_id']
            refs=[h['intent_ref']] if manual else vacancy['refs'] if vacant else background['evidence_refs']
            continuous=qualified_old(old) and old['last_valid_until']>t
            retained=(continuous and old['authority']==auth and old['goal_id']==goal and old['to_fraction']==target
                and old['cct_kelvin']==cct and ((old['absence_since'] is not None) if vacant else old['absence_since'] is None))
            origin=old['last_fraction'] if continuous else 0
            start=old['started_at'] if retained else vacancy['since']+policy['ambient_timing']['vacancy_delay_s'] if vacant and not human_release else t
            duration=old['duration_s'] if retained else policy['ambient_timing']['fade_in_s' if target>=origin else 'fade_out_s']
            origin=old['from_fraction'] if retained else origin
            progress=1 if duration==0 else max(0,min(1,(t-start)/duration))
            sample=origin+(target-origin)*progress
            upper=max(target,sample) if progress>=1 else max(origin,target,sample)
            candidate=actual[:]; candidate[n]=max(candidate[n],upper)
            future=priority[:]; future[n]=max(future[n],upper)
            future_reason=fit(future,[0]*4)
            reason=fit(candidate,task_carry) or ('manual_priority_reserved' if future_reason else None)
            if manual and not future_reason: priority[n]=future[n]
            if reason:
                row_expected['reason']=reason
            else:
                actual[n]=candidate[n]
                if not manual: priority[n]=future[n]
                expected=dict(fixture_id=f['fixture_id'],domain=a['domain'],goal_id=goal,cct_kelvin=cct,
                    from_fraction=origin,to_fraction=target,started_at=start,duration_s=duration,
                    last_sample_at=t,last_fraction=sample,last_valid_until=until,
                    absence_since=vacancy['since'] if vacant else None,basis_refs=refs,authority=auth,
                    capacity_fraction=max(carry[n],upper),capacity_valid_until=max(carry_until[n],until if upper>0 else 0))
                row_expected.update(status='modeled',reason='qualified_modeled_manual' if manual else 'qualified_modeled_vacancy' if vacant else 'qualified_modeled_presence',
                    goal_id=goal,desired=dict(brightness_fraction=target,cct_kelvin=cct),sample=dict(brightness_fraction=sample,cct_kelvin=cct),
                    valid_until=until,evidence_refs=refs,assessment_refs=[a['assessment_ref'],policy['exposure']['assessment_ref']])
        _exact(room_snapshot['ambient'][n],expected.keys())
        for key,value in expected.items(): _close(room_snapshot['ambient'][n][key],value,'ambient checkpoint differs from independent trajectory/capacity')
        for key,value in row_expected.items():
            if key=='sample' and value is not None:
                _exact(row[key],value.keys())
                for part in value: _close(row[key][part],value[part],'manual/automatic sample differs from independent ramp')
            else: _close(row[key],value,'ambient output differs from original desire/support/budget')

    order={(f['fixture_id'],a['domain']):n for f,a,_,_,n in slots}
    for demand in manual_demands: verify_slot(slots[order[(demand['fixture_id'],demand['domain'])]])
    for slot in slots:
        if owners[slot[4]]['mode']=='auto': verify_slot(slot)

    task=room_output['task']; selected_id=task['fixture_id']
    _exact(task,{'fixture_id','allocation','execution','display_lease'})
    commissioned=[f for f in fixtures if f['task_policy'] is not None]
    if ([r['fixture_id'] for r in room_snapshot['tasks']]!=[f['fixture_id'] for f in commissioned]
        or [r['fixture_id'] for r in room_output['task_candidates']]!=[f['fixture_id'] for f in commissioned]
        or room_snapshot['last_selected_fixture_id']!=selected_id):
        raise ValueError('exact task candidate/selected snapshot linkage required')
    for i,f in enumerate(fixtures):
        spot=room_output['fixtures'][i]['domains'][7]
        _exact(spot,{'domain','status','reason','goal_id','scene_id','desired','sample','valid_until','evidence_refs','assessment_refs','authority'})
        if spot['authority']!=authority(None,True): raise ValueError('spotlight cannot acquire manual ambient authority')
        capacity,capacity_until=task_carry[i],task_until[i]
        if f['fixture_id']==selected_id:
            allocation,execution,lease=task['allocation'],task['execution'],task['display_lease']
            if f['task_policy'] is None or allocation.get('status')!='allocated' or execution.get('status')!='virtual' or lease.get('status')!='current':
                raise ValueError('selected spotlight requires independent conservative task admission')
            if len(allocation['assignments'])!=1 or len(execution['readback'])!=1 or len(allocation['candidates'])!=1:
                raise ValueError('exact selected task member required')
            assignment,readback=allocation['assignments'][0],execution['readback'][0]
            member=next(r['snapshot']['state'] for r in room_snapshot['tasks'] if r['fixture_id']==selected_id)
            if (not member['plan'] or member['last_readback']!=readback or member['last_valid_until']!=lease['valid_until']
                or assignment['fixture_id']!=selected_id or readback['fixture_id']!=selected_id
                or allocation['candidates'][0]!=next(r for r in room_output['task_candidates'] if r['fixture_id']==selected_id)
                or not t<lease['valid_until']<=min(t+max_output_age_s,policy['valid_until'],policy['exposure']['valid_until'],f['valid_until'])):
                raise ValueError('task readback must name achieved selected snapshot under exact lease')
            transition=member.get('brightness_transition')
            applicable_from=transition['from_fraction'] if transition and t<transition['started_at']+transition['duration_s'] else 0
            upper=max(assignment['nominal']['brightness_fraction'],readback['brightness_fraction'],applicable_from)
            candidate=task_carry[:];candidate[i]=max(candidate[i],upper)
            future=[0]*4;future[i]=upper
            if fit(actual,candidate) or fit(priority,future):
                raise ValueError('combined task budget or prior manual priority would be exceeded')
            capacity=max(capacity,upper);capacity_until=max(capacity_until,lease['valid_until'] if upper>0 else 0)
            expected=dict(domain='spotlight',status='modeled',reason='qualified_modeled_task',goal_id=assignment['goal_id'],
                scene_id=member['plan']['scene_id'],desired={k:assignment['nominal'][k] for k in ('brightness_fraction','cct_kelvin')},
                sample={k:readback[k] for k in ('brightness_fraction','cct_kelvin')},valid_until=lease['valid_until'],
                evidence_refs=lease['geometry_refs'],assessment_refs=execution['assessment_refs'],authority=authority(None,True))
            if spot!=expected: raise ValueError('spotlight domain differs from conservative task linkage')
        elif (spot['domain']!='spotlight' or spot['status'] not in {'unassigned','unavailable'} or spot['desired'] is not None
            or spot['sample'] is not None or spot['goal_id'] is not None or spot['scene_id'] is not None or spot['valid_until']!=t
            or spot['evidence_refs'] or spot['assessment_refs']):
            raise ValueError('unselected spotlight cannot carry an output sample')
        expected=dict(fixture_id=f['fixture_id'],fraction=capacity,valid_until=capacity_until)
        if f['task_policy'] is None: expected.update(fraction=0,valid_until=0)
        if room_snapshot['task_capacity'][i]!=expected: raise ValueError('task reserve must derive only original committed trajectory')
    if selected_id is None and (task['allocation'].get('status')!='unavailable' or task['execution'].get('status')!='unavailable' or task['display_lease'].get('status')!='withdrawn'):
        raise ValueError('unselected task authority must be withdrawn')
    body={k:v for k,v in room_output.items() if k!='revision_id'}
    if room_output['revision_id']!='room:'+digest([identity,seq,body])[:48]:
        raise ValueError('content-bound room revision required')
    bounded_json(room_output,262144)
