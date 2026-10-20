"""Explicit manual successor uses the same idle actual acquisition factory."""
from copy import deepcopy
from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
from controlled_external_io import build_base_configuration, CLARIFICATION_SCENARIOS
from controlled_session import ControlledSession, load_prepared_room
from room_configuration import upgrade_configuration


def bundle_for_manual():
    root=Path(__file__).resolve().parents[2]
    bundle=build_base_configuration(session_ref={'site_id':'authored-site','room_id':'authored-room',
        'session_id':'authored-manual-session','domain_epoch':'authored-manual-domain'},node_runtime=shutil.which('node'),clarification=True,manual=True)
    room=json.loads(subprocess.check_output([shutil.which('node'),'-e',
        "process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"],cwd=root,text=True))
    room['registration']=deepcopy(bundle['worker_config']['delivery_policy']['registration'])
    now=time.time()
    def current(value):
        if isinstance(value,dict):
            for key in value:
                if key=='valid_until':value[key]=now+180
                else:current(value[key])
        elif isinstance(value,list):
            for row in value:current(row)
    current(room)
    method=next(m for m in next(iter(bundle['configuration']['manifest']['sources'].values()))['method_refs']
                if m['id']=='controlled-authored-holding-method')
    clarification=dict(schema='home-live-clarification-policy-1',version=1,proposal_ttl_s=30,max_pending=1,max_history=128,
        repeat_policy='one_per_scope',material_methods=[deepcopy(method)],interaction_methods=[deepcopy(method)])
    manual=dict(schema='home-live-manual-policy-1',version=1,policy_ref={'id':'authored-manual-policy','digest':'d'*64},
        room_policy_ref=deepcopy(room['policy_ref']),valid_until=now+180,max_duration_s=120,operating_not_after=now+120,
        max_active_holds=28,max_pending_per_domain=1,intent_capacity=128,
        resource_limits={'derived_event_bytes':262144,'worker_commit_bytes':1048576})
    bundle['configuration'],bundle['worker_config']=upgrade_configuration(bundle['configuration'],bundle['worker_config'],room,
        clarification_policy=clarification,manual_policy=manual)
    return bundle


class ControlledManualTests(unittest.TestCase):
    def test_prepared3_and_scenario_roster_require_the_complete_explicit_manual_profile(self):
        bundle=bundle_for_manual()
        self.assertEqual(bundle['external_io'].status()['scenarios'],list(CLARIFICATION_SCENARIOS)+['perception_unavailable'])
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            prepared=dict(schema='home-den-controlled-room-3',version=3,pack_directory=str(Path(directory).resolve()),
                asset_selection={},resident_binding={'schema':'home-live-resident-binding-10','version':10},
                room_policy=bundle['worker_config']['room_policy'],source_bundle_digest='c'*64,acquisition_binding={},resident_assets={},
                resident_source_paths=[],clarification_policy=bundle['worker_config']['clarification_policy'],manual_policy=bundle['worker_config']['manual_policy'])
            target=Path(directory).resolve()/'prepared.json';target.write_text(json.dumps(prepared))
            self.assertEqual(load_prepared_room(target),prepared)
            for change in (lambda p:p.pop('manual_policy'),lambda p:p.update(version=2,schema='home-den-controlled-room-2'),
                           lambda p:p['resident_binding'].update(version=9)):
                wrong=deepcopy(prepared);change(wrong);target.write_text(json.dumps(wrong))
                with self.assertRaisesRegex(ValueError,'prepared_room_refused'):load_prepared_room(target)

    def test_actual_factory_manual_hold_and_return_survive_unsupported_perception_and_replay(self):
        from live_context_edge import binding_digest
        with ExitStack() as cleanup:
            directory=Path(__file__).resolve().parents[2]/'artifacts'/('controlled-manual11-'+uuid.uuid4().hex)
            directory.mkdir(parents=True)
            print('authored manual11 evidence:',directory,flush=True)
            bundle=bundle_for_manual();bundle['external_io'].set_scenario('perception_unavailable')
            session=ControlledSession(bundle=bundle,output_directory=directory,node_runtime=shutil.which('node'),source_bundle_digest='c'*64)
            cleanup.callback(session.close)
            edge=session.start_edge();context=ssl.create_default_context(cafile=edge['ca_file'])
            headers={'Authorization':'Bearer '+Path(edge['token_file']).read_text(),'X-Home-Context-Binding':binding_digest(edge['binding'])}
            def request(method,route,body=None):
                connection=http.client.HTTPSConnection('127.0.0.1',int(edge['url'].rsplit(':',1)[1]),context=context,timeout=3)
                try:
                    connection.request(method,route,json.dumps(body) if body is not None else None,
                        headers={**headers,**({'Content-Type':'application/json'} if body is not None else {})})
                    response=connection.getresponse();return response.status,json.loads(response.read())
                finally:connection.close()
            query='?site_id=authored-site&room_id=authored-room'
            def latest():
                code,snapshot=request('GET','/api/home-context/v1/snapshot'+query)
                self.assertEqual(code,200,snapshot)
                return next((p['payload'] for p in snapshot['items'] if p['payload_type']=='home-live-shadow-decision-10'),None)
            session.control({'id':'manual-start','operation':'start'})
            deadline,decision=time.monotonic()+12,None
            while time.monotonic()<deadline:
                if session.status()['status']=='running':
                    decision=latest()
                    if decision:break
                time.sleep(.05)
            self.assertIsNotNone(decision,session.status())
            control=decision['room_control'];owner=control['domains'][0];now=time.time()
            intent=dict(schema='home-live-intent-event-4',version=4,id='intent:controlled-manual-hold',**session.identity,
                manifest_ref=deepcopy(bundle['configuration']['manifest_ref']),actor_ref='controlled-owner',origin='human_preference',truth_role='runtime_intent',
                kind='manual_ambient_hold',target={k:owner[k] for k in ('fixture_id','domain')},set_at=now,available_at=now,valid_until=now+4,
                value=dict(policy_ref=control['policy_ref'],expected_generation=owner['generation'],expected_control_generation=control['control_generation'],
                    expected_control_id=None,brightness_fraction=.1,cct_kelvin=3200,duration_s=10),reply_to=None)
            code,receipt=request('POST','/api/home-context/v1/intent-events',intent);self.assertEqual(code,200,receipt)
            deadline,held=time.monotonic()+8,None
            while time.monotonic()<deadline:
                decision=latest()
                if decision:
                    held=decision['room_control']['domains'][0]
                    sample=decision['room_output']['fixtures'][0]['domains'][0]['sample']
                    if held['mode']=='manual' and sample and sample['brightness_fraction']>0:break
                time.sleep(.05)
            self.assertEqual(held['mode'],'manual',session.status())
            self.assertIsNotNone(sample)
            self.assertGreater(sample['brightness_fraction'],0)
            self.assertEqual(held['hold']['hold_until'],receipt['committed_at']+10)
            self.assertIsNone(decision['room_output']['task']['fixture_id'])
            now=time.time();release=deepcopy(intent);release.update(id='intent:controlled-return',kind='return_ambient_auto',set_at=now,available_at=now,valid_until=now+4)
            release['value']={k:intent['value'][k] for k in ('policy_ref','expected_control_generation')}
            release['value'].update(expected_generation=held['generation'],expected_control_id=intent['id'])
            code,result=request('POST','/api/home-context/v1/intent-events',release);self.assertEqual(code,200,result)
            deadline=time.monotonic()+6
            while time.monotonic()<deadline:
                decision=latest()
                if decision and decision['room_control']['domains'][0]['mode']=='auto':break
                time.sleep(.05)
            self.assertEqual(decision['room_control']['domains'][0]['mode'],'auto')
            session.control({'id':'manual-stop','operation':'stop'})
            final=session.status(results=True)
            self.assertEqual(final['status'],'stopped',final)
            self.assertEqual(final['ordinary_replay']['status'],'reproduced',final)
            from archive import canonical,digest
            originals=[json.loads(line) for line in session.archive_file.read_text(encoding='utf-8').splitlines()]
            def replay_mutation(name,records,expected_code=2):
                previous=None
                for seq,record in enumerate(records):
                    record.update(seq=seq,previous_digest=previous)
                    record.pop('digest',None);record['digest']=digest(record);previous=record['digest']
                target=directory/name;target.write_text(''.join(canonical(r)+'\n' for r in records),encoding='utf-8')
                run=subprocess.run([shutil.which('node'),str(Path(__file__).parent/'replay.cjs'),str(target)],
                    capture_output=True,text=True,timeout=12)
                self.assertEqual(run.returncode,expected_code,run.stdout)
                result=json.loads(run.stdout)
                return result['reason'] if expected_code else result['status']
            checkpoint=next(r['record']['data']['prepared']['checkpoint'] for r in originals if r['record']['kind']=='worker_prepared')
            forged=deepcopy(originals);index=next(i for i,r in enumerate(forged) if r['record']['kind']=='worker_cut')
            forged.insert(index,{'record':{'kind':'worker_restore','data':{'checkpoint':checkpoint,'t':checkpoint['last_committed_at'],'allow_expired':False}},
                'schema':'home-live-session-record-1','version':1})
            self.assertEqual(replay_mutation('forged-checkpoint-originals.jsonl',forged),'archive_restore_checkpoint_original_missing')
            retire_index=next(i for i,r in enumerate(originals) if r['record']['kind']=='journal_prepare'
                and any(c['operation']['operation']=='retire' for c in r['record']['data'].get('control_operations',[])))
            truncated=deepcopy(originals[:retire_index]+[originals[-1]])
            self.assertEqual(replay_mutation('missing-retirement-originals.jsonl',truncated),'archive_control_retirement_missing')
            resurrected=deepcopy(originals)
            resurrected.insert(len(resurrected)-1,{'record':{'kind':'worker_restore','data':{
                'checkpoint':checkpoint,'t':checkpoint['last_committed_at'],'allow_expired':True}},
                'schema':'home-live-session-record-1','version':1})
            self.assertEqual(replay_mutation('late-restored-owner-originals.jsonl',resurrected),'archive_restore_recovery_unconfirmed')
            recovered=deepcopy(originals)
            cut_indexes=[i for i,r in enumerate(recovered) if r['record']['kind']=='worker_cut']
            self.assertGreaterEqual(len(cut_indexes),2)
            recovered.insert(cut_indexes[1],{'record':{'kind':'worker_restore','data':{
                'checkpoint':checkpoint,'t':checkpoint['last_committed_at'],'allow_expired':True}},
                'schema':'home-live-session-record-1','version':1})
            self.assertEqual(replay_mutation('known-checkpoint-fresh-cut-originals.jsonl',recovered,0),'reproduced')
            retirement_tx=originals[retire_index]['record']['data']['tx_id']
            retirement_records=[r for r in originals if r['record']['kind'] in {'journal_prepare','journal_commit'}
                and r['record']['data'].get('tx_id')==retirement_tx]
            self.assertEqual(len(retirement_records),2)
            last_active=next(r for r in reversed(originals[:retire_index]) if r['record']['kind']=='worker_prepared'
                and r['record']['data']['prepared']['checkpoint']['control_active'])
            out_of_order=deepcopy([r for r in originals if r not in retirement_records])
            prepared_index=next(i for i,r in enumerate(out_of_order) if r==last_active)
            out_of_order[prepared_index+1:prepared_index+1]=deepcopy(retirement_records)
            self.assertEqual(replay_mutation('retirement-before-acceptance-originals.jsonl',out_of_order),'archive_control_commit_authority_mismatch')


if __name__=='__main__':unittest.main()
