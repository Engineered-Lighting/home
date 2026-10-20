"""Bounded canonical consumer controls; native execution is explicitly selected.

Run under the pinned producer offline_guard. No builds, downloads, devices,
network, real model or hardware operations. Authored syntax cases are separate
from the executable-facing source-pinned capture and recomputation case.
"""
from __future__ import annotations
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import room_fake_plant_recording as old_recording
import room_fake_plant_replay as old_replay
from room_canonical_plant import RoomCanonicalPlant
from room_canonical_plant_recording import RoomCanonicalRecorder, RecordingIncomplete, validate_header
from room_canonical_plant_replay import (ABI, ArchiveInvalid, CanonicalStreamValidator,
    verify_room_canonical_archive, replay_room_canonical_archive)

PIN = '789533e2af58b4bf1a7f62be75db3ef118c96d8c4336b57cbd3bdd963d2bacf2'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def header(manifest=None):
    manifest = manifest or {'build_id': 'authored-canonical', 'source_digest': '1' * 64,
        'config_sha256': '2' * 64, 'executable': {'sha256': '3' * 64}}
    return {'session_id': 'authored-canonical-room', 'producer': {'abi': ABI,
        'build_id': manifest['build_id'], 'manifest_sha256': PIN, 'source_sha256': manifest['source_digest'],
        'config_sha256': manifest['config_sha256'], 'executable_sha256': manifest['executable']['sha256']},
        'fixtures': [{'fixture_index': i, 'fixture_id': 'fictional-' + chr(65 + i),
            'device_id': bytes(n + i * 16 for n in range(16)).hex(), 'boot': str(i + 1),
            'launch_id': 'authored-launch-' + str(i)} for i in range(4)],
        'evidence_source': 'simulated', 'physical_permission': False, 'physical_truth': False}


def describe(h):
    # Authored protocol syntax only; native acceptance below uses original bytes.
    return {'schema': ABI, 'version': 1, 'request_id': 'authored-1', 'op': 'describe', 'ok': True,
        'step_id': '1', 'evidence_source': 'simulated', 'physical_permission': False, 'physical_truth': False,
        'binding': None, 'processed_device_us': None, 'report': None, 'canonical_ops': [],
        'events': [], 'effects': [], 'accepted_observations': [],
        'counters': {k: '1' if k == 'steps' else '0' for k in ('steps', 'events', 'effects',
            'accepted_observations', 'can_attempts', 'lighting_attempts', 'canonical_ops')},
        'build_id': h['producer']['build_id'], 'base_revision': 'beda4eca32a9cccf7d0b5d35d91077810b23d6b2',
        'source_sha256': h['producer']['source_sha256'], 'config_sha256': h['producer']['config_sha256'],
        'operations': ['describe', 'initialize', 'exchange', 'observe', 'can_port', 'hal_fault', 'tick',
            'set_effect', 'disconnect', 'close'],
        'topology': {'fixture_count': 4, 'axis_count': 2, 'lighting_channels': 24, 'ambient_zones': 7,
            'domain_count': 8, 'total_domains': 32, 'total_axes': 8, 'total_lighting_channels': 96,
            'spotlight_channels': [21, 22, 23], 'spotlight_names': ['TEST_WARM_WHITE', 'TEST_NEUTRAL_WHITE', 'TEST_PC_AMBER']},
        'limits': {'request_bytes': 16384, 'response_bytes': 1048576, 'steps': 100000,
            'wire_packet_bytes': 64, 'effects_per_step': 64, 'events_per_step': 64, 'admitted_lineages': 100000},
        'explicit_ticks_only': True, 'initial_device_us': '0', 'default_effect': 'rejected',
        'logical_can_only': False, 'supports_axis_acquisition': True, 'spotlight_CCT_calibrated': False,
        'capture_layer': 'canonical_ports_and_shared_production_lighting_driver',
        'coordinate_contract': 'joint_input_once_to_raw_motor_output'}


def raw_event(ordinal, kind, **fields):
    return {'schema': 'c12-fake-plant-io-1', 'version': 1, 'ordinal': str(ordinal), 'kind': kind,
        'captured_monotonic_ns': str(time.monotonic_ns()), 'captured_utc_ns': str(time.time_ns()), **fields}


class CanonicalSyntaxTests(unittest.TestCase):
    def test_parent_close_attempts_owned_cleanup_after_start_without_response(self):
        from unittest.mock import patch
        from room_fake_plant import RoomFakePlantRefusal
        created = []
        class Delegate:
            def __init__(self, *args, **kwargs): self.closes = 0; created.append(self)
            def start(self, _): raise RuntimeError('authored_partial_start_before_original_response')
            def close(self):
                self.closes += 1
                return {'closed': True, 'physical_permission': False}
            @property
            def recording_status(self):
                return {'complete': False, 'cleanup_unconfirmed': False, 'observer_failed': False,
                        'terminal': True, 'active_callbacks': 0}
        with tempfile.TemporaryDirectory(prefix='canonical-partial-start-') as directory:
            recorder = RoomCanonicalRecorder(Path(directory) / 'original.jsonl', header=header())
            room = RoomCanonicalPlant('authored-no-native-manifest', PIN, recorder,
                fixture_ids=[f['fixture_id'] for f in header()['fixtures']], process_factory=Delegate)
            # Authored control of the external binding only; use the actual
            # inherited start/member ownership/cleanup/finalization path.
            with patch.object(RoomCanonicalPlant, '_binding', lambda owner: setattr(owner, '_header', recorder.header)):
                with self.assertRaises(RoomFakePlantRefusal): room.start()
            receipt = room.close()
            self.assertEqual(len(created), 1)
            self.assertEqual(created[0].closes, 1)
            self.assertEqual(receipt['status'], 'incomplete')
            self.assertFalse(receipt['members'][0]['close_acknowledged'])

    def test_cached_prior_response_cannot_acknowledge_a_new_member_call(self):
        from room_canonical_plant import _RecordedMember
        from room_fake_plant import RoomFakePlantRefusal
        class Recorded:
            value = None
            def validated_response(self, _): return copy.deepcopy(self.value)
        class Delegate:
            def __init__(self, recorded): self.recorded = recorded; self.calls = 0; self.closes = 0
            def start(self, index):
                self.recorded.value = {'step_id': '2', 'op': 'initialize', 'fixture_index': index}
                return copy.deepcopy(self.recorded.value)
            def command(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1: self.recorded.value = {'step_id': '3', 'op': 'authored-command'}
                return copy.deepcopy(self.recorded.value)
            request = command; can_port = command; hal_fault = command
            def close(self):
                self.closes += 1; self.recorded.value = {'step_id': '4', 'op': 'close', 'closed': True}
                return copy.deepcopy(self.recorded.value)
        for method, args in (('request', ('tick',)), ('can_port', (1,)), ('hal_fault', (1, 'ledc_same_cycle_stale'))):
            with self.subTest(method=method):
                recorded = Recorded(); delegate = Delegate(recorded); member = _RecordedMember(delegate, recorded)
                member.start(0)
                getattr(member, method)(*args)
                with self.assertRaisesRegex(RoomFakePlantRefusal, 'canonical_response_not_fresh'):
                    getattr(member, method)(*args)
                original_close = member.close()
                self.assertEqual(member.close(), original_close)
                self.assertEqual(delegate.closes, 1)

    def test_explicit_header_refuses_bridge1_and_physical_claims(self):
        h = header(); validate_header(h)
        with self.assertRaises(RecordingIncomplete): old_recording.validate_header(h)
        for family, key, value in (('producer', 'abi', 'c12-fake-plant-bridge-1'),
                                   (None, 'physical_truth', True)):
            changed = copy.deepcopy(h)
            (changed[family] if family else changed)[key] = value
            with self.assertRaises(RecordingIncomplete): validate_header(changed)

    def test_original_bad_response_is_durable_before_parse_refusal(self):
        with tempfile.TemporaryDirectory(prefix='canonical-raw-') as directory:
            path = Path(directory) / 'original.jsonl'
            recorder = RoomCanonicalRecorder(path, header=header())
            callback = recorder.observer(0)
            request = canonical(dict(schema=ABI, version=1, request_id='authored-1', op='describe')) + b'\n'
            callback(raw_event(1, 'stdin_attempt', request_id='authored-1', raw=request))
            bad = b'{"bad":\n'
            with self.assertRaises(RecordingIncomplete):
                callback(raw_event(2, 'stdout_bytes', raw=bad))
            rows = [json.loads(line) for line in path.read_bytes().splitlines()]
            self.assertEqual(base64.b64decode(rows[-1]['payload']['original']['base64']), bad)
            members = [dict(fixture_index=i, launch_id=f['launch_id'], close_acknowledged=False,
                            recording_status=None) for i, f in enumerate(header()['fixtures'])]
            receipt = recorder.finalize(members)
            self.assertEqual(receipt['status'], 'incomplete')
            self.assertEqual(verify_room_canonical_archive(path, expected_header=header(), recording_receipt=receipt)['status'], 'incomplete')

    def test_pinned_describe_counter_identity_clock_and_physical_mutations_refuse(self):
        changes = [lambda r: r.update(physical_truth=True),
                   lambda r: r.update(source_sha256='9' * 64),
                   lambda r: r['counters'].update(canonical_ops='1'),
                   lambda r: r.update(step_id='2'),
                   lambda r: r.update(processed_device_us='1')]
        for change in changes:
            with self.subTest(change=change):
                validator = CanonicalStreamValidator(header(), 0)
                validator.request(canonical(dict(schema=ABI, version=1, request_id='authored-1', op='describe')) + b'\n')
                response = describe(header()); change(response)
                with self.assertRaises(ArchiveInvalid): validator.stdout(canonical(response) + b'\n')

    def test_response_bound_and_unfinished_original_stream_refuse(self):
        validator = CanonicalStreamValidator(header(), 0)
        validator.request(canonical(dict(schema=ABI, version=1, request_id='authored-1', op='describe')) + b'\n')
        for _ in range(255): validator.stdout(b'x' * 4096)
        with self.assertRaisesRegex(ArchiveInvalid, 'byte_bound'): validator.stdout(b'x' * 4096)
        with self.assertRaisesRegex(ArchiveInvalid, 'incomplete'): validator.finish()

    def test_canonical_ordinal_generation_coordinate_clock_lineage_and_budget_refuse(self):
        from room_canonical_plant_replay import _operations
        report = {'phase': 'idle', 'result': 'ok', 'revision_id': '0', 'motion_id': '0', 'state_generation': '0',
            'motion_deadline_us': '0', 'protective_device_deadline_us': '0', 'context_sha256': '0' * 64,
            'boot_generation': '1', 'session_id': '0', 'ownership_generation': '0', 'original_plan': None}
        operation = {'operation_id': '1', 'step_id': '3', 'processed_device_us': '1', 'at_device_us': '1',
            'generation': '1', 'kind': 'can_port_transmit_once', 'lineage': report,
            'fields': {'can_id': 321, 'dlc': 8, 'extended': False, 'remote': False,
                'data_hex': 'a400280018fcffff', 'axis': 0, 'logical_opcode': 164, 'protective': False,
                'feedback': False, 'speed_stage': False, 'motion_generation': '10', 'admitted_us': '0',
                'attempted_us': '1', 'returned': True, 'coordinate_space': 'raw_motor_post_arbiter'}}
        response = {'step_id': '3', 'processed_device_us': '1', 'canonical_ops': [operation],
                    'counters': {'canonical_ops': '1'}}
        _operations(copy.deepcopy(response), {'operations': 0}, header()['fixtures'][0])
        mutations = [lambda r: r['canonical_ops'][0].update(operation_id='2'),
            lambda r: r['canonical_ops'][0].update(generation='-1'),
            lambda r: r['canonical_ops'][0].update(at_device_us='2'),
            lambda r: r['canonical_ops'][0]['fields'].update(coordinate_space='joint_input_pre_arbiter'),
            lambda r: r['canonical_ops'][0]['fields'].update(attempted_us='2'),
            lambda r: r['canonical_ops'][0].update(lineage=None),
            lambda r: r['canonical_ops'][0]['lineage'].pop('context_sha256'),
            lambda r: r['canonical_ops'][0]['fields'].update(returned=1),
            lambda r: r['counters'].update(canonical_ops='0'),
            lambda r: r.update(canonical_ops=[operation] * 2049)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(response); mutate(changed)
                with self.assertRaises(ArchiveInvalid): _operations(changed, {'operations': 0}, header()['fixtures'][0])

    def test_typed_axis_injection_and_generic_new_operation_are_not_authority(self):
        from room_canonical_plant_replay import _request
        for fields in ({'op': 'observe', 'at_device_us': '1', 'observation': {'kind': 'axis'}},
                       {'op': 'can_port', 'at_device_us': '1', 'completed': True, 'failed': False,
                        'running': True, 'tx_count': 0, 'frames': [{'can_id': 321}]}):
            raw = canonical(dict(schema=ABI, version=1, request_id='invalid-input', **fields)) + b'\n'
            with self.assertRaises(ArchiveInvalid): _request(raw, 0, header())


class CanonicalNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('DEN_CANONICAL_MANIFEST')
        expected = os.environ.get('DEN_CANONICAL_MANIFEST_SHA256')
        if not supplied or expected != PIN:
            raise RuntimeError('Explicit reviewed DEN_CANONICAL_MANIFEST and exact SHA256 required; no automatic native fallback')
        cls.manifest_path = Path(supplied)
        if hashlib.sha256(cls.manifest_path.read_bytes()).hexdigest() != PIN:
            raise RuntimeError('Reviewed manifest changed before import')
        cls.manifest = json.loads(cls.manifest_path.read_bytes())
        cls.source = Path(cls.manifest['source']['dir'])
        for relative, expected_source in cls.manifest['source']['files'].items():
            if hashlib.sha256((cls.source / relative).read_bytes()).hexdigest() != expected_source:
                raise RuntimeError('Reviewed source changed before import: ' + relative)
        sys.path.insert(0, str(cls.source / 'tools'))
        from c12_canonical_capture import CanonicalCaptureProcess
        cls.provider = CanonicalCaptureProcess
        spec = importlib.util.spec_from_file_location('_home_canonical_scenarios', cls.source / 'tools/test_c12_canonical_capture.py')
        cls.scenarios = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.scenarios)

    def test_actual_native_room_capture_commands_primitives_retry_and_exact_recompute(self):
        with tempfile.TemporaryDirectory(prefix='canonical-native-room-') as directory:
            path = Path(directory) / 'original.jsonl'; h = header(self.manifest)
            recorder = RoomCanonicalRecorder(path, header=h)
            room = RoomCanonicalPlant(str(self.manifest_path), PIN, recorder,
                fixture_ids=[f['fixture_id'] for f in h['fixtures']], process_factory=self.provider)
            scenario = self.scenarios
            class Joined(scenario.Plant):
                def __init__(selected, index):
                    selected.index = index; selected.now = 1_000_000; selected.sequence = 0
                    selected.effects = []; selected.events = []; selected.operations = []; selected.responses = []
                    selected.key = bytes(n + index for n in range(32)); selected.device = bytes(n + index * 16 for n in range(16))
                    selected.context = scenario.plan.example_context()
                    selected.context.update(boot_generation=str(index + 1), session_id=str(index + 2), ownership_generation=str(index + 4))
                    selected.context_raw = scenario.plan.encode_context(selected.context)
                    selected.context_sha = hashlib.sha256(selected.context_raw).digest()
                    selected.description = selected.ask('describe')
                def ask(selected, op, **fields):
                    return selected.collect(room.step(selected.index, op, **fields))
                def port(selected, *, completed=False, failed=False, running=True, tx_count=0, frames=(), at=None):
                    if at is not None: selected.now = at
                    return selected.ask('can_port', at_device_us=str(selected.now), completed=completed,
                        failed=failed, running=running, tx_count=tx_count, frames=list(frames))
            receipt = None
            try:
                self.assertEqual(len(room.start()['members']), 4)
                plant = Joined(0)
                self.assertEqual(plant.description['topology']['total_domains'], 32)
                self.assertEqual(plant.description['topology']['total_lighting_channels'], 96)
                self.assertEqual(plant.description['topology']['total_axes'], 8)
                self.assertFalse(plant.description['spotlight_CCT_calibrated'])
                plant.bootstrap(); plant.healthy(); plant.effect('lighting'); plant.effect('can')
                capabilities, _ = plant.document(scenario.wire.Operation.CAPABILITIES)
                status, _ = plant.document(scenario.wire.Operation.STATUS)
                self.assertEqual(len(capabilities), 404); self.assertEqual(len(status), 998)
                for revision, (mired, triple) in enumerate(((556, [4095, 0, 0]), (286, [0, 2649, 0]), (154, [0, 0, 2537])), 1):
                    ambient = plant.ambient_plan([{'zone': zone, 'level': 254, 'mired': mired} for zone in range(7)], revision)
                    self.assertEqual(plant.submit(ambient)[0], scenario.wire.Code.ADMITTED)
                    result = plant.tick(); scenario.assert_apply_batches(result, triple * 7 + [0, 0, 0]); plant.tick()
                current = plant.status()
                command = plant.plan(status=current, revision=4)
                command['transition_us'] = 100_000
                command['frame'][:21] = current['applied'][:21]
                command['ambient_level'] = current['current_semantic_target']['ambient_level'][:]
                command['ambient_mired'] = current['current_semantic_target']['ambient_mired'][:]
                self.assertEqual(command['predecessor_id'], '3')
                code, _, admitted = plant.submit(command)
                self.assertEqual(code, scenario.wire.Code.ADMITTED)
                duplicate = plant.ask('exchange', at_device_us=str(plant.now + 1), ingress_device_us=str(plant.now + 1),
                                      packet_hex=admitted['packet_hex']); plant.now += 1
                self.assertEqual(duplicate['reply_hex'], admitted['reply_hex'])
                self.assertEqual(duplicate['canonical_ops'], []); self.assertEqual(duplicate['effects'], [])
                plant.tick(); dark = plant.now; plant.tick(); plant.tick(dark + 100_000); plant.drain_can()
                plant.tick(); plant.drain_can()
                axes = plant.transmissions(0xa4)
                self.assertEqual([(row['fields']['can_id'], row['fields']['data_hex']) for row in axes],
                                 [(0x141, 'a400280018fcffff'), (0x142, 'a4000a0024faffff')])
                self.assertTrue(all(row['lineage']['original_plan']['predecessor_id'] == '3' for row in axes))
                lit_at = scenario.arrive_and_settle(plant)
                plant.tick(); plant.drain_can((1000, -2000)); plant.tick()
                plant.now = lit_at + 100_001
                result = plant.tick(); scenario.assert_apply_batches(result, command['frame'])
                plant.drain_can((1000, -2000)); plant.tick()
                final = plant.status()
                self.assertEqual(final['applied'][21:], [1000, 200, 300])
                self.assertFalse(final['measured_light']); self.assertFalse(final['mechanical_rest_confirmed'])
                fault = plant.ask('hal_fault', at_device_us=str(plant.now), mode='ledc_same_cycle_stale')
                self.assertEqual(fault['mode'], 'ledc_same_cycle_stale')
                expired = plant.tick(int(command['support_expiry_home_us']) + 1)
                self.assertTrue(any(row['kind'] in ('enable_request', 'guard_trip') and row['fields']['enabled'] is False
                                    for row in expired['canonical_ops']))
            finally:
                receipt = room.close()
            self.assertEqual(receipt['status'], 'complete', receipt)
            verified = verify_room_canonical_archive(path, expected_header=h, recording_receipt=receipt)
            self.assertEqual(verified['status'], 'native_archive_verified', verified.get('reason'))
            self.assertFalse(verified['home_room_replayed']); self.assertFalse(verified['physical_truth'])
            self.assertEqual(len(verified['transcripts']), 4)
            self.assertTrue(all(t['responses'][1]['canonical_ops'] for t in verified['transcripts']))
            self.assertEqual(old_replay.verify_room_native_archive(path, expected_header=h, recording_receipt=receipt)['status'], 'incomplete')

            def recompute(*, header, transcripts):
                results = []
                for transcript in transcripts:
                    self.assertEqual(hashlib.sha256(Path(self.manifest['executable']['path']).read_bytes()).hexdigest(),
                                     self.manifest['executable']['sha256'])
                    process = subprocess.run([self.manifest['executable']['path']], input=transcript['stdin_bytes'],
                        capture_output=True, timeout=30)
                    self.assertEqual(process.returncode, 0)
                    results.append({'fixture_index': transcript['fixture_index'], 'stdin_bytes': transcript['stdin_bytes'],
                                    'stdout_bytes': process.stdout, 'stderr_bytes': process.stderr})
                return results
            replay = replay_room_canonical_archive(path, expected_header=h, recording_receipt=receipt, consumer=recompute)
            self.assertEqual(replay['status'], 'native_archive_recomputed', replay.get('reason'))
            self.assertFalse(replay['home_room_replayed']); self.assertFalse(replay['physical_truth'])
            missing_receipt = verify_room_canonical_archive(path, expected_header=h)
            self.assertEqual(missing_receipt['status'], 'incomplete')
            truncated = Path(directory) / 'truncated.jsonl'; truncated.write_bytes(path.read_bytes()[:-1])
            self.assertEqual(verify_room_canonical_archive(truncated, expected_header=h, recording_receipt=receipt)['status'], 'incomplete')
            bad_receipt = copy.deepcopy(receipt); bad_receipt['status'] = 'incomplete'
            self.assertEqual(verify_room_canonical_archive(path, expected_header=h, recording_receipt=bad_receipt)['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
