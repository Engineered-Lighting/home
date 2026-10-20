"""Config9 journal authority: independent room samples plus exact task admission."""
from copy import deepcopy

from journal import canonical
from live_delivery import ConservativeLiveDeliveryWorkerBoundary
from live_worker import reject
from room_delivery import ROOM_SCHEMAS,ROOM_OUTPUTS,validate_room_output


class RoomLiveDeliveryWorkerBoundary(ConservativeLiveDeliveryWorkerBoundary):
    schema_types=ConservativeLiveDeliveryWorkerBoundary.schema_types|ROOM_SCHEMAS
    output_types=ROOM_OUTPUTS
    decision_type='home-live-shadow-decision-8'
    checkpoint_type='home-live-worker-checkpoint-8'
    commit_type='home-live-worker-commit-8'
    cut_type='home-live-input-cut-8'
    version=8
    worker_version='home-live-context-worker-8.0.0'
    module_names=ConservativeLiveDeliveryWorkerBoundary.module_names|{
        'app/src/home-live-room-composition.js','app/src/home-live-room-allocation.js'}

    def configuration_description(self,nested):
        value=super().configuration_description(nested)
        value.update(version=self.worker_version,room_policy=self.policy['room_policy'],
                     room_schema_pins=self.policy['room_schema_pins'],room_interface_digest=self.j.schemas.room_interface_digest)
        return value

    def _checkpoint(self,cp,commit,next_seq,old,now):
        if cp['room_schema_pins']!=self.policy['room_schema_pins']:
            reject('checkpoint_conflict','room checkpoint schema pins differ from the commissioned successor')
        return super()._checkpoint(cp,commit,next_seq,old,now)

    @staticmethod
    def _snapshot_for(room,fixture_id):
        if room['fallback_snapshot'] is not None:return room['fallback_snapshot']
        found=next((x['snapshot'] for x in room['tasks'] if x['fixture_id']==fixture_id),None)
        if found is None:reject('invalid_room_checkpoint','complete task snapshot identity required')
        return found

    def validate_room_output(self, **kwargs):
        return validate_room_output(**kwargs)

    def _room_fixtures(self):
        return self.policy['room_policy']['fixtures']

    def _task_inventory(self,fixtures):
        expected=[f['fixture_id'] for f in fixtures if f['task_policy'] is not None]
        if expected and len(expected)!=4:
            reject('invalid_room_checkpoint','task candidate inventory is zero or all four commissioned fixtures')
        return expected

    def settle(self,commit,grant,publications,decision,engagements,old):
        room=commit['checkpoint']['state']['room_execution']
        output=decision['room_output']
        tasks=room['tasks']
        fixtures=self._room_fixtures()
        expected=self._task_inventory(fixtures)
        if [x['fixture_id'] for x in tasks]!=expected or bool(tasks)==(room['fallback_snapshot'] is not None):
            reject('invalid_room_checkpoint','exact candidate snapshots or original dark fallback required')
        fixture_id=output['task']['fixture_id'] or room['last_selected_fixture_id'] or (expected[0] if expected else None)
        selected=next((f for f in fixtures if f['fixture_id']==fixture_id),None)
        task_policy=selected['task_policy'] if selected and selected['task_policy'] is not None else self.policy['delivery_policy']
        virtual=self._snapshot_for(room,fixture_id)
        if virtual!=commit['checkpoint']['state']['virtual_execution']:
            reject('invalid_room_checkpoint','selected legacy task snapshot must be the same immutable room member')
        if any(output['task'][k]!=decision[k] for k in ('allocation','execution','display_lease')):
            reject('invalid_room_output','room task and legacy committed execution must agree exactly')
        if decision['background_delivery']!={'status':'committed_room_output','revision_id':output['revision_id']}:
            reject('invalid_room_output','background pointer must name this committed room revision')
        previous=deepcopy(old) if old else None
        if previous:
            previous['state']['virtual_execution']=deepcopy(self._snapshot_for(old['state']['room_execution'],fixture_id))
        configured=self.delivery_policy
        try:
            # Scene/correction settlement remains ordinary authority. The inherited
            # task validator checks this member's independently pinned policy.
            self.delivery_policy=task_policy
            super().settle(commit,grant,publications,decision,engagements,previous)
            chosen_state=virtual['state']
            hazard_keys=('hazards','hazard_resolutions','clearance_settlements')
            for item in tasks:
                if item['fixture_id']==fixture_id:continue
                member=item['snapshot']
                if any(member['state'][key]!=chosen_state[key] for key in hazard_keys):
                    reject('invalid_room_checkpoint','all candidate states must preserve the same causal hazard history')
                candidate=next(f for f in fixtures if f['fixture_id']==item['fixture_id'])
                self.delivery_policy=candidate['task_policy']
                dark_commit=deepcopy(commit)
                dark_commit['checkpoint']['state']['virtual_execution']=deepcopy(member)
                dark_decision=deepcopy(decision)
                reason='room_fixture_not_selected'
                dark_decision.update(allocation={'status':'unavailable','reason':reason,'candidates':[],
                                                 'geometry_cut_digest':decision['input_cut']['cut_digest']},
                    execution={'status':'unavailable','reason':reason},
                    display_lease={'status':'withdrawn','sample_at':decision['t'],'valid_until':decision['t'],
                        'geometry_refs':[],'protection_refs':[],'authority_generation':decision['input_cut']['revocation_generation'],'reason':reason})
                dark_previous=deepcopy(old) if old else None
                if dark_previous:dark_previous['state']['virtual_execution']=deepcopy(self._snapshot_for(old['state']['room_execution'],item['fixture_id']))
                self._delivery(dark_commit,publications,dark_decision,dark_previous)
        finally:
            self.delivery_policy=configured
        rows=[]
        receipts={r['cursor']:r for r in commit['input_receipts']}
        for cursor,p in publications.items():
            payload=p['payload']
            if payload.get('source_id') not in self.ingress.sources:continue
            proof={k:p[k] for k in ('stream_epoch','cursor','producer_id','id','payload_digest')}
            proof['component_id']=None
            rows.append({'payload':payload,'publication_ref':proof,
                         'available_at':max(payload['available_at'],p['committed_at'],receipts[cursor]['received_at']),
                         'valid_until':min(payload['valid_until'],payload['observed_at']+self.ingress.sources[payload['source_id']]['max_age_s'])-payload['clock']['uncertainty_s']})
        try:
            self.validate_room_output(policy=self.policy['room_policy'],identity=self.ingress.identity,t=decision['t'],seq=decision['seq'],
                input_cut_digest=decision['input_cut']['cut_digest'],authority_generation=commit['checkpoint']['authority_generation'],
                source_generations=commit['checkpoint']['state']['engagement']['source_generations'],background=decision['background'],
                room_output=output,room_snapshot=room,prior_room_snapshot=old['state']['room_execution'] if old else None,
                publications=rows,max_output_age_s=self.policy['max_output_age_s'],lifecycle_policy=self.policy['lifecycle_policy'])
        except ValueError:
            reject('invalid_room_output','room samples do not match original support, modeled bounds and trusted prior state')
