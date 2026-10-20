"""One bounded two-subject call through incumbent JEV authorization/transport."""
from copy import deepcopy
import json
import re
import time
from reasoning import ACTIVITIES,canonical,digest,finite,validate_jev_response
from jev_provider import JevProvider,ProviderFailure,post_systemone
from jev_person_source import freeze_person_context,validate_person_context
import jev_person_wire as person_wire


class JevPersonProvider(JevProvider):
    person_mode=True
    freeze_person_context=staticmethod(freeze_person_context)

    def __init__(self,*,model_selector,response_version,authorize,credential,transport=post_systemone,
                 clock=time.time,wire=person_wire.SCHEMA,**legacy_options):
        if model_selector!=person_wire.MODEL or response_version!=person_wire.MODEL or wire!=person_wire.SCHEMA:
            raise ValueError('exact_person_model_and_wire_required')
        super().__init__(model_selector=model_selector,response_version=response_version,authorize=authorize,
                         credential=credential,transport=transport,clock=clock,wire=None,**legacy_options)
        self.wire=wire

    def facts_key(self,records,*,site_id,session_id,arm,generation,cutoff,window_seconds=10,
                  shared_context=None,context_snapshot=None,person_context=None):
        scope=dict(site_id=site_id,session_id=session_id,arm=arm,generation=generation,cutoff=cutoff)
        frozen=freeze_person_context(records,**scope) if person_context is None else validate_person_context(person_context,**scope)
        if frozen is None:
            return dict(key=digest(['person_unavailable',site_id,session_id,arm,generation]),people='unknown',camera='unavailable',
                        cue_state='person_cues_not_measured',person_context=None)
        packet=person_wire.build_packet(frozen,model=self.model)
        return dict(key=digest([packet,frozen]),people='one' if len(frozen['slots'])==1 else 'multiple',camera='current',
                    cue_state='person_cues_not_measured',person_context=deepcopy(frozen))

    def evaluate(self,records,*,request_id,site_id,session_id,arm,generation,cutoff,deadline,window_seconds=10,
                 shared_context=None,context_snapshot=None,person_context=None):
        now=self.clock();scope=dict(site_id=site_id,session_id=session_id,arm=arm,generation=generation,cutoff=cutoff)
        try:
            if (type(request_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',request_id)
                    or arm!='b' or not all(finite(v) for v in (now,cutoff,deadline))
                    or not cutoff<=now<deadline<=cutoff+15):raise ValueError('request_invalid')
            frozen=(freeze_person_context(records,**scope) if person_context is None
                    else validate_person_context(person_context,**scope))
            if frozen is None:
                return dict(status='missing_evidence',subjects=[],person_context=None,actuation=False)
            if records and canonical(freeze_person_context(records,**scope))!=canonical(frozen):
                raise ValueError('person_context_changed')
            packet=person_wire.build_packet(frozen,model=self.model)
        except (ValueError,KeyError,TypeError,RecursionError):
            raise ProviderFailure('request_invalid') from None
        original=frozen['packet']
        context=dict(request_id=request_id,**scope,deadline=deadline,wire_version=person_wire.SCHEMA,
                     question_version=person_wire.QUESTION_VERSION,response_version=self.version,
                     observed_at=original['observed_at'],available_at=original['available_at'],valid_until=original['valid_until'],
                     evidence_ids=[str(frozen['visual_event_id']),str(frozen['semantics_event_id'])],timeline_ids=[],
                     evidence_digest=digest(frozen),input_digest=digest(packet))
        grant=self._grant(context,packet,'send',frozen)
        try:
            token=self.credential()
            if type(token) is not str or not 0<len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError()
        except Exception:
            raise ProviderFailure('provider_credential_unavailable') from None
        sending=self.clock();remaining=deadline-sending
        if not finite(sending) or remaining<=0 or sending>=frozen['packet']['valid_until']:
            return dict(status='expired',subjects=[],person_context=frozen,provenance=context,actuation=False)
        try:
            raw=self.transport(canonical(packet).encode(),token,remaining)
        except ProviderFailure:
            raise
        except Exception:
            raise ProviderFailure('provider_transport_indeterminate',indeterminate=True) from None
        received=self.clock()
        if not finite(received) or received>deadline:
            return dict(status='expired',subjects=[],person_context=frozen,provenance=context,actuation=False)
        if self._grant(context,packet,'publish',frozen)!=grant:
            raise ProviderFailure('authorization_changed')
        try:
            if type(raw) is not bytes or len(raw)>65536:raise ValueError()
            def unique(pairs):
                result={}
                for key,value in pairs:
                    if key in result:raise ValueError('duplicate_response_key')
                    result[key]=value
                return result
            def constant(_):raise ValueError('nonfinite_response')
            response=validate_jev_response(json.loads(raw,object_pairs_hook=unique,parse_constant=constant),packet['questions'],expected_version=self.version)
        except Exception:
            raise ProviderFailure('provider_response_invalid') from None
        subjects=[];gates={}
        for slot in frozen['slots']:
            answer=deepcopy(response['answers']['activity_'+slot['subject_slot']])
            withheld=packet['state']['subjects'][slot['subject_slot']]['coverage']=='unavailable'
            if withheld:
                answer.update(choice='insufficient_evidence',confidence=0.,probabilities={key:float(key=='insufficient_evidence') for key in ACTIVITIES})
                gates[slot['subject_slot']]='source_evidence_unavailable'
            subjects.append(dict(**deepcopy(slot),status='abstain' if answer['choice']=='insufficient_evidence' else 'ready',
                judgment=dict(activity=answer['choice'],confidence=answer['confidence'],probabilities=answer['probabilities'],model_version=response['model'])))
        usage=response.get('usage',{});usage={k:usage[k] for k in ('input_tokens','output_tokens') if type(usage.get(k)) is int and usage[k]>=0} if isinstance(usage,dict) else {}
        return dict(status='ready' if any(s['status']=='ready' for s in subjects) else 'abstain',subjects=subjects,
                    person_context=deepcopy(frozen),provenance=dict(context,authorization_revision=grant[0],authorization_generation=grant[1]),
                    received_at=received,diagnostics=dict(request=packet,answers=response['answers'],usage=usage,
                        subject_gates=gates,
                        usage_status='reported' if len(usage)==2 else 'not_reported',wire_version=person_wire.SCHEMA,
                        question_version=person_wire.QUESTION_VERSION,packet_bytes=len(canonical(packet).encode())),actuation=False)
