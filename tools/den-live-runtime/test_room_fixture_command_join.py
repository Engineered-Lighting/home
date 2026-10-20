"""First actual accepted-room -> four canonical software fixtures vertical gate.

Camera/model/occupancy I/O and native thermal/CAN replies are authored. The
actual factory, HTTP admission, Node worker, Journal, SDK, native engines and
HAL encoders execute only when the coordinator explicitly runs this test.
Fixture warmup precedes our recorder: capture_prefix_incomplete is intentional;
this gate cannot establish full worker replay or READY-before-all-source.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import threading
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/home-context'))

import test_room_worker as worker_tests
import test_room_canonical_plant as canonical_tests
from fixture_canonical_authority import (CanonicalFixtureAuthority, CanonicalFixtureAuthorityPolicy,
                                         CanonicalIssuerPolicy)
from fixture_canonical_dispatch import CanonicalFixtureDispatcher
from fixture_execution_validation import PIN_NAMES, FixtureAuthorityDenied
from room_canonical_adapter import (CanonicalRoomObservation, RoomCanonicalAdapter,
    SimulatedClockObservation, load_canonical_modules)
from room_canonical_plant import RoomCanonicalPlant
from room_canonical_plant_recording import RoomCanonicalRecorder
from room_committed_source import CommittedRoomSource, RoomCommitBinding
from room_command_projection import CanonicalProjectionProvider, MIXER_SOURCE_SHA256, RoomCommandProjector
# Deliberate first-red boundary: no alternative fixture-only join/fallback.
from room_fixture_command_join import (RoomCommandGeneration, RoomCommandStateResolver,
    RoomCommandMember, CanonicalRoomCommandJoin)


class PartialPrefixRecorder:
    """Small fsynced test callback, not a claimed whole-session replay format."""
    def __init__(self, path):
        self.file = Path(path).open('xb')
        self.lock, self.records, self.used = threading.Lock(), [], 0
        self.record(dict(kind='test_capture_boundary', data=dict(capture_prefix_incomplete=True,
            historical_fixture_warmup_unarchived=True, full_worker_replay=False)))

    def record(self, value):
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
        with self.lock:
            if len(self.records) >= 4096 or self.used + len(raw) > 64 * 1024**2:
                raise ValueError('first_vertical_test_capture_bound')
            if self.file.write(raw) != len(raw): raise ValueError('test_capture_partial_write')
            self.file.flush(); os.fsync(self.file.fileno())
            self.records.append(deepcopy(value)); self.used += len(raw)

    def close(self):
        self.file.close()


class RoomFixtureCommandJoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Existing guard verifies the exact manifest and every source member.
        # It imports only the pinned provider/scenario; no process starts here.
        canonical_tests.CanonicalNativeTests.setUpClass()
        cls.native = canonical_tests.CanonicalNativeTests

    def test_actual_accepted_room_samples_drive_all_four_native_ambient_outputs(self):
        evidence = ROOT / 'artifacts/den-room-command-join-20261017/fixture' / ('run-' + uuid.uuid4().hex)
        evidence.mkdir(parents=True, exist_ok=False)
        prefix = PartialPrefixRecorder(evidence / 'partial-home-prefix.jsonl')
        self.addCleanup(prefix.close)
        # Reuse actual production factory + external I/O doubles + public HTTP
        # and real worker setup. No decision/checkpoint/database rows are seeded.
        home = worker_tests.RoomWorkerTests('test_rehashed_forged_ambient_sample_is_refused_without_advancing_public_checkpoint')
        self.addCleanup(home.doCleanups)
        home.setUp()
        journal = home.graph.journal
        with journal.lock:
            self.assertIsNone(journal._recorder)
            journal._recorder = prefix.record  # Explicit partial-prefix test seam.
        home.advance(); home.advance()
        home.now += 1
        home.bundle['external_io'].set_scenario('presence'); home.wait_occupancy()
        cycle = home.graph.step()
        self.assertEqual(cycle['interaction'], 'completed', cycle)
        posted = home.probe.command('tick', t=home.now)
        self.assertEqual(len(posted['decisions']), 1, posted)
        self.assertTrue(posted['commit_posts'], posted)
        commit = json.loads(posted['commit_posts'][-1])
        worker = journal.dynamic.worker
        binding = RoomCommitBinding(deepcopy(home.identity), worker.policy['worker_id'],
            worker.policy['producer_id'], worker.policy['config_digest'],
            journal.dynamic.config['manifest_ref']['digest'], worker.worker_version,
            tuple(f['fixture_id'] for f in worker.policy['room_policy']['fixtures']))
        source = CommittedRoomSource(journal, binding=binding, current=lambda: True)
        accepted = source.install_commit(commit)
        self.assertEqual(accepted['receipt']['status'], 'accepted')
        self.assertEqual(accepted['commit'], commit)
        modules = load_canonical_modules()
        provider = CanonicalProjectionProvider(**modules.provider_pins,
            mixer_source_sha256=MIXER_SOURCE_SHA256, ambient_mix=modules.outputs.ambient_mix)
        projector = RoomCommandProjector(source, room_policy=worker.policy['room_policy'], provider=provider)
        projection = projector.project()
        for fixture in projection['fixtures']:
            # Independently literal fixture: .2 background * .4 scale, 3000K;
            # sampled .08 -> level20, mired333 -> W/N/C [4,13,0].
            self.assertAlmostEqual(fixture['domains'][0]['original']['sample']['brightness_fraction'], .08)
            self.assertEqual(fixture['domains'][0]['original']['sample']['cct_kelvin'], 3000)
            self.assertEqual(fixture['ambient']['logical_channels'], [4, 13, 0] * 7)
        native = self.native
        header = canonical_tests.header(native.manifest)
        for row, fid in zip(header['fixtures'], binding.fixture_ids): row['fixture_id'] = fid
        header['session_id'] = 'actual-room-command-authored-native'
        recorder = RoomCanonicalRecorder(evidence / 'native-originals.jsonl', header=header)
        room = RoomCanonicalPlant(str(native.manifest_path), canonical_tests.PIN, recorder,
            fixture_ids=binding.fixture_ids, process_factory=native.provider)
        scenario = native.scenarios
        home_us = int(Decimal(str(home.now)) * 1000000)

        class JoinedPlant(scenario.Plant):
            def __init__(selected, index):
                selected.index, selected.now, selected.sequence = index, 1_000_000, 0
                selected.uncertainty_us = 0  # Fixed authored mapping; later mutation must refuse.
                selected.effects, selected.events, selected.operations, selected.responses = [], [], [], []
                selected.key = bytes(n + index for n in range(32))
                selected.device = bytes(n + index * 16 for n in range(16))
                selected.context = modules.plan.example_context()
                selected.context.update(boot_generation=str(index + 1), session_id=str(index + 2), ownership_generation=str(index + 4))
                selected.context_raw = modules.plan.encode_context(selected.context)
                selected.context_sha = hashlib.sha256(selected.context_raw).digest()
                selected.description = selected.ask('describe')

            def ask(selected, op, **fields):
                return selected.collect(room.step(selected.index, op, **fields))

            def port(selected, *, completed=False, failed=False, running=True, tx_count=0, frames=(), at=None):
                if at is not None: selected.now = at
                return selected.ask('can_port', at_device_us=str(selected.now), completed=completed,
                    failed=failed, running=running, tx_count=tx_count, frames=list(frames))

            def bootstrap(selected):
                # Same original SESSION_OPEN / TIME_EXCHANGE sequence as the
                # pinned helper; only its explicit simulated Home UTC mapping
                # differs from that helper's small epoch example.
                for body in scenario.wire.session_open_bodies(selected.context_raw, selected.index + 4):
                    code, raw, _ = selected.exchange(scenario.wire.Operation.SESSION_OPEN, body, bootstrap=True)
                    self.assertEqual(code, scenario.wire.Code.OK)
                code, raw, _ = selected.exchange(scenario.wire.Operation.TIME_EXCHANGE,
                    scenario.wire.clock_query_body(home_us))
                self.assertEqual(code, scenario.wire.Code.OK)
                echoed, ingress, built = struct.unpack('<3Q', raw[2:])
                self.assertEqual((echoed, ingress, built), (home_us, selected.now, selected.now))
                selected.mapping_device, selected.mapping_home = built, home_us
                code, _, _ = selected.exchange(scenario.wire.Operation.TIME_EXCHANGE,
                    scenario.wire.clock_install_body(1, built, home_us, 0, 0))
                self.assertEqual(code, scenario.wire.Code.ADMITTED)

            def clock(selected):
                now_home = selected.mapping_home + selected.now - selected.mapping_device
                return SimulatedClockObservation(now_home, selected.now, selected.mapping_home, selected.uncertainty_us,
                    selected.mapping_home + 60_000_000, 1, selected.now, selected.now)

        members, plants, dispatchers = [], [], []
        join = None
        terminal = None
        try:
            self.assertEqual(len(room.start()['members']), 4)
            for index, fixture_id in enumerate(binding.fixture_ids):
                p = JoinedPlant(index); plants.append(p)
                p.bootstrap(); p.healthy(); p.effect('lighting'); p.effect('can')
                native_binding = modules.zha.RuntimeBinding('00:11:22:33:44:55:66:77',
                    '00:11:22:33:44:55:66:' + format(128 + index, '02x'), format(index + 1, '02x') * 16,
                    p.device, p.key, index + 1, index + 2, index + 4, p.context_raw, 'aa' * 32, 'bb' * 32)
                raw_cap, _ = p.document(scenario.wire.Operation.CAPABILITIES)
                raw_status, captured = p.document(scenario.wire.Operation.STATUS)
                decoded = modules.zha.decode_snapshot_v3(raw_status, binding=native_binding, captured_device_us=captured)
                modules.zha.decode_capabilities_v2(raw_cap, p.context['pins'])
                ref = modules.outputs.CurrentStateReference(native_binding.binding_sha256,
                    hashlib.sha256(raw_status).hexdigest(), hashlib.sha256(raw_cap).hexdigest(),
                    index + 1, index + 2, index + 4, p.context_sha.hex(), int(decoded['state_generation']),
                    int(decoded['revision_id']), tuple(d['generation'] for d in decoded['domains']),
                    captured, p.now, p.now, 250000, 'simulated')
                initial = CanonicalRoomObservation(ref, raw_status, raw_cap, 1)
                resolver = RoomCommandStateResolver(source, projector, fixture_id, modules=modules,
                    native_binding=native_binding, initial_observation=initial, clock_observer=p.clock,
                    generation=RoomCommandGeneration(1, home_us + 60_000_000))
                pins = {**p.context['pins'], **{key: hashlib.sha256(('authored-' + key).encode()).hexdigest()
                    for key in PIN_NAMES if key not in p.context['pins']}}
                policy = CanonicalFixtureAuthorityPolicy(fixture_id, p.device.hex(), deepcopy(p.context), pins,
                    p.context['principal_id'],
                    CanonicalIssuerPolicy('autonomous-ambient', 'home-ambient-' + str(index), '1', 'authored-ambient-lookup-' + str(index)),
                    CanonicalIssuerPolicy('autonomous-task', 'home-task-' + str(index), '1', 'authored-task-lookup-' + str(index)),
                    deepcopy(home.identity), {key: accepted['room_commit_ref'][key] for key in
                        ('configuration_digest', 'manifest_digest', 'worker_version', 'room_output_schema')},
                    deepcopy(modules.provider_pins))
                holder = {}
                authority = CanonicalFixtureAuthority(journal, policy=policy, codec=modules.plan, binding=native_binding,
                    decode_status=modules.zha.decode_snapshot_v3, decode_capabilities=modules.zha.decode_capabilities_v2,
                    current_resolver=resolver.resolve, reservation_reader=lambda g, r, h=holder: h['dispatcher'].row(g, r),
                    command_validator=resolver.validate, clock=lambda p=p: p.clock().now_home_us, create=True)
                dispatcher = CanonicalFixtureDispatcher(journal, authority=authority, codec=modules.plan,
                    clock_home_us=lambda p=p: p.clock().now_home_us, create=True, call_timeout_ms=3000)
                holder['dispatcher'] = dispatcher; dispatchers.append(dispatcher)
                cursor = modules.adapter.SessionCursor(index + 1, index + 2, p.context_sha.hex(),
                    p.sequence, 0, 0, 0)  # Exact original consumed bootstrap/CAP/STATUS packet sequence.
                adapter = RoomCanonicalAdapter(room, index=index, dispatcher=dispatcher, binding=native_binding,
                    session_cursor=cursor, clock_observer=p.clock,
                    submit_credential='authored-submit-' + str(index), observe_credential='authored-observe-' + str(index))
                resolver.replace_observation(adapter.observe())
                members.append(RoomCommandMember(fixture_id, resolver, dispatcher, adapter))
            join = CanonicalRoomCommandJoin(source, projector, members=tuple(members))
            result = join.dispatch_current()
            self.assertEqual(result['room_commit_ref'], accepted['room_commit_ref'])
            self.assertEqual([r['fixture_id'] for r in result['fixtures']], list(binding.fixture_ids))
            for index, row in enumerate(result['fixtures']):
                self.assertEqual(row['room_commit_ref'], accepted['room_commit_ref'])
                self.assertEqual(row['ambient']['status'], 'dispatched', row)
                dispatch = row['ambient']['dispatch']
                self.assertEqual(dispatch['record']['adapter_disposition'], 'ADMITTED', dispatch)
                admitted = dispatch['record']['admitted']
                self.assertEqual(admitted['originals']['room_commit_ref'], accepted['room_commit_ref'])
                self.assertEqual(admitted['plan']['frame'], [4, 13, 0] * 7 + [0, 0, 0])
                self.assertEqual(admitted['plan']['field_mask'], [3] * 7 + [0])
                self.assertEqual(row['task']['status'], 'refused')
                self.assertEqual(row['task']['reason'], 'spotlight_semantic_CCT_mapping_unavailable')
                applied = plants[index].tick()
                scenario.assert_apply_batches(applied, [4, 13, 0] * 7 + [0, 0, 0])
                plants[index].tick()
                self.assertTrue(any(op['kind'] == 'wire_end_transmission' for op in applied['canonical_ops']))
                self.assertFalse(result['physical_permission'])
                self.assertFalse(result['native_achieved_truth'])
            self.assertTrue(any(r['kind'] == 'fixture_canonical_grant_commit' for r in prefix.records))
            self.assertTrue(any(r['kind'] == 'fixture_canonical_dispatch_commit' for r in prefix.records))
            # Public exact repeat must read the original durable outcomes,
            # without another STATUS/CAP exchange, reservation or native step.
            native_before_retry = [recorder.validated_response(i) for i in range(4)]
            records_before_retry = len(prefix.records)
            reservations_before_retry = [deepcopy(r['data']) for r in prefix.records
                if r['kind'] == 'fixture_canonical_dispatch_prepare' and r['data']['operation'] == 'reserve']
            self.assertEqual(len(reservations_before_retry), 4)
            repeated = join.dispatch_current()
            self.assertEqual(repeated['room_commit_ref'], result['room_commit_ref'])
            for original, again in zip(result['fixtures'], repeated['fixtures']):
                self.assertEqual(again['fixture_id'], original['fixture_id'])
                self.assertEqual(again['room_commit_ref'], original['room_commit_ref'])
                self.assertEqual(again['ambient']['request_id'], original['ambient']['request_id'])
                self.assertEqual(again['ambient']['dispatch'], original['ambient']['dispatch'])
                self.assertTrue(again['ambient']['exact_retry'])
                self.assertEqual(again['task'], original['task'])
            self.assertEqual([recorder.validated_response(i) for i in range(4)], native_before_retry)
            self.assertEqual(len(prefix.records), records_before_retry)
            self.assertEqual([r['data'] for r in prefix.records
                if r['kind'] == 'fixture_canonical_dispatch_prepare' and r['data']['operation'] == 'reserve'],
                reservations_before_retry)
            # The same mapping identity cannot silently acquire new uncertainty
            # after success. This source-only refusal performs no native I/O.
            plants[0].uncertainty_us = 1
            try:
                with self.assertRaisesRegex(FixtureAuthorityDenied, 'clock renewal requires explicit successor'):
                    members[0].resolver.resolve('autonomous-ambient')
            finally:
                plants[0].uncertainty_us = 0
            self.assertEqual([recorder.validated_response(i) for i in range(4)], native_before_retry)
            self.assertEqual(len(prefix.records), records_before_retry)
        finally:
            if join is not None:
                join.quiesce(); join.drain()
            else:
                for dispatcher in dispatchers: dispatcher.quiesce(); dispatcher.drain()
            terminal = room.close()
            (evidence / 'gate-limits.json').write_text(json.dumps(dict(
                capture_prefix_incomplete=True, historical_fixture_warmup_unarchived=True,
                full_worker_replay=False, real_camera_or_models=False, physical_permission=False,
                task_MOVE_qualified=False, spotlight_CCT_mapping_available=False,
                room_commit_ref=accepted['room_commit_ref'], native_receipt=terminal), indent=2), encoding='utf-8')
        self.assertEqual(terminal['status'], 'complete', terminal)
        self.assertTrue(all(d.drain()['safe_to_close_journal'] for d in dispatchers))


if __name__ == '__main__': unittest.main()
