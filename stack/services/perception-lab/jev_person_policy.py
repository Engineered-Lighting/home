"""Independent ledger re-derivation for the opt-in person wire; no egress."""
from reasoning import canonical,digest,finite
from jev_policy import LabJevPolicy
from jev_person_source import freeze_person_context
import jev_person_wire as wire


class PersonJevPolicy(LabJevPolicy):
    def __call__(self,context,packet,phase,local=None):
        # Keep source verification and durable admission under the same owner
        # lock. _admit opens its own transaction, not a nested transaction.
        with self.store.lock:
            policy,sources,now=self._current()
            if wire.SCHEMA not in sources or 'lab-live-person-scene-1' not in sources:
                raise ValueError('Packet wire not permitted')
            if (phase not in ('send','publish') or type(context) is not dict or type(packet) is not dict
                    or set(packet)!={'model','state','questions'} or context.get('wire_version')!=wire.SCHEMA
                    or packet.get('model')!=wire.MODEL or policy['model_selector']!=wire.MODEL
                    or context.get('response_version')!=wire.MODEL or context.get('site_id')!=policy['site_id']
                    or context.get('arm')!='b' or not finite(context.get('cutoff'))
                    or not finite(context.get('deadline')) or not context['cutoff']<=now<context['deadline']<=context['cutoff']+15):
                raise ValueError('Structured source permission mismatch')
            session=self.store.get_session(context['session_id'])
            generation=self.store.db.execute('SELECT generation FROM lab_experiment_state WHERE session_id=?',(session['id'],)).fetchone()
            if (session['state']!='recording' or session.get('reasoning_enabled') is not True
                    or session['site_id']!=policy['site_id'] or session['camera_id'] not in policy['camera_ids']
                    or generation is None or generation[0]!=context.get('generation')):
                raise ValueError('Session reasoning grant revoked')
            rows=self.store.db.execute("SELECT id,kind,payload FROM events WHERE session_id=? "
                "AND kind IN ('perception.output','qwen.person_observation') ORDER BY id DESC LIMIT 512",(session['id'],)).fetchall()
            import json
            records=[dict(id=row['id'],kind=row['kind'],payload=json.loads(row['payload']),session_id=session['id']) for row in rows]
            scope={k:context[k] for k in ('site_id','session_id','arm','generation','cutoff')}
            frozen=freeze_person_context(records,**scope)
            if (frozen is None or canonical(frozen)!=canonical(local) or digest(frozen)!=context.get('evidence_digest')
                    or context.get('input_digest')!=digest(packet)
                    or context.get('evidence_ids')!=[str(frozen['visual_event_id']),str(frozen['semantics_event_id'])]
                    or context.get('timeline_ids')!=[] or not now<frozen['packet']['valid_until']):
                raise ValueError('Structured source permission mismatch')
            # Recheck the actual current original and association, even after a
            # prior successful send; a cached packet digest is not freshness.
            from qwen_person_live import PersonFrames,membership
            evidence=frozen['packet']['evidence']
            pool=PersonFrames(self.store,arm='b').candidates(session,now)
            original=next((f for f in pool if f['media_id']==evidence['media_id']
                           and f['frame_id']==evidence['frame_id'] and f['sha256']==evidence['sha256']),None)
            if original is None:
                raise ValueError('Structured source permission mismatch')
            current=membership(self.store,session,original,now,arm='b')
            expected=[dict(subject_slot='person_'+str(i),instance_id=p['instance_id'],track_ref=p['track_ref'])
                      for i,p in enumerate(current['subjects'],1)]
            if canonical(current['basis'])!=canonical(frozen['packet']['basis']) or expected!=frozen['slots']:
                raise ValueError('Structured source permission mismatch')
            wire.validate_state(packet['state'])
            if canonical(wire.build_packet(frozen))!=canonical(packet):
                raise ValueError('Structured source permission mismatch')
            if phase=='send':
                # The scheduler must have durably frozen this same private cut.
                reserved=self.store.db.execute('SELECT payload FROM lab_reasoning_requests WHERE id=?',(context['request_id'],)).fetchone()
                if reserved is None or canonical(json.loads(reserved[0]).get('person_context'))!=canonical(frozen):
                    raise ValueError('Durable reasoning reservation required')
            facts=dict(wire_version=wire.SCHEMA,evidence_ids=list(context['evidence_ids']),timeline_ids=[],
                       evidence_digest=context['evidence_digest'],timeline_verified='full',person_context=frozen,
                       cutoff=context['cutoff'],deadline=context['deadline'])
            admission_now=self.store.clock()
            if (not finite(admission_now) or not context['cutoff']<=admission_now<
                    min(context['deadline'],frozen['packet']['valid_until'],policy['expires_at'])):
                raise ValueError('Structured source permission mismatch')
            self._admit(context,packet,phase,policy,admission_now,facts=facts if phase=='send' else None)
            return self._granted(policy,admission_now)
