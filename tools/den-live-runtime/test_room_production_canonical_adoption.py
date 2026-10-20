"""One actual Home source -> explicit production family, software only.

Only the coordinator executes this case under its pinned four-native/loopback
Job. External detector, P1/JEV, commissioning and clock inputs are authored.
Accepted worker commits, native boot/session, SDK grants, output acknowledgments,
protective Travel, original EOF/exit and both framed readers remain actual.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[2 if Path(__file__).parent.name=='den-live-runtime' else 3]
sys.path[:0]=[str(ROOT/'stack/services/perception-lab'),str(ROOT/'stack/services/home-context'),
              str(ROOT/'tools/den-live-runtime')]


def load_upstream():
    path=ROOT/'tools/fixtures/den-qualified-desk-source-runtime.py'
    spec=importlib.util.spec_from_file_location('production_home_desk_source',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class ProductionCanonicalAdoptionTests(unittest.TestCase):
    def wait_commit(self,session,commit,source_owner):
        fixed=source_owner.f.now;deadline=time.monotonic()+65
        while time.monotonic()<deadline:
            status=session.status()
            self.assertIsNone(status['error'],status)
            ref=status['last_commit_ref']
            if ref is not None and ref['commit_id']==commit['id'] and not status['pending']:
                self.assertEqual(source_owner.f.now,fixed,'waiting must not advance the original source clock')
                return status
            time.sleep(.01)
        self.fail('bounded actual production selected commit did not settle: '+json.dumps(session.status()))

    def test_same_home_context_boot_sample_cancellation_and_framed_replay(self):
        from live_den_mount import _runtime_module
        _runtime_module()
        from home_den_runtime.archive import Recorder
        from journal import digest
        from home_den_runtime.room_production_command_factory import ProductionCommandFactory,SCHEMA
        from room_production_canonical_provider import FAMILY
        from room_production_canonical_replay import verify_room_production_canonical_archive
        from room_production_spotlight_sample import TrustedCommittedSpotlightSample
        import room_production_canonical_provider as provider
        import test_room_command_session as incumbent

        evidence=Path(os.environ['DEN_PRODUCTION_ADOPTION_EVIDENCE_DIR'])
        allowed=ROOT/'artifacts/den-c12-production-consumer-20261019'
        self.assertTrue(evidence.is_absolute() and evidence.is_dir() and evidence.resolve().is_relative_to(allowed.resolve()))
        self.assertFalse(any(p.is_symlink() for p in (evidence,*evidence.parents)))
        self.assertFalse(any(evidence.iterdir()),'fresh bounded coordinator evidence required')
        for name in ('ledgers','approvals'):(evidence/name).mkdir()
        holder={};timeline=[];source_owner=None;terminal=None;source_cleanup=None;source_quiescence=None
        source_bundle=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

        def on_graph(owner,graph,worker):
            self.assertNotIn('session',holder)
            holder['source_owner']=owner
            self.assertEqual(int(graph.journal._meta('cursor')),0,'factory READY must precede all Journal inputs')
            self.assertIsNone(graph.journal._recorder)
            public=deepcopy(worker)
            for key in ('token','token_file','service_url'):public.pop(key,None)
            manifest=dict(schema='home-live-session-manifest-2',version=2,
                runtime_profile='home-live-runtime-config-3',session_ref=deepcopy(owner.source.scope),
                configuration_digest=graph.configuration_digest,source_bundle_digest=source_bundle,
                worker_config=public,worker_description=None)
            recorder=Recorder(evidence/'home-originals.jsonl',manifest,max_records=8192,
                max_bytes=64*1024**2,min_free_bytes=1)
            holder['recorder']=recorder
            declaration=dict(schema=SCHEMA,version=1,selection=FAMILY,source_bundle_digest=source_bundle,
                recording=dict(native_file=str(evidence/'production-native.jsonl'),birth_file=str(evidence/'source-birth.json'),
                    ledger_directory=str(evidence/'ledgers'),approval_directory=str(evidence/'approvals'),
                    max_records=8192,max_bytes=32*1024**2,min_free_bytes=1),
                limits=dict(max_runtime_s=20,max_commits=32,interval_s=.02,maximum_requests=8))
            factory=ProductionCommandFactory(declaration,composition=owner.composition,owner_bearer=owner.owner_token)
            session=factory(graph,recorder);holder.update(factory=factory,session=session)
            graph.journal.attach_recorder(session.record)
            session.start();self.assertEqual(session.readiness()['status'],'ready')
            self.assertIs(session.owner.source.journal,graph.journal)
            self.assertIs(session.owner.composition,owner.composition)
            self.assertIs(session.owner.graph,graph)
            self.assertTrue(session.owner.recording_ready)
            self.assertFalse(session.owner.started)
            self.assertFalse(session.owner.plant._started)
            self.assertEqual(session.owner.plant._members,[None]*4)
            session.owner.require_context()
            with self.assertRaisesRegex(ValueError,'no_accepted_room_commit_selected'):session.owner.require_current()
            # Exact Recorder remains factory input. This write-only port forwards
            # worker originals to the actual session, then that same Recorder.
            return SimpleNamespace(path=recorder.path,manifest=recorder.manifest,
                record=session.record,status=recorder.status)

        try:
            source_owner=load_upstream().Fixture(self,on_graph=on_graph)
            session=holder['session'];actual_tick=source_owner.tick
            def tick():
                row=actual_tick()
                selected=self.wait_commit(session,row['commit'],source_owner)
                timeline.append(dict(commit_id=row['commit']['id'],t=row['decision']['t'],
                    worker_decision=deepcopy(row['decision']),command=selected))
                if row['decision']['room_output']['fixtures'][0]['domains'][7]['sample'] is None:
                    self.assertFalse(selected['native_ready'],'unsupported sample must not trigger native boot')
                return row
            source_owner.tick=tick
            positive=source_owner.run_positive()
            self.assertEqual(len(positive['steps']),7)
            final_relations=list(positive['checkpoint']['state']['engagement']['reducer']['state']['relations'].values())
            supported=[r for r in final_relations if r['state']=='supported']
            self.assertEqual(len(supported),1)
            self.assertEqual(supported[0]['chosen_clause']['qualification'],'qualified_pc_input')
            self.assertEqual(supported[0]['refinement']['value'],'computer_work')
            self.assertGreaterEqual(supported[0]['refine_dwell'],10)
            commands=[t['command']['last_result']['fixtures'][0] for t in timeline]
            submitted=[c for c in commands if c['status']=='submitted']
            unmet=[c for c in commands if c['status']=='unmet']
            self.assertEqual(len(submitted),1,'one actual original zero SAMPLE, no hidden repeat')
            self.assertTrue(unmet,'actual later nonzero SAMPLE must remain explicitly unmet')
            zero=submitted[0]
            self.assertEqual(zero['translation']['status'],'supported_zero_without_color_claim')
            self.assertEqual(zero['translation']['original_sample'],dict(brightness_fraction=0,cct_kelvin=3000))
            self.assertTrue(zero['adapter_receipt']['finalization_complete'])
            self.assertIn(zero['adapter_receipt']['device_disposition'],('ADMITTED','DUPLICATE'))
            self.assertFalse(zero['translation']['authority_created'])
            self.assertEqual(zero['plan']['frame'][21:],[0,0,0])
            for row in unmet:
                self.assertFalse(row['dispatched'])
                self.assertEqual(row['translation']['reason'],'spotlight_emitter_mapping_and_color_model_unavailable')
                self.assertIsNone(row['translation']['production_request'])
            self.assertTrue(session.owner.started)
            self.assertEqual(set(session.bridges),set(range(4)))
            for i,bridge in session.bridges.items():
                self.assertTrue(bridge.started)
                self.assertIs(bridge.native_tuple,session.owner._tuples[i])
                self.assertEqual(bridge.authorization._used,{'activation','session'})
            # Real installed source/refusal controls; none can create a packet.
            before=[session.owner.recorder.validated_response(i) for i in range(4)]
            consumer=TrustedCommittedSpotlightSample(session.owner.source)
            selected=consumer.translate('fixture-1',request_id=str(uuid.uuid4()))
            with self.assertRaisesRegex(ValueError,'translation_changed'):
                consumer.require_current(replace(selected,sample_pointer='/payload/desired/sample'))
            changed=deepcopy(selected);changed.translation['production_request']={'levels':[1,2,3]}
            with self.assertRaisesRegex(ValueError,'translation_changed'):consumer.require_current(changed)
            altered=deepcopy(positive['commit']);altered['checkpoint']['input_cursor']+=1
            with self.assertRaises(ValueError):session.owner.install_commit(altered)
            self.assertEqual([session.owner.recorder.validated_response(i) for i in range(4)],before)
            session.quiesce()
            source_quiescence=source_owner.graph.quiesce('operator_stop')
            self.assertEqual(source_quiescence['status'],'quiescent',source_quiescence)
            self.assertFalse(source_quiescence['inflight'])
            self.assertTrue(source_owner.graph.cancel.is_set())
            with self.assertRaises(ValueError):session.owner.require_current()
            terminal=session.drain(timeout_s=10)
            self.assertFalse(terminal['pending']);self.assertTrue(terminal['safe_to_close_journal'],terminal)
            self.assertEqual(terminal['status'],'complete',terminal)
            self.assertEqual(len(terminal['protective_stops']),4)
            for row in terminal['protective_stops']:
                stop=row['receipt'];self.assertEqual(stop['status'],'protective_stop_acknowledged',stop)
                self.assertEqual(stop['acknowledgement']['domain_mask'],255)
                self.assertEqual(stop['acknowledgement']['reason'],'travel')
                self.assertTrue(stop['manual_authority_revoked']);self.assertFalse(stop['retry'])
                self.assertFalse(stop['physical_rest_confirmed'])
            self.assertTrue(session.done.is_set());self.assertFalse(session.thread.is_alive())
            closed_before=[session.owner.recorder.validated_response(i) for i in range(4)]
            with self.assertRaises(ValueError):session.bridges[0].submit_plan(zero['plan'])
            self.assertEqual([session.owner.recorder.validated_response(i) for i in range(4)],closed_before)
            native=terminal['native']
            self.assertFalse(native['cleanup_unconfirmed'],native)
            framed=verify_room_production_canonical_archive(session.owner.recording['native_file'],
                expected_header=session.owner.header,recording_receipt=native,qualification=session.owner.qualification)
            self.assertEqual(framed['status'],'production_archive_verified',framed)
            self.assertEqual(len(framed['transcripts']),4)
            packet_counts=[]
            for i,transcript in enumerate(framed['transcripts']):
                binding=session.owner.native_bindings[i];operations=[];first_session=None;ready_at=None
                for n,(request,response) in enumerate(zip(transcript['stdin_bytes'].splitlines(),transcript['responses'])):
                    request=json.loads(request)
                    if request['op']=='tick' and response.get('report',{}).get('boot_ready') is True and ready_at is None:ready_at=n
                    if request['op']!='exchange':continue
                    raw=bytes.fromhex(request['packet_hex'])
                    wire=session.owner.modules.wire
                    peek=wire.inspect_packet(raw)
                    if raw==wire.capabilities_request():
                        self.assertEqual(peek.operation.name,'CAPABILITIES')
                        continue  # The sole original unauthenticated capability query.
                    context=wire.BOOTSTRAP_CONTEXT if peek.operation.name=='SESSION_OPEN' else binding.context_digest
                    packet=wire.open_packet(binding.key,binding.device_id,binding.boot_generation,
                        context,wire.Direction.REQUEST,raw)
                    operations.append(packet.operation.name)
                    self.assertEqual(packet.session,binding.session_id)
                    if packet.operation.name=='SESSION_OPEN' and first_session is None:first_session=n
                    if packet.operation.name=='WITHDRAW':
                        self.assertEqual(packet.body,session.owner.modules.wire.withdraw_body(255,3,True))
                self.assertIsNotNone(ready_at);self.assertIsNotNone(first_session);self.assertLess(ready_at,first_session)
                self.assertEqual(operations.count('WITHDRAW'),1)
                self.assertEqual(operations.count('PLAN_COMMIT'),int(i==0))
                packet_counts.append(dict(fixture_index=i,operations=operations))
            bad_header=deepcopy(session.owner.header);bad_header['fixtures'][0]['device_id']='ff'*8
            refused=verify_room_production_canonical_archive(session.owner.recording['native_file'],
                expected_header=bad_header,recording_receipt=native,qualification=session.owner.qualification)
            self.assertEqual(refused['status'],'incomplete')
            birth_copy=evidence/'changed-source-birth.json';raw=session.owner.birth_file.read_bytes();birth_copy.write_bytes(raw+b' ')
            with patch.object(session.owner,'birth_file',birth_copy):
                bad_birth=verify_room_production_canonical_archive(session.owner.recording['native_file'],
                    expected_header=session.owner.header,recording_receipt=native,qualification=session.owner.qualification)
            self.assertEqual(bad_birth['status'],'incomplete')
            original_read=provider._read
            def changed_manifest(path,maximum):
                raw=original_read(path,maximum)
                return raw+b' invalid' if Path(path)==provider.BUILD/'manifest.json' else raw
            with patch.object(provider,'_read',side_effect=changed_manifest):
                bad_manifest=verify_room_production_canonical_archive(session.owner.recording['native_file'],
                    expected_header=session.owner.header,recording_receipt=native,qualification=session.owner.qualification)
            self.assertEqual(bad_manifest['status'],'incomplete')
            source_cleanup=source_owner.close()
            self.assertTrue(source_owner.graph.finished)
            self.assertEqual(source_cleanup['actual_worker_drain']['status'],'settled')
            archive=holder['recorder'].drain({'status':'incomplete','reason':'authored_source_partial_prefix_no_full_home_replay_claim'})
            receipt=dict(schema='home-production-canonical-adoption-authored-1',version=1,status='passed',
                source_bundle_digest=source_bundle,original_timeline=timeline,
                genuine_zero=submitted,nonzero_unmet=unmet,terminal=terminal,source_quiescence=source_quiescence,
                source_cleanup=source_cleanup,
                framed={k:v for k,v in framed.items() if k!='transcripts'},packet_counts=packet_counts,
                negative_controls=dict(changed_pointer=True,nested_translation=True,changed_commit=True,
                    changed_birth=bad_birth,changed_header=refused,changed_manifest=bad_manifest,post_Stop_dispatch=True),
                worker_archive=archive,evidence_source='authored_external_software_integration',
                source_qualification=False,physical_permission=False,physical_truth=False,home_room_replayed=False,
                hardware_operations=0,host_operations=0,real_model_calls=0,spotlight_nonzero_CCT_mapping=False)
            (evidence/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
        finally:
            session=holder.get('session')
            selected_owner=source_owner or holder.get('source_owner')
            safe=session is None
            if session is not None:
                try:
                    if terminal is None:
                        session.quiesce()
                        if selected_owner is not None and not selected_owner.graph.finished:
                            source_quiescence=selected_owner.graph.quiesce('operator_stop')
                        terminal=session.drain(timeout_s=10)
                except BaseException as error:
                    terminal=dict(status='incomplete',pending=True,safe_to_close_journal=False,
                        reason=type(error).__name__)
                safe=terminal.get('safe_to_close_journal') is True and terminal.get('pending') is False
            if not safe and selected_owner is not None:
                incumbent.RoomCommandSessionTests._defer_unsafe_fixture_cleanup(self,selected_owner,session,holder)
                self.fail('actual production Stop/drain unconfirmed; writers retained for outer Job')
            if selected_owner is not None and not selected_owner.closed:selected_owner.close()
            if holder.get('recorder') is not None and not holder['recorder'].closed:
                holder['recorder'].drain({'status':'incomplete','reason':'authored_cleanup_without_full_home_replay_claim'})


if __name__=='__main__':unittest.main(verbosity=2)
