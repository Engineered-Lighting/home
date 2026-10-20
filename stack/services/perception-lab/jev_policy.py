"""Lab-specific, durable structured-egress authorization; no network calls."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import threading
import re
from reasoning import canonical, digest, finite
from jev_evidence import (project, project_context, fact_granted, granted_bindings, AMBIENT_EGRESS, CAPTION_EGRESS,
                          FACT_GRANTS, OPAQUE, OPAQUE_TOKEN, PRESENCE_EGRESS, QWEN_EGRESS, VJEPA_EGRESS)
from jev_wire import ROOM_WIRES, WIRE_VERSION, WIRE_VERSION_8, WIRE_VERSIONS


# Admitted digests remembered for their publish check; far above the two
# reasoning arms' in-flight requests.
VERIFIED_ITEMS=64
# Optional household grants: each names an exact egress schema, never a flag.
GRANT_KEYS=('historical_caption','ambient_context','owner_presence','device_context')
# Optional list of granted structured-source schemas (e.g. lab-live-scene-1,
# vjepa-den-1).
SOURCES_KEY='structured_sources'
# Controller-assigned identifiers: a ledger event id or a code-built digest id.
# Never prose, so no operator or model text can ride in them.
RECORD_ID=re.compile(r'[A-Za-z0-9_:-]{1,100}')
# v6 (den-facts-6) ledger re-derivation: ids remembered as verified per
# (session, arm, generation), and at most this many first-seen timeline frames
# re-derived per request; any remainder is recorded as timeline_verified
# 'partial' and left to the offline audit.
FRAME_MEMO_ITEMS=1024
FRAME_REDERIVE_PER_CALL=32
TIMELINE_KEYS=frozenset(('id','observed_at','available_at','people','cue_state','head_yaw','head_pitch'))
# v6 sidecar shared records: at most one per kind, each under its own grant.
# The room facts record rides only a room wire (den-facts-8), under that wire's
# own grant, and is re-derived from its ledger row like the sidecar records.
SHARED_KINDS={'qwen.observation':QWEN_EGRESS,'vjepa.observation':VJEPA_EGRESS,'room.facts':WIRE_VERSION_8}
# Controlled shadow-verdict codes for the v6 checks' error messages.
SHADOW_REASONS={'Structured source permission mismatch':'source_mismatch',
    'Qwen scene egress not permitted':'qwen_scene_not_permitted',
    'V-JEPA scene egress not permitted':'vjepa_scene_not_permitted',
    'Ambient context egress not permitted':'ambient_not_permitted',
    'Owner presence egress not permitted':'presence_not_permitted',
    'Device context egress not permitted':'device_context_not_permitted'}


def structured_sources(policy):
    """The household's granted structured-source schemas; absent is none."""
    value=policy.get(SOURCES_KEY,[])
    if (not isinstance(value,list) or len(value)>8 or len(set(value))!=len(value)
            or any(not isinstance(v,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',v) for v in value)):
        raise ValueError('Lab household policy unavailable')
    return frozenset(value)


def opaque_provenance(record):
    """Provenance that could carry operator/source text must be a digest token."""
    for key,prefix in OPAQUE.items():
        if key not in record:continue
        values=record[key] if key=='evidence_roots' else [record[key]]
        if (not isinstance(values,list) or not values or len(values)>64
                or any(not isinstance(v,str) or not OPAQUE_TOKEN.fullmatch(v) or not v.startswith(prefix+'-') for v in values)):
            return False
    return (isinstance(record.get('id'),(str,int)) and not isinstance(record.get('id'),bool)
            and RECORD_ID.fullmatch(str(record['id'])) is not None)


class LabJevPolicy:
    def __init__(self, store, policy_reader, toggle_reader, *, ha_bindings=None, zone_bindings=None, site_timezone=None,
                 monitor_side=None, desk_seat=None, audit_only=False):
        self.store,self.read,self.toggle=store,policy_reader,toggle_reader
        self.ha_bindings=ha_bindings or {};self.zone_bindings=zone_bindings or {}
        # The site zone this gate re-derives ambient clock facts with; an
        # unloadable zone re-derives 'unknown', exactly as the runtime does.
        self.site_timezone=site_timezone
        # The site's optional monitor side (head-direction-1): this gate's own
        # config, with which it recomputes head_toward and the PC-input cue.
        from jev_semantics import MONITOR_SIDES
        if monitor_side is not None and (not isinstance(monitor_side,str) or monitor_side not in MONITOR_SIDES):
            raise ValueError('Registered semantic source bindings required')
        self.monitor_side=monitor_side
        # The site's optional desk_seat region (desk-seat-1): this gate's own
        # config, with which it recomputes a den-facts-7 now.position.
        from jev_semantics import desk_seat_config
        try:
            self.desk_seat=None if desk_seat is None else desk_seat_config(desk_seat)
        except ValueError:
            raise ValueError('Registered semantic source bindings required') from None
        # Packet digests whose structure this gate verified at 'send' and then
        # durably admitted, keyed by request. The projection and semantic checks
        # are deterministic in the packet and this gate's fixed bindings, so the
        # 'publish' re-check of the same digest does not recompute them; every
        # policy, switch, identity and durable-row check still runs.
        # Both reasoning arms' workers call this gate concurrently.
        self.verified=OrderedDict();self.verified_lock=threading.Lock()
        # v6: {(session, arm, generation, kind, id): digest of the re-derived
        # sidecar item}, so each ledger row is re-derived once.
        self.frames=OrderedDict();self.frames_lock=threading.Lock()
        if audit_only:
            # Offline audit/replay over a read-only ledger: verify_facts only.
            # No policy or switch reader, so __call__ can never admit egress.
            self.read=self.toggle=None
            return
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS lab_jev_egress(
                request_id TEXT PRIMARY KEY, packet_digest TEXT NOT NULL,
                admitted_at REAL NOT NULL, revision TEXT NOT NULL, generation INTEGER NOT NULL)''')

    def _current(self):
        """(policy, granted structured sources, now) after the policy file,
        its expiry and the fresh kill switch are verified."""
        policy=deepcopy(self.read());toggle=self.toggle();now=self.store.clock()
        required={'schema_version','enabled','scope','revision','generation','expires_at',
                  'site_id','camera_ids','model_selector','max_calls_per_minute'}
        if (not isinstance(policy,dict) or set(policy)-{'max_session_calls',SOURCES_KEY,*GRANT_KEYS}!=required or policy['schema_version']!=1
                or any(not isinstance(policy.get(k,''),str) for k in GRANT_KEYS)
                or policy['enabled'] is not True or policy['scope']!='household_structured'
                or not isinstance(policy['revision'],str) or not policy['revision']
                or type(policy['generation']) is not int or policy['generation']<0
                or not finite(policy['expires_at']) or policy['expires_at']<=now
                or type(policy['max_calls_per_minute']) is not int or not 2<=policy['max_calls_per_minute']<=120
                or not isinstance(policy['camera_ids'],list) or not policy['camera_ids']
                or not all(isinstance(c,str) and c for c in policy['camera_ids'])):
            raise ValueError('Lab household policy unavailable')
        sources=structured_sources(policy)
        if (not isinstance(toggle,dict) or toggle.get('state')!='on'
                or not finite(toggle.get('received_at')) or not 0<=now-toggle['received_at']<5):
            raise ValueError('Fresh Lab kill-switch state required')
        return policy,sources,now

    def __call__(self, context, packet, phase, local=None):
        policy,sources,now=self._current()
        state=packet.get('state',{})
        if isinstance(state,dict) and state.get('schema') in WIRE_VERSIONS:
            return self._facts(context,packet,phase,local,policy,sources,now)
        if local is not None:
            # A sidecar belongs only to a facts-only (v6) packet.
            raise ValueError('Structured source permission mismatch')
        records=state.get('evidence',[])
        admitted=None
        if phase=='publish':
            with self.verified_lock:admitted=self.verified.get(context.get('request_id'))
        verified=admitted is not None and admitted==digest(packet)
        if (phase not in ('send','publish') or packet.get('model')!=policy['model_selector']
                or context.get('site_id')!=policy['site_id'] or state.get('site_id')!=policy['site_id']
                or not records or (not verified and project(records,ha_bindings=self.ha_bindings,zone_bindings=self.zone_bindings)!=records)
                or any(r.get('site_id')!=policy['site_id'] or r.get('camera_id') not in policy['camera_ids']
                    or r.get('session_id')!=context.get('session_id') or r.get('arm')!=context.get('arm')
                    or r.get('generation')!=context.get('generation') or not opaque_provenance(r) for r in records)):
            # site_id and camera_id must equal the household policy's own grant;
            # session_id the controller-generated session; id a ledger or code
            # id; roots, frame_id and capture_epoch opaque digests.
            raise ValueError('Structured source permission mismatch')
        # Caption egress is off unless the household policy names exactly this
        # projection schema; a changed schema needs a new explicit grant.
        if (any(r.get('source_kind')=='caption.receipt' or 'historical_caption' in r for r in records)
                and policy.get('historical_caption')!=CAPTION_EGRESS):
            raise ValueError('Historical caption egress not permitted')
        # Owner home/away and the den device facts are off unless the household
        # policy names their schema. The provider drops an ungranted fact per
        # tick, so a packet still carrying one is refused here.
        carried={fact for r in records if isinstance(r.get('ha'),dict) for fact in r['ha']}
        if 'owner_home' in carried and not fact_granted('owner_home',policy):
            raise ValueError('Owner presence egress not permitted')
        if any(f in FACT_GRANTS and not fact_granted(f,policy) for f in carried):
            raise ValueError('Device context egress not permitted')
        # Continuous Qwen scene facts only under the exact structured-source grant.
        if (any(r.get('source_kind')=='qwen.observation' or 'qwen_scene' in r for r in records)
                and QWEN_EGRESS not in sources):
            raise ValueError('Qwen scene egress not permitted')
        # V-JEPA den buckets likewise only under their exact grant.
        if (any(r.get('source_kind')=='vjepa.observation' or 'vjepa_scene' in r for r in records)
                and VJEPA_EGRESS not in sources):
            raise ValueError('V-JEPA scene egress not permitted')
        # One shared context block per packet: inside state.semantic when
        # semantic, else state.shared_context; never both.
        semantic=state.get('semantic') if isinstance(state.get('semantic'),dict) else None
        if ('semantic' in state and semantic is None) or (semantic is not None and 'shared_context' in state):
            raise ValueError('Structured source permission mismatch')
        shared=semantic.get('context') if semantic is not None else state.get('shared_context')
        if shared is not None or (semantic is not None and 'context' in semantic):
            try:
                admitted=isinstance(shared,dict) and (verified or project_context(shared)==shared)
            except ValueError:
                admitted=False
            if not admitted:
                raise ValueError('Structured source permission mismatch')
            # A fact the block reports as not carried is never in the packet,
            # nor a Qwen scene record the block reports as withheld or absent.
            if (any(shared['ha_facts'].get(f,'supplied')!='supplied' for f in carried)
                    or (any(r.get('source_kind')=='qwen.observation' for r in records)
                        and shared['coverage']['qwen_scene'] not in ('supplied','stale','invalid'))
                    or (any(r.get('source_kind')=='vjepa.observation' for r in records)
                        and shared['coverage']['temporal_encoder'] not in ('supplied','stale','invalid'))):
                raise ValueError('Structured source permission mismatch')
            if 'ambient' in shared:
                # Ambient egress is off unless the household policy names this schema.
                if policy.get('ambient_context')!=AMBIENT_EGRESS:
                    raise ValueError('Ambient context egress not permitted')
                from ambient_context import facts, zone
                ambient=shared['ambient']
                if not verified and ambient!=facts(state.get('observation_cutoff'),zone(self.site_timezone),
                                                   daylight=ambient['daylight'],weather=ambient['weather']):
                    raise ValueError('Structured source permission mismatch')
        if semantic is not None:
            from jev_evidence import head_words
            from jev_semantics import semantic_state
            # Head blocks hold registered words only, then the whole block
            # must equal this gate's recompute with its own monitor_side.
            observations=semantic.get('observations')
            if (not verified and (not isinstance(observations,list)
                    or any(not isinstance(o,dict) or ('head' in o and not head_words(o['head'],self.monitor_side))
                           for o in observations)
                    or semantic!=semantic_state(records,state['observation_cutoff'],context=shared,
                                                monitor_side=self.monitor_side))):
                raise ValueError('Structured source permission mismatch')
        packet_hash=self._admit(context,packet,phase,policy,now)
        if phase=='send':
            # Committed: remember the verified structure for this request's
            # publish check only. Bounded to the most recent admissions.
            self._remember(context['request_id'],packet_hash)
        return self._granted(policy,now)

    def _granted(self, policy, now):
        return {'allowed':True,'scope':'household_structured','revision':policy['revision'],
                'generation':policy['generation'],'expires_at':min(policy['expires_at'],now+5)}

    def _remember(self, rid, value):
        with self.verified_lock:
            self.verified[rid]=value
            self.verified.move_to_end(rid)
            while len(self.verified)>VERIFIED_ITEMS:
                self.verified.popitem(last=False)

    def _admit(self, context, packet, phase, policy, now, facts=None):
        """Durable admission (send) or its re-check (publish); returns the
        packet digest. facts: the v6 binding (wire, ids, evidence digest),
        recorded beside the egress row and in the reasoning.request event."""
        # One encoding for the digest (== digest(packet)) and the admission size.
        body=canonical(packet).encode();rid=context['request_id']
        packet_hash=hashlib.sha256(body).hexdigest()
        with self.store.transaction() as db:
            prior=db.execute('SELECT * FROM lab_jev_egress WHERE request_id=?',(rid,)).fetchone()
            if prior:
                if (prior['packet_digest']!=packet_hash or prior['revision']!=policy['revision']
                        or prior['generation']!=policy['generation']):
                    raise ValueError('Changed egress authorization')
                if phase=='send':raise ValueError('Provider dispatch already admitted; do not repeat')
            elif phase=='publish':
                raise ValueError('No admitted provider dispatch')
            else:
                # The reasoning scheduler persists dispatching before this
                # callback. An unresolved earlier session cannot be bypassed by
                # creating a new session or restarting the controller.
                current=db.execute('SELECT session_id,state FROM lab_reasoning_requests WHERE id=?',(rid,)).fetchone()
                if not current or current['state']!='dispatching' or current['session_id']!=context['session_id']:
                    raise ValueError('Durable reasoning reservation required')
                if db.execute("SELECT 1 FROM lab_reasoning_requests WHERE state='indeterminate' OR (state='dispatching' AND session_id!=?) LIMIT 1",(context['session_id'],)).fetchone():
                    raise ValueError('Unresolved provider execution')
                count=db.execute('SELECT count(*) FROM lab_jev_egress WHERE admitted_at>?',(now-60,)).fetchone()[0]
                if count>=policy['max_calls_per_minute']:raise ValueError('Provider rate budget exhausted')
                if 'max_session_calls' in policy:
                    limit=policy['max_session_calls']
                    if type(limit) is not int or not 1<=limit<=600:raise ValueError('Invalid session call budget')
                    used=db.execute('''SELECT count(*) FROM lab_jev_egress e JOIN lab_reasoning_requests r
                        ON r.id=e.request_id WHERE r.session_id=?''',(context['session_id'],)).fetchone()[0]
                    if used>=limit:raise ValueError('Provider session budget exhausted')
                self.store.check_capacity(len(body)+65536)
                db.execute('INSERT INTO lab_jev_egress(request_id,packet_digest,admitted_at,revision,generation) VALUES(?,?,?,?,?)',
                    (rid,packet_hash,now,policy['revision'],policy['generation']))
                event={'request_id':rid,'arm':context['arm'],'generation':context['generation'],
                    'available_at':now,'packet_sha256':packet_hash,'request':packet,
                    'status':'admitted_before_transport'}
                if facts is not None:
                    # A side table, not new lab_jev_egress columns: older
                    # releases insert five positional values there, so a
                    # rollback keeps working; old rows need no migration.
                    db.execute('''CREATE TABLE IF NOT EXISTS lab_jev_egress_facts(
                        request_id TEXT PRIMARY KEY, wire_version TEXT NOT NULL,
                        evidence_digest TEXT NOT NULL, timeline_verified TEXT NOT NULL)''')
                    db.execute('INSERT INTO lab_jev_egress_facts VALUES(?,?,?,?)',
                        (rid,facts['wire_version'],facts['evidence_digest'],facts['timeline_verified']))
                    event.update(facts)
                self.store._event(db,context['session_id'],'reasoning.request',event)
        return packet_hash

    # --- Packet v6 (den-facts-6, -7, -8): facts-only wire, local sidecar -------

    def _facts(self, context, packet, phase, local, policy, sources, now):
        """Egress of a facts-only packet: the wire grant (its own schema id in
        structured_sources), then (at send) the independent schema walk,
        registered questions, per-block grants, sidecar provenance, digest
        binding, ledger re-derivation and the recompute; then durable
        admission exactly as v5."""
        wire=packet['state']['schema']
        if wire not in sources:
            raise ValueError('Packet wire not permitted')
        if (phase not in ('send','publish') or packet.get('model')!=policy['model_selector']
                or context.get('site_id')!=policy['site_id'] or context.get('wire_version')!=wire):
            raise ValueError('Structured source permission mismatch')
        rid=context.get('request_id')
        admitted=None
        if phase=='publish':
            with self.verified_lock:admitted=self.verified.get(rid)
        if admitted is not None and admitted==(digest(packet),context.get('evidence_digest')):
            outcome=None
        else:
            outcome=self.verify_facts(context,packet,local,policy,sources)
        facts=None
        if phase=='send':
            facts={'wire_version':wire,'evidence_ids':list(context['evidence_ids']),
                   'timeline_ids':list(context['timeline_ids']),'evidence_digest':context['evidence_digest'],
                   'timeline_verified':outcome['timeline_verified']}
        packet_hash=self._admit(context,packet,phase,policy,now,facts=facts)
        if phase=='send':
            self._remember(rid,(packet_hash,context['evidence_digest']))
        return self._granted(policy,now)

    def verify_facts(self, context, packet, local, policy, sources):
        """Steps 3-9 of the v6 check (plan §3.3). Raises ValueError, else
        {'timeline_verified': 'full'|'partial', 'rederived': n}."""
        from jev_wire import (DEVICE_COVERAGE, DEVICE_FIELDS, MAX_SIDECAR_RECORDS, QUESTION_SETS, TIMELINE_MAX,
                              question_wire_ok,
                              build_state, validate_state)
        mismatch=ValueError('Structured source permission mismatch')
        if not isinstance(packet,dict) or set(packet)!={'model','state','questions'}:
            raise mismatch
        state=packet['state']
        # 3. Independent schema walk: closed vocabulary, no numbers anywhere,
        # against the registered schema of the wire the context names.
        try:
            if not isinstance(state,dict) or state.get('schema')!=context.get('wire_version'):
                raise ValueError()
            validate_state(state)
        except ValueError:
            raise mismatch from None
        # 4. The question set is the registered one for its version.
        questions=QUESTION_SETS.get(context.get('question_version'))
        if (questions is None or canonical(packet['questions'])!=canonical(questions)
                or not question_wire_ok(context.get('question_version'),state['schema'])):
            raise mismatch
        # 5. Grants per block.
        if 'qwen' in state and QWEN_EGRESS not in sources:
            raise ValueError('Qwen scene egress not permitted')
        if 'vjepa' in state and VJEPA_EGRESS not in sources:
            raise ValueError('V-JEPA scene egress not permitted')
        if 'ambient' in state and policy.get('ambient_context')!=AMBIENT_EGRESS:
            raise ValueError('Ambient context egress not permitted')
        for fact,name in DEVICE_FIELDS.items():
            value=(state.get('devices') or {}).get(name)
            if value is not None and value not in DEVICE_COVERAGE and not fact_granted(fact,policy):
                raise ValueError('Owner presence egress not permitted' if fact=='owner_home' else 'Device context egress not permitted')
        # 6. Sidecar shape and provenance, as v5 checks state.evidence.
        if (not isinstance(local,dict) or set(local)!={'records','timeline','shared','context'}
                or not isinstance(local['records'],list) or not isinstance(local['timeline'],list)
                or not isinstance(local['shared'],list) or len(local['records'])>MAX_SIDECAR_RECORDS
                or len(local['timeline'])>TIMELINE_MAX or len(local['shared'])>len(SHARED_KINDS)
                or not all(isinstance(r,dict) for r in local['records']+local['shared'])):
            raise mismatch
        records,timeline,shared,block=local['records'],local['timeline'],local['shared'],local['context']
        if block is not None:
            try:
                if project_context(block)!=block:raise ValueError()
            except ValueError:
                raise mismatch from None
        items=records+shared
        if (any(r.get('source_kind') not in ('perception.output','ha.observation') for r in records)
                or any(r.get('source_kind') not in SHARED_KINDS for r in shared)
                or len({r.get('source_kind') for r in shared})!=len(shared)
                or any(SHARED_KINDS[r['source_kind']] not in sources for r in shared)
                or project(items,ha_bindings=self.ha_bindings,zone_bindings=self.zone_bindings)!=items
                or any(r.get('site_id')!=policy['site_id'] or r.get('camera_id') not in policy['camera_ids']
                       or r.get('session_id')!=context.get('session_id') or r.get('arm')!=context.get('arm')
                       or r.get('generation')!=context.get('generation') or not opaque_provenance(r) for r in items)
                or any(not isinstance(e,dict) or set(e)!=TIMELINE_KEYS or not RECORD_ID.fullmatch(str(e['id'])) for e in timeline)):
            raise mismatch
        coverage=(block or {}).get('coverage') or {}
        if coverage.get('ha')=='withheld_by_experiment' and any('ha' in r for r in records):
            raise mismatch
        # A withheld or absent temporal_encoder family carries no V-JEPA record.
        if (any(r.get('source_kind')=='vjepa.observation' for r in shared)
                and coverage.get('temporal_encoder') not in ('supplied','stale','invalid')):
            raise mismatch
        # Room facts ride only a room wire; every other wire's sidecar never
        # carries them (its builder drops them).
        if any(r.get('source_kind')=='room.facts' for r in shared) and state['schema'] not in ROOM_WIRES:
            raise mismatch
        carried={fact for r in records if isinstance(r.get('ha'),dict) for fact in r['ha']}
        if 'owner_home' in carried and not fact_granted('owner_home',policy):
            raise ValueError('Owner presence egress not permitted')
        if any(f in FACT_GRANTS and not fact_granted(f,policy) for f in carried):
            raise ValueError('Device context egress not permitted')
        # 7. Binding of the sidecar to the admitted request.
        if (digest(local)!=context.get('evidence_digest')
                or [str(r['id']) for r in records]!=context.get('evidence_ids')
                or [str(e['id']) for e in timeline]!=context.get('timeline_ids')):
            raise mismatch
        # 8. Ledger re-derivation of each sidecar record, room facts record
        # and timeline frame.
        verified,rederived=self._rederive(context,records,timeline,block,
                                          [r for r in shared if r.get('source_kind')=='room.facts'])
        # 9. Recompute the state from the sidecar.
        cutoff=context.get('observed_at')
        try:
            expected=build_state(local,cutoff=cutoff,storyline_seconds=context.get('storyline_seconds'),
                                 budget_level=context.get('budget_level'),monitor_side=self.monitor_side,
                                 wire_version=state['schema'],desk_seat=self.desk_seat)
        except (ValueError,KeyError,TypeError):
            raise mismatch from None
        if canonical(expected)!=canonical(state):
            raise mismatch
        if 'ambient' in state:
            # Clock facts re-derived from the cutoff and this gate's own zone.
            from ambient_context import facts, zone
            ambient=block['ambient']
            if ambient!=facts(cutoff,zone(self.site_timezone),daylight=ambient['daylight'],weather=ambient['weather']):
                raise mismatch
        return {'timeline_verified':verified,'rederived':rederived}

    def _rederive(self, context, records, timeline, block, rooms=()):
        """Re-derive each not yet verified sidecar item from its ledger row
        (observation_record -> arm copy -> admitted fields -> project) and
        require equality. Records and room facts records are always verified;
        timeline frames at most FRAME_REDERIVE_PER_CALL per request (newest
        first), any remainder 'partial'."""
        from jev_wire import frame_facts
        from persisted_evidence import observation_record
        from reasoning import _ADMITTED_FIELDS
        mismatch=ValueError('Structured source permission mismatch')
        sid,arm,generation=context.get('session_id'),context.get('arm'),context.get('generation')
        todo=[]
        with self.frames_lock:
            for tag,item in [('record',r) for r in records]+[('room',r) for r in rooms]+[('frame',e) for e in timeline]:
                key=(sid,arm,generation,tag,str(item['id']))
                if self.frames.get(key)!=digest(item):
                    todo.append((key,tag,item))
        # Newest first: the current storyline segment rests on the newest
        # frames; an older remainder is left 'partial' for the offline audit.
        frames=[t for t in todo if t[1]=='frame'][::-1]
        deferred=frames[FRAME_REDERIVE_PER_CALL:]
        todo=[t for t in todo if t[1] in ('record','room')]+frames[:FRAME_REDERIVE_PER_CALL]
        if todo:
            session=self.store.get_session(sid)
            dropped=[f for f,v in ((block or {}).get('ha_facts') or {}).items() if v!='supplied']
            bound=dict(ha_bindings=granted_bindings(self.ha_bindings,dropped),zone_bindings=self.zone_bindings)
            kinds=('perception.output','ha.observation')+(('room.facts',) if rooms else ())
            rows=self.store.events_by_ids(sid,[t[2]['id'] for t in todo],kinds)
            for key,tag,item in todo:
                row=rows.get(str(item['id']))
                if row is None:raise mismatch
                kind,value=row
                base=observation_record(item['id'],kind,value,session)
                if (base is None or (tag=='frame' and kind!='perception.output') or ((tag=='room')!=(kind=='room.facts'))
                        or (kind in ('perception.output','room.facts')
                            and (base.get('arm')!=arm or base.get('generation')!=generation))):
                    raise mismatch
                rebuilt={k:v for k,v in {**base,'arm':arm,'generation':generation}.items() if k in _ADMITTED_FIELDS}
                projected=project([rebuilt],**bound)
                if len(projected)!=1:raise mismatch
                if (frame_facts(projected[0]) if tag=='frame' else projected[0])!=item:
                    raise mismatch
            with self.frames_lock:
                for key,tag,item in todo:
                    self.frames[key]=digest(item)
                    self.frames.move_to_end(key)
                while len(self.frames)>FRAME_MEMO_ITEMS:
                    self.frames.popitem(last=False)
        return ('partial' if deferred else 'full'),len(todo)

    def shadow_verify(self, context, packet, local):
        """Shadow mode: the v6 checks beside a live v5 packet. Never admits,
        records egress or sends; returns a content-free verdict. The wire
        grant is reported, not required: the owner has not switched yet."""
        try:
            policy,sources,_=self._current()
        except Exception:
            return {'match':False,'reason':'policy_unavailable','wire_granted':False}
        state=(packet or {}).get('state') if isinstance(packet,dict) else None
        granted=(state.get('schema') if isinstance(state,dict) else WIRE_VERSION) in sources
        try:
            outcome=self.verify_facts(context,packet,local,policy,sources)
        except ValueError as error:
            return {'match':False,'reason':SHADOW_REASONS.get(str(error),'verification_failed'),'wire_granted':granted}
        except Exception:
            return {'match':False,'reason':'verification_failed','wire_granted':granted}
        return {'match':True,'reason':None,'wire_granted':granted,**outcome}
