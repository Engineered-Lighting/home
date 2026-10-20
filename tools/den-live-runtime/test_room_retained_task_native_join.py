"""One finite, authored config13 TASK -> original canonical CAN tracer.

The real factory, Journal, Node worker, projection, compiler, issuer, outbox,
adapter and four-member pinned native capturer remain intact. Only external
observations, one pre-construction ambient assessment, path assessment and
simulated motor feedback are authored. No continuous service, real Den/07,
semantic spotlight LED mapping, physical operation or full replay is claimed.

The coordinator must select this file under the existing bounded native guard;
collection alone must not launch children. The selected provider stays 6d5f2bc.
"""
from copy import deepcopy
import base64
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / 'stack/services/perception-lab', ROOT / 'stack/services/home-context',
             ROOT / 'tools/den-live-runtime'):
    if str(path) not in sys.path:
        sys.path.append(str(path))

import test_room_command_session as canonical_fixture
import test_room_subset_worker as worker_fixture
from journal import digest
from fixture_execution_validation import CAP_FIELDS
from fixture_joint_execution import (ABI, COORDINATES, PROFILE_SCHEMA, CURRENT_FIELDS,
    JointFixtureCompiler, RETAINED_ASSESSMENT_METHOD, RETAINED_REQUEST_SCHEMA,
    retained_native_reference_digest)
from test_fixture_profile import fictional_profile
from room_command_projection import RoomCommandProjector, RETAINED_TASK_PROFILE1
from room_committed_source import RoomCommitBinding, CommittedRoomSource
from room_fixture_command_join import (CanonicalRoomCommandJoin, RoomCommandStateResolver,
    RoomRetainedTaskAssessment)

# Use the same module identity as the actual reusable four-member helper.
Recorder = canonical_fixture.Recorder
CanonicalCommandSession = canonical_fixture.CanonicalCommandSession
CONTROLLED_INITIAL_OFFSET_CENTIDEG = (100, 200)
CONTROLLED_JOINT_SPEED_DEG_S = (20, 20)
CONTROLLED_ARRIVAL_US = 600000


def us(value):
    return int(Decimal(str(value)) * 1000000)


def joint_target(angles):
    """Independent literal representable grids: PAN=1, TILT=2 centidegrees."""
    return [int((Decimal(str(angle)) * 100 / step).to_integral_value(rounding=ROUND_HALF_UP)) * step
            for angle, step in zip(angles, (1, 2))]


class RetainedTaskNativeJoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verifies the existing manifest/49 originals; no native process starts.
        canonical_fixture.RoomCommandSessionTests.setUpClass()
        cls.native = canonical_fixture.RoomCommandSessionTests.native

    def test_actual_positive_worker_ambient_predecessor_then_retained_dark_task_can(self):
        evidence = Path(os.environ['DEN_RETAINED_TASK_EVIDENCE_DIR'])
        allowed = (ROOT / 'artifacts/den-retained-task-aim-20261018/native-join-01').resolve()
        self.assertTrue(evidence.is_absolute() and evidence.is_dir() and not evidence.is_symlink())
        self.assertTrue(evidence.resolve().is_relative_to(allowed))
        self.assertFalse(any(path.is_symlink() for path in evidence.parents))
        self.assertFalse(any(evidence.iterdir()), 'Root must select a fresh empty evidence directory')
        home = worker_fixture.RoomSubsetWorkerTests()
        self.addCleanup(home.doCleanups)
        helper = canonical_fixture.RoomCommandSessionTests()
        helper.native = canonical_fixture.RoomCommandSessionTests.native
        helper.holder = {}
        holder = helper.holder
        native, join, recorder, sidecar = None, None, None, None
        record_order, task_originals = [], []
        original_policy = worker_fixture.subset_policy
        original_create = worker_fixture.create_production_acquisition

        def assessed_policy(policy):
            # Independent authored assessment exists BEFORE config13/grants.
            result = original_policy(policy)
            result['fixtures'][0]['assessment']['ambient'][0]['assessment'] = {
                key: deepcopy(value) for key, value in policy['fixtures'][0]['ambient'][0].items()
                if key != 'domain'}
            return result

        class OwnerClock:
            @property
            def now(self):
                return home.now

        def projector(*args, **kwargs):
            return RoomCommandProjector(*args, task_profile=deepcopy(RETAINED_TASK_PROFILE1), **kwargs)

        def resolver(*args, **kwargs):
            source, projection, fixture_id = args
            selected = next(row for row in projection.project()['fixtures'] if row['fixture_id'] == fixture_id)
            if selected['task']['retained_motion'] is not None:
                kwargs['task_compiler'] = self._compiler(selected, kwargs)
            return RoomCommandStateResolver(*args, **kwargs)

        def joined(*args, **kwargs):
            return CanonicalRoomCommandJoin(*args, task_profile=deepcopy(RETAINED_TASK_PROFILE1), **kwargs)

        def session(*args, **kwargs):
            return CanonicalCommandSession(*args, task_profile=deepcopy(RETAINED_TASK_PROFILE1), **kwargs)

        def graph_created(*args, **kwargs):
            nonlocal recorder, native, sidecar
            graph = original_create(*args, **kwargs)
            # Actual public factory return: before any source step/worker tick.
            worker = graph.journal.dynamic.worker
            binding = RoomCommitBinding(deepcopy(home.identity), worker.policy['worker_id'],
                worker.policy['producer_id'], worker.policy['config_digest'],
                graph.journal.dynamic.config['manifest_ref']['digest'], worker.worker_version,
                tuple(row['fixture_id'] for row in worker.policy['room_policy']['fixtures']))
            public_worker = deepcopy(home.config)
            for key in ('token', 'service_url'):
                public_worker.pop(key, None)
            manifest = dict(schema='home-live-session-manifest-2', version=2,
                runtime_profile='home-live-runtime-config-3', session_ref=deepcopy(home.identity),
                configuration_digest=binding.configuration_digest,
                source_bundle_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                worker_config=public_worker, worker_description=None)
            recorder = Recorder(evidence / 'task-worker-originals.jsonl', manifest,
                max_records=4096, max_bytes=64 * 1024**2, min_free_bytes=1)
            owner = SimpleNamespace(f=OwnerClock(), path=evidence, worker=home.config,
                source=SimpleNamespace(scope=deepcopy(home.identity)),
                composition=SimpleNamespace(require_current=lambda: None if graph.cancel.is_set() else graph))
            owner.f.session = dict(id=home.identity['session_id'])
            commands = helper._canonical_commands(owner, graph, binding, recorder)
            native, sidecar = holder['native'], holder['sidecar']

            def recorded(body, *, record_id=None):
                if body['kind'] == 'fixture_canonical_dispatch_prepare' and body['data']['operation'] == 'reserve':
                    reservation = body['data']['record']['reservation']
                    if reservation['mode'] == 'autonomous-task':
                        self.assertTrue(task_originals, 'TASK reservation preceded original durable ACK')
                        self.assertTrue(task_originals[-1]['physical_boundary'])
                ack = commands.record(body, record_id=record_id)
                record_order.append((body['kind'], deepcopy(body['data']), deepcopy(ack)))
                if body['kind'] == 'room_task_assessment_original':
                    # Actual fsync-returned ACK and bytes, not an ACK stub.
                    raw = recorder.path.read_bytes()
                    rows = [json.loads(line) for line in raw.splitlines()]
                    self.assertEqual(rows[ack['seq']]['digest'], ack['digest'])
                    self.assertEqual(rows[ack['seq']]['record'], body)
                    task_originals.append(dict(body=deepcopy(body), ack=deepcopy(ack),
                        physical_boundary=raw.endswith(b'\n') and len(raw) == recorder.status()['bytes']))
                return ack

            graph.journal.attach_recorder(recorded)
            holder.update(commands=commands, binding=binding, graph=graph)
            self.assertEqual(recorder.status()['status'], 'ready')
            self.assertFalse(native._started)
            return graph

        # Constructor-only adapters return the actual production classes. No
        # validation/receipt/decision/clock or native transport is substituted.
        with patch.object(worker_fixture, 'subset_policy', assessed_policy), \
                patch.object(worker_fixture, 'create_production_acquisition', graph_created), \
                patch.object(canonical_fixture, 'RoomCommandProjector', projector), \
                patch.object(canonical_fixture, 'RoomCommandStateResolver', resolver), \
                patch.object(canonical_fixture, 'CanonicalRoomCommandJoin', joined), \
                patch.object(canonical_fixture, 'CanonicalCommandSession', session):
            try:
                home.setUp()
                for _ in range(12):
                    decision = home.advance()
                    if (decision['execution']['status'] == 'virtual'
                            and decision['execution']['readback'][0]['brightness_fraction'] > 0):
                        break
                else:
                    self.fail('Actual unchanged dwell/worker did not establish a positive task')
                self.assertEqual(decision['schema'], 'home-live-shadow-decision-12')
                self.assertGreaterEqual(home.relation()['enter_dwell'], 5)
                posted = home.probe.command('tick', t=home.now)
                self.assertFalse(posted['http_errors'], posted)
                commit = json.loads(posted['commit_posts'][-1])
                source = CommittedRoomSource(home.graph.journal, binding=holder['binding'],
                    current=lambda: not home.graph.cancel.is_set())
                accepted = source.install_commit(commit)
                self.assertEqual(accepted['receipt']['status'], 'accepted')
                self.assertEqual(len(native.start()['members']), 4)
                join = holder['commands'].bind_join(source)
                self.assertIs(type(join), CanonicalRoomCommandJoin)
                self.assertIs(join.source.journal, home.graph.journal)
                first = join.dispatch_current()
                self.assertEqual(first['fixtures'][0]['ambient']['status'], 'dispatched', first)
                self.assertEqual(first['fixtures'][0]['task']['reason'], 'task_assessment_unavailable')
                for row in first['fixtures'][1:]:
                    self.assertEqual(row['ambient']['status'], 'unavailable')
                    self.assertEqual(row['task']['status'], 'unavailable')
                member, plant = join.members[0], holder['plants'][0]
                projected = join.projector.project()['fixtures'][0]
                target = joint_target([projected['task']['retained_motion']['original'][key]
                                       for key in ('to_pan_deg', 'to_tilt_deg')])
                # Actual simulated RMD replies place the axes near the retained
                # endpoint. They enter through can_port and the pinned decoder;
                # neither STATUS nor current-reference fields are rewritten.
                initial = [value - offset for value, offset in zip(target, CONTROLLED_INITIAL_OFFSET_CENTIDEG)]
                for _ in range(3):
                    plant.tick(plant.now + 20000)
                    plant.drain_can(initial)
                member.resolver.replace_observation(member.adapter.observe())
                observation = member.resolver._observation
                decoded = member.resolver.modules.zha.decode_snapshot_v3(observation.raw_status_v3,
                    binding=member.resolver.binding, captured_device_us=observation.current_ref.captured_device_us)
                self.assertEqual([row['angle_centideg'] for row in decoded['axis_observations']], initial)
                self.assertGreater(decoded['applied'][0], 0)
                self.assertEqual(decoded['applied'][21:], [0, 0, 0])
                self.assertEqual(decoded['revision_id'], first['fixtures'][0]['ambient']['dispatch']['record']['reservation']['revision_id'],
                    'Pinned native counter disagrees with actual confirmed ambient predecessor; do not reseed it')
                original = self._assessment(member, projected, initial)
                member.resolver.install_task_assessment(original)
                dispatched = join.dispatch_current()
                task = dispatched['fixtures'][0]['task']
                self.assertEqual(task['status'], 'dispatched', dispatched)
                self.assertTrue(dispatched['fixtures'][0]['ambient']['exact_retry'])
                self.assertEqual(task['light_request']['status'], 'unmet')
                self.assertFalse(dispatched['physical_permission'])
                self.assertEqual(len(task_originals), 1)
                self.assertEqual(task_originals[0]['body']['data']['assessment'], original.original())
                self.assertEqual(task_originals[0]['body']['data']['room_commit_ref'], accepted['room_commit_ref'])
                plan = task['dispatch']['record']['admitted']['plan']
                self.assertEqual(plan['predecessor_id'], decoded['revision_id'])
                self.assertEqual(plan['domain_mask'], 128)
                self.assertEqual(plan['field_mask'], [0] * 7 + [4])
                self.assertEqual(plan['target_centideg'], target)
                self.assertEqual(plan['frame'][21:], [0, 0, 0])
                self.assertEqual(plan['frame'][:21], decoded['requested'][:21])
                # Tick only the explicit canonical simulator; no automatic
                # motion/arrivals or LED semantics are added by this test.
                plant.tick(); dark_at = plant.now
                plant.tick(); plant.tick(dark_at + 100000); plant.drain_can(initial)
                plant.tick(); plant.drain_can(initial)
                sends = [row for row in plant.transmissions(0xa4)
                         if row['lineage']['original_plan']['revision_id'] == plan['revision_id']]
                expected = [(0x141, bytes((0xa4, 0)) + struct.pack('<Hi', 40, 1000 - 2 * target[0])),
                            (0x142, bytes((0xa4, 0)) + struct.pack('<Hi', 10, target[1] // 2 - 500))]
                self.assertEqual([(row['fields']['can_id'], bytes.fromhex(row['fields']['data_hex'])) for row in sends], expected)
                self.assertTrue(all(row['lineage']['original_plan']['predecessor_id'] == decoded['revision_id'] for row in sends))
                # Every parsed operation must have an original captured response.
                capture_rows = [json.loads(line) for line in native.recorder.path.read_bytes().splitlines()]
                stream = b''.join(base64.b64decode(row['payload']['original']['base64'], validate=True)
                    for row in capture_rows if row['kind'] == 'io'
                    and row['payload']['fixture_index'] == 0
                    and row['payload']['event']['kind'] == 'stdout_bytes')
                captured = [operation for raw in stream.splitlines() for operation in json.loads(raw)['canonical_ops']
                    if operation['kind'] == 'can_port_transmit_once'
                    and operation['fields']['data_hex'].startswith('a4')
                    and operation['lineage']['original_plan']['revision_id'] == plan['revision_id']]
                self.assertEqual([(row['fields']['can_id'], bytes.fromhex(row['fields']['data_hex'])) for row in captured], expected)
                self.assertEqual(len(task_originals), 1)
            finally:
                # One failed cleanup receipt must never skip other owned closes.
                cleanups, errors = {}, []
                for label, close in (
                    ('join', lambda: (join.quiesce(), join.drain())[1] if join is not None else
                        [dict(fixture_id=m.fixture_id, receipt=m.dispatcher.drain())
                            for m in holder.get('members', ())]),
                    ('native', lambda: native.close() if native is not None else None),
                    ('worker_stop', lambda: home.probe.command('stop') if getattr(home, 'probe', None) is not None else None),
                    ('source_close', lambda: home.graph.close() if getattr(home, 'graph', None) is not None else None),
                    ('canonical', lambda: sidecar.drain(dict(status='complete', physical_permission=False))
                        if sidecar is not None else None),
                    ('worker_archive', lambda: recorder.drain(dict(status='incomplete',
                        reason='finite_command_tracer_not_full_worker_replay', physical_permission=False))
                        if recorder is not None else None)):
                    try:
                        cleanups[label] = close()
                    except BaseException as error:
                        errors.append(dict(stage=label, type=type(error).__name__, reason=str(error)))
                (evidence / 'cleanup.json').write_text(json.dumps(dict(receipts=cleanups, errors=errors,
                    limits=dict(full_worker_replay=False, continuous_service=False, real_Den_07=False,
                        physical_permission=False, spotlight_semantic_mapping=False)), indent=2), encoding='utf-8')
                self.assertFalse(errors, errors)
                if join is not None:
                    self.assertTrue(cleanups['join']['safe_to_close_journal'])
                if native is not None:
                    self.assertEqual(cleanups['native']['status'], 'complete', cleanups['native'])
                    self.assertFalse(cleanups['native']['cleanup_unconfirmed'])
                    self.assertEqual(len(cleanups['native']['members']), 4)
                    self.assertTrue(all(row['close_acknowledged'] and row['recording_status']['complete']
                                        for row in cleanups['native']['members']))
                if sidecar is not None:
                    self.assertEqual(cleanups['canonical']['status'], 'drained')
                if recorder is not None:
                    # WorkerProbe lacks worker_tick/prepared originals; do not
                    # turn its ordinary Journal archive into a replay claim.
                    self.assertEqual(cleanups['worker_archive']['status'], 'incomplete')

    def _compiler(self, selected, kwargs):
        modules, binding, observation = kwargs['modules'], kwargs['native_binding'], kwargs['initial_observation']
        config = json.loads(Path(self.native.manifest['config']['path']).read_bytes())
        status = modules.zha.decode_snapshot_v3(observation.raw_status_v3, binding=binding,
            captured_device_us=observation.current_ref.captured_device_us)
        profile = fictional_profile()
        profile['fixture_id'] = selected['fixture_id']
        profile['pins'].update(status['pins'])
        until = kwargs['generation'].authority_not_after_home_us
        for evidence in profile['evidence'].values():
            evidence['valid_until_home_us'] = until
        basis = selected['task']['retained_motion']['fixture_basis']
        profile['geometry'].update(optical_pivot_m=deepcopy(basis['origin_m']),
            fixture_to_room_basis=deepcopy(basis['local_to_room']))
        for axis, motor, max_speed in zip(profile['axes'], config['motors'], (100, 50)):
            axis.update(zero_centideg=motor['motor_zero_centideg'], direction=motor['direction'],
                units_per_fixture_degree=dict(numerator=100 * motor['joint_per_motor_den'],
                    denominator=motor['joint_per_motor_num']), minimum_centideg=-40000,
                maximum_centideg=40000, max_speed_deg_s=max_speed)
        profile['bounds']['max_quantization_error_rad'] = .0004
        # Frozen independent fictional short-motion commissioning: the actual
        # start offsets map to raw distances200/100 centidegrees. At unchanged
        # acceleration10000 centidegrees/s² and raw speed caps40/10dps, the
        # triangular lower bounds are ceil(2*sqrt(.02)*1e6)=282843us and
        # 200000us. The authored600000us upper bound exceeds both. Every other
        # timing floor and the original source/display leases remain intact.
        profile['bounds']['arrival_bound_us'] = CONTROLLED_ARRIVAL_US
        joint = dict(schema=PROFILE_SCHEMA, version=1, motor_profile=profile,
            canonical=dict(abi=ABI, coordinate_contract=COORDINATES, **modules.provider_pins),
            spotlight=deepcopy(config['spotlight']))
        return JointFixtureCompiler(joint, canonical_config=config, provider_pins=modules.provider_pins,
            binding=binding, decode_status=modules.zha.decode_snapshot_v3,
            decode_capabilities=modules.zha.decode_capabilities_v2)

    def _assessment(self, member, projected, start):
        r, snapshot = member.resolver, member.resolver.source.current()
        trace = projected['task']['retained_motion']
        c, ref = r._clock(), r._observation.current_ref
        fixture = r._fixture(r.projector.policy)
        support = us(trace['support_until'])
        authority_cap = min(r.generation.authority_not_after_home_us, us(fixture['valid_until']),
            us(r.projector.policy['valid_until']), us(r.projector.policy['exposure']['valid_until']))
        caps = dict(zip(CAP_FIELDS, (support, support, authority_cap, c.qualified_until_home_us)))
        cut, scene = snapshot['decision']['input_cut'], snapshot['decision']['scene']
        lineage = dict(source_cut_sha256=cut['cut_digest'], protection_sha256=digest(dict(input_cut=cut,
            geometry_refs=trace['geometry_refs'], protection_refs=trace['protection_refs'],
            execution_assessment_refs=trace['execution_assessment_refs'])),
            authority_sha256=digest(dict(room_policy_sha256=r.projector.policy_sha256, fixture_id=r.fixture_id,
                native_binding_sha256=r.binding.binding_sha256, home_scope=snapshot['home_scope'],
                authority_generation=snapshot['commit']['checkpoint']['authority_generation'],
                command_generation=r.generation.command_source_generation, authority_cap=authority_cap)),
            scene_id=scene['scene_id'], episode_id=scene['episode_id'], example_id=scene['example_ref']['id'], correction_ref=None)
        angles = [trace['original'][key] for key in ('to_pan_deg', 'to_tilt_deg')]
        request = dict(schema=RETAINED_REQUEST_SCHEMA, version=2, kind='MOVE', motion_id=1,
            revision_id=int(member.dispatcher._meta()['last_revision']) + 1, predecessor_id=ref.revision_id,
            scope=dict(home_id=snapshot['home_scope']['site_id'], room_id=snapshot['home_scope']['room_id'], fixture_id=r.fixture_id),
            pins=deepcopy(r.task_compiler.p['pins']), lineage=lineage,
            context=dict(boot_generation=ref.boot_generation, session_id=ref.session_id, ownership_generation=ref.ownership_generation),
            fixture_angles_deg=angles, retained_aim_ref=deepcopy(trace['retained_aim_ref']),
            speed_fixture_deg_s=list(CONTROLLED_JOINT_SPEED_DEG_S),
            issued_home_us=c.now_home_us, support_expiry_home_us=min(caps.values()), original_caps=caps, motion_timeout_us=2000000)
        # Independent authored model: these path/range/error claims are NOT
        # inferred from the requested angles or labeled as Den qualification.
        request['assessment'] = dict(status='fictional', assessment_id='authored-finite-retained-path',
            method=RETAINED_ASSESSMENT_METHOD, profile_sha256=digest(r.task_compiler.profile),
            pins=deepcopy(request['pins']), lineage=deepcopy(lineage), fixture_angles_deg=deepcopy(angles),
            retained_aim_ref=deepcopy(trace['retained_aim_ref']), target_centideg=joint_target(angles),
            start_centideg=deepcopy(start), current_reference_sha256=retained_native_reference_digest(
                {key:getattr(ref, key) for key in CURRENT_FIELDS}), motion_id=1,
            checks=dict(work_coverage=True, all_people_protection=True, room_collision=True, dark_motion_path=True),
            valid_from_home_us=c.now_home_us, valid_until_home_us=min(caps.values()), original_caps=deepcopy(caps),
            minimum_work_range_m=.8, target_uncertainty_m=.001, total_aim_error_bound_rad=.01)
        return RoomRetainedTaskAssessment(request)


if __name__ == '__main__':
    unittest.main()
