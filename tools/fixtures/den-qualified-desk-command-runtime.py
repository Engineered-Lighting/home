"""One finite authored qualified-desk commit -> canonical software command join.

The caller owns the actual source/Store/Journal/sole-worker path. This adapter
accepts only its real accepted commit and retains originals before command
effects. External commissioning, path witness and motor feedback are authored;
no real qualification, physical operation or spotlight CCT mapping is claimed.
Importing this module launches no process. The coordinator must select its test
under the existing bounded canonical provider guard.
"""
from copy import deepcopy
from contextlib import ExitStack
from decimal import Decimal, ROUND_HALF_UP
import base64
import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT/'stack/services/perception-lab', ROOT/'stack/services/home-context',
                  ROOT/'tools/den-live-runtime'):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

import test_room_command_session as canonical_fixture
import test_room_retained_task_native_join as retained_fixture
from journal import digest
from room_command_projection import (RoomCommandProjector, RETAINED_TASK_PROFILE1,
    CanonicalProjectionProvider, MIXER_SOURCE_SHA256)
from room_committed_source import RoomCommitBinding, CommittedRoomSource
from room_fixture_command_join import CanonicalRoomCommandJoin, RoomCommandStateResolver
from room_retained_task_assessor import TrustedRetainedTaskAssessor


def target_grid(angles):
    return [int((Decimal(str(angle))*100/step).to_integral_value(rounding=ROUND_HALF_UP))*step
            for angle, step in zip(angles, (1, 2))]


class CommandTrace:
    """Downstream adapter; source/temporal/worker authority stays with caller."""
    def __init__(self, case, *, owner, graph, worker, evidence):
        self.case, self.owner, self.graph, self.worker = case, owner, graph, worker
        self.evidence = Path(evidence)
        case.assertTrue(self.evidence.is_absolute() and self.evidence.is_dir())
        case.assertFalse(any(self.evidence.iterdir()), 'fresh command evidence directory required')
        case.assertFalse(any(path.is_symlink() for path in (self.evidence, *self.evidence.parents)))
        self.helper = canonical_fixture.RoomCommandSessionTests()
        self.helper.native = canonical_fixture.RoomCommandSessionTests.native
        self.helper.holder = {}
        self.holder, self.records, self.path_originals = self.helper.holder, [], {}
        self.path_queries = []
        self.task_originals, self.native, self.join, self.sidecar = [], None, None, None
        self.assessor, self.last, self.receipt = None, None, None
        self.initial_can_replies=[]
        dynamic = graph.journal.dynamic.worker
        self.binding = RoomCommitBinding(deepcopy(owner.source.scope), dynamic.policy['worker_id'],
            dynamic.policy['producer_id'], dynamic.policy['config_digest'],
            graph.journal.dynamic.config['manifest_ref']['digest'], dynamic.worker_version,
            tuple(row['fixture_id'] for row in dynamic.policy['room_policy']['fixtures']))
        case.assertEqual(dynamic.worker_version, 'home-live-context-worker-12.0.0')
        case.assertEqual(graph.journal.dynamic.config['version'], 13)
        public_worker = deepcopy(worker)
        for key in ('token', 'token_file', 'service_url'):
            public_worker.pop(key, None)
        manifest = dict(schema='home-live-session-manifest-2', version=2,
            runtime_profile='home-live-runtime-config-3', session_ref=deepcopy(self.binding.scope),
            configuration_digest=self.binding.configuration_digest,
            source_bundle_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            worker_config=public_worker, worker_description=None)
        self.recorder = canonical_fixture.Recorder(self.evidence/'worker-originals.jsonl', manifest,
            max_records=8192, max_bytes=64*1024**2, min_free_bytes=1)
        # Existing helper returns actual command/native/authority owners. Only
        # constructor composition selects the reviewed optional TASK capability.
        helper_owner=SimpleNamespace(f=owner.f,path=self.evidence,worker=worker,
            source=owner.source,composition=owner.composition)
        with self.composition():
            self.commands = self.helper._canonical_commands(helper_owner, graph, self.binding, self.recorder)
        self.native, self.sidecar = self.holder['native'], self.holder['sidecar']
        case.assertFalse(self.native._started)
        graph.journal.attach_recorder(self.record)

    def record(self, body, *, record_id=None):
        if body['kind'] == 'fixture_canonical_dispatch_prepare' and body['data']['operation'] == 'reserve':
            if body['data']['record']['reservation']['mode'] == 'autonomous-task':
                self.case.assertTrue(self.task_originals, 'TASK reservation before durable path original ACK')
        ack = self.commands.record(body, record_id=record_id)
        self.records.append(dict(body=deepcopy(body), ack=deepcopy(ack)))
        if body['kind'] == 'room_task_assessment_original':
            rows = [json.loads(line) for line in self.recorder.path.read_bytes().splitlines()]
            self.case.assertEqual(rows[ack['seq']]['digest'], ack['digest'])
            self.case.assertEqual(rows[ack['seq']]['record'], body)
            self.task_originals.append(deepcopy(self.records[-1]))
        return ack

    def _resolve_path(self, query):
        # Lookup only. The independently authored original must already exist.
        self.path_queries.append(deepcopy(query))
        return deepcopy(self.path_originals.get(digest(query)))

    def retain_authored_path(self, query, *, now_home_us):
        # Explicit external assessment seam, separate from trusted lookup. It
        # authors neither a compiler request nor a worker/native observation.
        identity = digest(query)
        if identity not in self.path_originals:
            now = now_home_us
            until = min(query['original_caps'].values())
            original = dict(schema='home-retained-task-path-witness-1', version=1,
                query=deepcopy(query), checks=dict(work_coverage=True, all_people_protection=True,
                    room_collision=True, dark_motion_path=True), minimum_work_range_m=.8,
                target_uncertainty_m=.001, total_aim_error_bound_rad=.01,
                observed_at_home_us=now, available_at_home_us=now, valid_until_home_us=until,
                evidence_source='authored-independent', physical_permission=False, physical_truth=False)
            self.path_originals[identity] = dict(ref=dict(store_id='authored-path-originals',
                component_id='independent-path-assessor', receipt_id='path:'+identity, digest=digest(original)),
                original=original)
            with (self.evidence/('path-witness-'+identity+'.json')).open('x', encoding='utf8') as stream:
                json.dump(self.path_originals[identity], stream, sort_keys=True, indent=2)
                stream.write('\n')
        return deepcopy(self.path_originals[identity])

    def composition(self):
        stack = ExitStack()
        original_healthy=self.helper.native.scenarios.Plant.healthy
        def healthy(plant):
            if plant.index!=0:return original_healthy(plant)
            # The declared short-move fixture begins near its retained aim.
            # Supply that external position during the genuine first polls;
            # an idle later drain cannot change a snapshot without a query.
            modules=canonical_fixture.load_canonical_modules()
            provider=CanonicalProjectionProvider(**modules.provider_pins,
                mixer_source_sha256=MIXER_SOURCE_SHA256,ambient_mix=modules.outputs.ambient_mix)
            projection=RoomCommandProjector(self.source,room_policy=self.worker['room_policy'],
                provider=provider,task_profile=deepcopy(RETAINED_TASK_PROFILE1)).project()
            trace=projection['fixtures'][0]['task']['retained_motion']
            target=target_grid([trace['original'][k] for k in ('to_pan_deg','to_tilt_deg')])
            initial=[value-offset for value,offset in zip(target,(100,200))]
            drain=plant.drain_can
            original_port=plant.port
            def port(**fields):
                result=original_port(**fields)
                if fields.get('frames'):
                    self.initial_can_replies.append(dict(at_device_us=plant.now,
                        fields=deepcopy(fields),reply=deepcopy(result)))
                return result
            with patch.object(plant,'drain_can',side_effect=lambda positions=None:drain(
                    initial if positions is None else positions)),patch.object(plant,'port',side_effect=port):
                return original_healthy(plant)
        def projector(*args, **kwargs):
            return RoomCommandProjector(*args, task_profile=deepcopy(RETAINED_TASK_PROFILE1), **kwargs)
        def resolver(*args, **kwargs):
            source, projection, fixture_id = args
            selected = next(row for row in projection.project()['fixtures'] if row['fixture_id'] == fixture_id)
            if selected['task']['retained_motion'] is not None:
                profile_owner = retained_fixture.RetainedTaskNativeJoinTests()
                profile_owner.native = self.helper.native
                declared = profile_owner._compiler(selected, kwargs)
                self.assessor = TrustedRetainedTaskAssessor(profiles={fixture_id:deepcopy(declared.profile)},
                    receipt_store_id='authored-path-originals', receipt_component_id='independent-path-assessor',
                    method_ref=dict(id='authored-independent-retained-path', digest='a'*64),
                    qualification_ref=dict(id='authored-software-only-path', digest='b'*64),
                    resolve_original=self._resolve_path, speed_fixture_deg_s=(20,20), motion_timeout_us=2000000)
                kwargs['retained_task_assessor'] = self.assessor
                kwargs['task_compiler'] = declared
            return RoomCommandStateResolver(*args, **kwargs)
        def join(*args, **kwargs):
            return CanonicalRoomCommandJoin(*args, task_profile=deepcopy(RETAINED_TASK_PROFILE1), **kwargs)
        original = canonical_fixture.CanonicalCommandSession
        stack.enter_context(patch.object(self.helper.native.scenarios.Plant,'healthy',healthy))
        stack.enter_context(patch.object(canonical_fixture, 'RoomCommandProjector', projector))
        stack.enter_context(patch.object(canonical_fixture, 'RoomCommandStateResolver', resolver))
        stack.enter_context(patch.object(canonical_fixture, 'CanonicalRoomCommandJoin', join))
        stack.enter_context(patch.object(canonical_fixture, 'CanonicalCommandSession',
            lambda *a,**k: original(*a, task_profile=deepcopy(RETAINED_TASK_PROFILE1), **k)))
        return stack

    def accept_and_trace(self, commit):
        case = self.case
        self.source = CommittedRoomSource(self.graph.journal, binding=self.binding,
            current=lambda:not self.graph.cancel.is_set())
        self.accepted = self.source.install_commit(commit)
        case.assertEqual(self.accepted['receipt']['status'], 'accepted')
        case.assertEqual(len(self.native.start()['members']), 4)
        with self.composition():
            self.join = self.commands.bind_join(self.source)
        case.assertIs(self.join.source.journal, self.graph.journal)
        # Establish independent actual native position before first TASK. All
        # feedback enters the unchanged provider's can_port and STATUS decoder.
        member, plant = self.join.members[0], self.holder['plants'][0]
        projected = self.join.projector.project()['fixtures'][0]
        trace = projected['task']['retained_motion']
        case.assertIsNotNone(trace, 'actual committed worker task required')
        target = target_grid([trace['original'][key] for key in ('to_pan_deg','to_tilt_deg')])
        initial = [value-offset for value,offset in zip(target,(100,200))]
        for _ in range(3):
            plant.tick(plant.now+20000); plant.drain_can(initial)
        member.resolver.replace_observation(member.adapter.observe())
        first = self.join.dispatch_current()
        case.assertEqual(first['fixtures'][0]['ambient']['status'],'unavailable',first)
        case.assertEqual(first['fixtures'][0]['task']['status'],'refused',first)
        case.assertFalse(self.task_originals, 'no witness must mean no TASK reservation')
        case.assertTrue(self.path_queries, 'actual assessor lookup must be exercised')
        for _ in range(3):
            plant.tick(plant.now+20000); plant.drain_can(initial)
        member.resolver.replace_observation(member.adapter.observe())
        decoded=member.resolver.modules.zha.decode_snapshot_v3(member.resolver._observation.raw_status_v3,
            binding=member.resolver.binding,
            captured_device_us=member.resolver._observation.current_ref.captured_device_us)
        case.assertEqual(decoded['applied'],[0]*24)
        case.assertEqual(decoded['revision_id'],'0','TASK must use the genuine zero native predecessor')
        case.assertEqual([row['angle_centideg'] for row in decoded['axis_observations']],initial,
            'genuine initial CAN polls must confirm the independently authored starting pose')
        case.assertTrue(self.initial_can_replies)
        (self.evidence/'initial-native-can-originals.json').write_text(json.dumps(dict(
            schema='den-authored-initial-motor-feedback-1',initial_centideg=initial,
            original_can_replies=self.initial_can_replies,raw_status_v3_hex=member.resolver._observation.raw_status_v3.hex(),
            decoded_axis_observations=decoded['axis_observations'],revision_id=decoded['revision_id'],
            applied=decoded['applied'],physical_permission=False),indent=2)+'\n',encoding='utf8')
        query=self.assessor.query(member)
        self.retain_authored_path(query,now_home_us=member.resolver._clock().now_home_us)
        self.last = self.join.dispatch_current()
        task, ambient = self.last['fixtures'][0]['task'], self.last['fixtures'][0]['ambient']
        case.assertEqual(ambient['status'], 'unavailable', self.last)
        case.assertEqual(task['status'], 'dispatched', self.last)
        case.assertEqual(task['light_request']['status'], 'unmet')
        case.assertEqual(task['light_request']['reason'], 'spotlight_semantic_CCT_mapping_unavailable')
        case.assertEqual(len(self.task_originals),1)
        original_data=self.task_originals[0]['body']['data']
        case.assertEqual(original_data['schema'],'home-room-task-assessment-original-2')
        case.assertEqual(original_data['version'],2)
        case.assertIn(original_data['path_witness'],list(self.path_originals.values()))
        case.assertEqual(original_data['assessor_ref']['digest'],digest(self.assessor.descriptor()))
        case.assertEqual(original_data['room_commit_ref'],self.accepted['room_commit_ref'])
        plan = task['dispatch']['record']['admitted']['plan']
        case.assertEqual(plan['predecessor_id'],'0')
        case.assertEqual(plan['target_centideg'], target)
        case.assertEqual(plan['domain_mask'],128)
        case.assertEqual(plan['field_mask'],[0]*7+[4])
        case.assertEqual(plan['frame'][21:],[0,0,0])
        case.assertEqual(plan['frame'],[0]*24,'no unavailable ambient or unmet spotlight may become light')
        case.assertFalse(any(row['body']['kind']=='fixture_canonical_grant_prepare'
            and row['body']['data']['receipt']['grant_body']['mode']=='autonomous-ambient' for row in self.records),
            'unavailable ambient must issue no grant')
        case.assertFalse(self.last['physical_permission'])
        for row in self.last['fixtures'][1:]:
            case.assertEqual(row['ambient']['status'],'unavailable')
            case.assertEqual(row['task']['status'],'unavailable')
        plant.tick(); dark_at=plant.now
        plant.tick(); plant.tick(dark_at+100000); plant.drain_can(initial)
        plant.tick(); plant.drain_can(initial)
        sends=[row for row in plant.transmissions(0xa4)
               if row['lineage']['original_plan']['revision_id']==plan['revision_id']]
        expected=[(0x141,bytes((0xa4,0))+struct.pack('<Hi',40,1000-2*target[0])),
                  (0x142,bytes((0xa4,0))+struct.pack('<Hi',10,target[1]//2-500))]
        case.assertEqual([(r['fields']['can_id'],bytes.fromhex(r['fields']['data_hex'])) for r in sends],expected)
        before=[self.native.recorder.validated_response(i) for i in range(4)]
        record_count=len(self.records)
        repeated=self.join.dispatch_current()
        case.assertEqual(repeated['fixtures'][0]['task']['dispatch'],task['dispatch'])
        case.assertTrue(repeated['fixtures'][0]['task']['exact_retry'])
        case.assertEqual([self.native.recorder.validated_response(i) for i in range(4)],before)
        case.assertEqual(len(self.records),record_count)
        altered=deepcopy(commit)
        altered['checkpoint']['input_cursor']+=1
        with case.assertRaises(ValueError):
            self.source.install_commit(altered)
        case.assertEqual([self.native.recorder.validated_response(i) for i in range(4)],before)
        case.assertEqual(len(self.records),record_count)
        self.receipt=dict(schema='den-qualified-desk-canonical-trace-1',version=1,
            room_commit_ref=deepcopy(self.accepted['room_commit_ref']), command=deepcopy(self.last),
            task_originals=len(self.task_originals), path_originals=len(self.path_originals),
            can=[dict(can_id=r['fields']['can_id'],data_hex=r['fields']['data_hex']) for r in sends],
            exact_retry_without_effects=True, altered_commit_refused_without_effects=True,
            ambient_status='unavailable', ambient_grants=0, native_predecessor='0',
            spotlight_semantic_CCT_mapping=False,
            physical_permission=False, physical_truth=False, full_combined_replay=False)
        (self.evidence/'trace.json').write_text(json.dumps(self.receipt,indent=2)+'\n',encoding='utf8')
        return deepcopy(self.receipt)

    def close(self, *, stop_source=None):
        results, errors = {}, []
        native_was_started=bool(self.native and self.native._attempted)
        def stop_join():
            if self.join is None:
                rows=[dict(fixture_id=m.fixture_id,receipt=m.dispatcher.drain())
                      for m in self.holder.get('members',())]
                return dict(fixtures=rows,safe_to_close_journal=all(
                    row['receipt'].get('safe_to_close_journal') is True for row in rows))
            self.join.quiesce()
            before=[self.native.recorder.validated_response(i) for i in range(4)]
            with self.case.assertRaisesRegex(ValueError,'quiesced'):
                self.join.dispatch_current()
            self.case.assertEqual([self.native.recorder.validated_response(i) for i in range(4)],before)
            return self.join.drain()
        safe=False
        try:
            results['join']=stop_join()
            safe=results['join'].get('safe_to_close_journal') is True
        except BaseException as error:
            errors.append(dict(stage='join',type=type(error).__name__,reason=str(error)))
        if safe:
            try:
                results['native']=self.native.close() if self.native else None
                safe=not native_was_started or results['native'].get('cleanup_unconfirmed') is False
            except BaseException as error:
                errors.append(dict(stage='native',type=type(error).__name__,reason=str(error)))
                safe=False
        if safe:
            try:
                results['source_stop']=stop_source() if stop_source is not None else None
                safe=stop_source is None or self.graph.finished is True
            except BaseException as error:
                errors.append(dict(stage='source_stop',type=type(error).__name__,reason=str(error)))
                safe=False
        if safe:
            for name, close in (
                ('canonical',lambda:self.sidecar.drain(dict(status='complete',physical_permission=False)) if self.sidecar else None),
                ('worker_archive',lambda:self.recorder.drain(dict(status='incomplete',reason='combined_replay_unproven',physical_permission=False)))):
                try: results[name]=close()
                except BaseException as error:errors.append(dict(stage=name,type=type(error).__name__,reason=str(error)))
        else:
            # Dispatcher submission may still run after a synchronous caller
            # receives an uncertain outcome. Keep its Journal/source/archives
            # alive, including unittest's registered upstream cleanup, until
            # the existing bounded outer Job ends this failed gate.
            self.holder['trace']=self
            canonical_fixture.RoomCommandSessionTests._defer_unsafe_fixture_cleanup(
                self.case,self.owner,self.commands,self.holder)
            results['deferred']=deepcopy(self.holder['deferred_cleanup'])
        receipt=dict(receipts=results,errors=errors,physical_permission=False)
        (self.evidence/'command-cleanup.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf8')
        self.case.assertFalse(errors,errors)
        self.case.assertTrue(safe,receipt)
        if self.receipt is not None:
            self.case.assertEqual(results['source_stop']['actual_worker_drain']['status'],'settled',receipt)
            self.case.assertTrue(self.graph.finished)
            self.case.assertFalse(self.graph.server_thread.is_alive())
            self.case.assertEqual(self.graph.server.socket.fileno(),-1)
            self.case.assertTrue(results['source_stop']['buffers_cleared'],receipt)
            source=self.owner.composition.desk_person_bridge_source
            with self.owner.f.store.lock:
                registered=getattr(self.owner.f.store,'_qualified_desk_person_bridges',{}).get(self.binding.scope['session_id'])
            self.case.assertIsNot(registered,source)
        if self.native and native_was_started:
            self.case.assertEqual(results['native']['status'],'complete',results['native'])
            self.case.assertFalse(results['native']['cleanup_unconfirmed'])
        return receipt
