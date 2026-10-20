"""Public archive boundary tests with independently authored original byte streams."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from room_fake_plant_replay import replay_room_native_archive, verify_room_native_archive


ABI = 'c12-fake-plant-bridge-1'
BASE = 'd97c6bf58353d5f808774893c7559e799008f791'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def header():
    return {'session_id': 'authored-four-native', 'producer': {
        'abi': ABI, 'build_id': 'authored-beda4ec', 'manifest_sha256': 'a' * 64,
        'source_sha256': 'b' * 64, 'config_sha256': 'c' * 64, 'executable_sha256': 'd' * 64},
        'fixtures': [{'fixture_index': i, 'fixture_id': 'fixture-' + str(i),
                      'device_id': format(i + 1, '032x'), 'boot': str(i + 1),
                      'launch_id': 'launch-' + str(i)} for i in range(4)],
        'evidence_source': 'simulated', 'physical_permission': False}


def requests_and_responses(h, index):
    """Authored protocol originals; no producer or recording implementation imported."""
    p, f = h['producer'], h['fixtures'][index]
    operations = ['describe', 'initialize', 'exchange', 'observe', 'tick', 'set_effect', 'disconnect', 'close']
    describe = {'build_id': p['build_id'], 'base_revision': BASE,
                'source_sha256': p['source_sha256'], 'config_sha256': p['config_sha256'],
                'operations': operations,
                'topology': {'fixture_count': 4, 'axis_count': 2, 'lighting_channels': 24,
                             'ambient_zones': 7, 'spotlight_channels': [21, 22, 23],
                             'spotlight_names': ['TEST_WARM_WHITE', 'TEST_NEUTRAL_WHITE', 'TEST_PC_AMBER']},
                'limits': {'request_bytes': 16384, 'response_bytes': 65536, 'steps': 100000,
                           'wire_packet_bytes': 64, 'effects_per_step': 64, 'events_per_step': 64,
                           'admitted_lineages': 100000},
                'explicit_ticks_only': True, 'initial_device_us': '0', 'default_effect': 'rejected',
                'logical_can_only': True, 'supports_axis_acquisition': False,
                'spotlight_CCT_calibrated': False}
    requests, responses = [], []
    for step, (op, fields, extra) in enumerate([
        ('describe', {}, describe),
        ('initialize', {'fixture_index': index, 'expected_config_sha256': p['config_sha256']},
         {'fixture_index': index, 'device_id_hex': f['device_id'], 'boot_generation': f['boot'],
          'at_device_us': '0', 'profile': 'fictional-c12-v1'}),
        ('tick', {'at_device_us': '100'}, {}),
        ('close', {}, {'closed': True, 'terminal': True, 'exit_code': 0, 'exit_reason': 'owner_close'})], 1):
        request = {'schema': ABI, 'version': 1, 'request_id': 'request-' + str(step), 'op': op, **fields}
        response = {'schema': ABI, 'version': 1, 'request_id': request['request_id'], 'op': op,
                    'ok': True, 'step_id': str(step), 'evidence_source': 'simulated',
                    'physical_permission': False, 'binding': None if step == 1 else {
                        'fixture_index': index, 'device_id': f['device_id'], 'boot': f['boot'],
                        'config_sha256': p['config_sha256'], 'source_sha256': p['source_sha256'],
                        'build_id': p['build_id']},
                    'processed_device_us': None if step == 1 else ('0' if step == 2 else '100'),
                    'counters': {key: str(step) if key == 'steps' else '0' for key in
                                 ('steps', 'events', 'effects', 'accepted_observations', 'can_attempts', 'lighting_attempts')},
                    'events': [], 'effects': [], 'accepted_observations': [], **extra}
        requests.append(canonical(request) + b'\n')
        responses.append(canonical(response) + b'\n')
    return requests, responses


def payloads(h=None, stream_transform=None):
    h = h or header()
    result = [('header', copy.deepcopy(h))]
    for index in range(4):
        ordinal = 0

        def emit(kind, raw=None, **fields):
            nonlocal ordinal
            ordinal += 1
            # Intentional decreasing capture timestamps: callback order does not
            # establish capture-time ordering between independent producer pipes.
            stamp = str(9007199254740999 - ordinal)
            event = {'schema': 'c12-fake-plant-io-1', 'version': 1, 'ordinal': str(ordinal),
                     'kind': kind, 'captured_monotonic_ns': stamp,
                     'captured_utc_ns': '18000000000000000001', **fields}
            original = None if raw is None else {'base64': base64.b64encode(raw).decode('ascii'),
                                                 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            result.append(('io', {'fixture_index': index, 'launch_id': h['fixtures'][index]['launch_id'],
                                  'event': event, 'original': original,
                                  'callback_monotonic_ns': '9007199254741000',
                                  'callback_utc_ns': '18000000000000000000'}))

        inputs, outputs = requests_and_responses(h, index)
        if stream_transform is not None:
            inputs, outputs = stream_transform(index, inputs, outputs)
        for request, response in zip(inputs, outputs):
            rid = json.loads(request)['request_id']
            emit('stdin_attempt', request, request_id=rid)
            # Original stdout may arrive before pipe-write-result callback.
            for start in range(0, len(response), 73):
                emit('stdout_bytes', response[start:start + 73])
            emit('stdin_write_result', request_id=rid, reported_bytes=len(request), flush_completed=True,
                 error=None, effect='unknown', flush_captured_monotonic_ns='9007199254740999',
                 flush_captured_utc_ns='18000000000000000000')
        emit('stdout_eof')
        emit('stderr_eof')
        emit('process_exit', exit_code=0)
    result.append(('final', {'status': 'complete', 'failure_reason': None, 'members': [
        {'fixture_index': i, 'launch_id': h['fixtures'][i]['launch_id'], 'close_acknowledged': True,
         'recording_status': {'complete': True, 'cleanup_unconfirmed': False, 'observer_failed': False,
                              'terminal': False, 'active_callbacks': 0}} for i in range(4)]}))
    return result


def write_archive(path, bodies):
    previous = '0' * 64
    with Path(path).open('wb') as stream:
        for sequence, (kind, payload) in enumerate(bodies, 1):
            row = {'schema': 'home-room-fake-plant-record-1', 'version': 1,
                   'sequence': str(sequence), 'kind': kind, 'previous_sha256': previous, 'payload': payload}
            row['record_sha256'] = hashlib.sha256(canonical(row)).hexdigest()
            stream.write(canonical(row) + b'\n')
            previous = row['record_sha256']
    return {'schema': 'home-room-fake-plant-recording-receipt-1', 'version': 1,
            'status': 'complete', 'failure_reason': None, 'path': str(path), 'records': len(bodies),
            'terminal_record_sha256': previous, 'members': copy.deepcopy(bodies[-1][1].get('members', [])),
            'footer_durable': True, 'cleanup_unconfirmed': False, 'native_recomputed': False,
            'home_room_replayed': False, 'evidence_source': 'simulated', 'physical_permission': False}


def renumber_events(bodies):
    """Keep a forged archive structurally ordered so semantic rejection is tested."""
    ordinals = [0] * 4
    for kind, payload in bodies:
        if kind == 'io':
            index = payload['fixture_index']
            ordinals[index] += 1
            payload['event']['ordinal'] = str(ordinals[index])


class RoomFakePlantReplayTests(unittest.TestCase):
    def verify_bodies(self, bodies, **kwargs):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            path = Path(directory) / 'originals.jsonl'
            receipt = write_archive(path, bodies)
            kwargs.setdefault('recording_receipt', receipt)
            return verify_room_native_archive(path, expected_header=header(), **kwargs)

    def assert_incomplete(self, result, reason=None):
        self.assertEqual(result['status'], 'incomplete')
        self.assertNotIn('transcripts', result)
        self.assertFalse(result['native_recomputed'])
        self.assertFalse(result['physical_permission'])
        if reason is not None:
            self.assertEqual(result['reason'], reason)

    def test_four_complete_members_expose_lossless_originals_without_native_claim(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            path = Path(directory) / 'originals.jsonl'
            receipt = write_archive(path, payloads())
            result = verify_room_native_archive(path, expected_header=header(), recording_receipt=receipt)
            self.assertEqual(result['status'], 'native_archive_verified')
            self.assertFalse(result['native_recomputed'])
            self.assertFalse(result['home_room_replayed'])
            self.assertFalse(result['physical_permission'])
            self.assertEqual(result['archive_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(len(result['transcripts']), 4)
            for index, transcript in enumerate(result['transcripts']):
                inputs, outputs = requests_and_responses(header(), index)
                self.assertEqual(transcript['stdin_bytes'], b''.join(inputs))
                self.assertEqual(transcript['stdout_bytes'], b''.join(outputs))
                self.assertEqual(transcript['stderr_bytes'], b'')
                self.assertEqual(transcript['responses'][-1]['exit_reason'], 'owner_close')

    def test_missing_or_failed_durable_receipt_cannot_be_inferred_from_readable_footer(self):
        self.assert_incomplete(self.verify_bodies(payloads(), recording_receipt=None), 'recording_receipt_required')

    def test_original_receipt_failure_and_archive_binding_are_not_repaired_by_readable_bytes(self):
        mutations = [lambda r: r.update(status='incomplete'), lambda r: r.update(footer_durable=False),
                     lambda r: r.update(cleanup_unconfirmed=True), lambda r: r.update(failure_reason='fsync_failed'),
                     lambda r: r.update(records=r['records'] - 1),
                     lambda r: r.update(terminal_record_sha256='0' * 64),
                     lambda r: r['members'][0].update(close_acknowledged=False),
                     lambda r: r.update(physical_permission=True), lambda r: r.update(native_recomputed=True),
                     lambda r: r.update(path=r['path'] + '.another'), lambda r: r.update(extra='unreviewed')]
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            path = Path(directory) / 'originals.jsonl'
            original = write_archive(path, payloads())
            for mutate in mutations:
                with self.subTest(mutation=mutate):
                    receipt = copy.deepcopy(original)
                    mutate(receipt)
                    self.assert_incomplete(verify_room_native_archive(path, expected_header=header(),
                                                                      recording_receipt=receipt))

    def test_outer_rehash_does_not_hide_original_digest_ordinal_identity_or_clock_forgery(self):
        def first_io(bodies):
            return next(p for kind, p in bodies if kind == 'io')

        mutations = [lambda p: p['original'].update(sha256='0' * 64),
                     lambda p: p['original'].update(bytes=p['original']['bytes'] + 1),
                     lambda p: p['original'].update(base64=p['original']['base64'] + '='),
                     lambda p: p['event'].update(ordinal='2'),
                     lambda p: p['event'].update(ordinal=1),
                     lambda p: p.update(launch_id='replacement-launch'),
                     lambda p: p.update(fixture_index=True),
                     lambda p: p['event'].update(captured_monotonic_ns='9007199254741001'),
                     lambda p: p.update(callback_monotonic_ns='09007199254741000'),
                     lambda p: p['event'].update(extra='private_causal_claim')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                bodies = payloads()
                mutate(first_io(bodies))
                self.assert_incomplete(self.verify_bodies(bodies))
        bodies = payloads()
        bodies[0][1]['producer']['source_sha256'] = 'f' * 64
        self.assert_incomplete(self.verify_bodies(bodies), 'expected_header_mismatch')

    def test_partial_unknown_or_invalid_original_pipe_counts_never_expose_confirmed_inputs(self):
        for count in (None, -2147483648, -1, 0, 1, 0xffffffff):
            with self.subTest(count=count):
                bodies = payloads()
                event = next(p['event'] for k, p in bodies if k == 'io' and p['event']['kind'] == 'stdin_write_result')
                event['reported_bytes'] = count
                self.assert_incomplete(self.verify_bodies(bodies), 'unconfirmed_pipe_write')
        for changes in ({'flush_completed': False}, {'error': 'BrokenPipeError'}, {'effect': 'submitted'},
                        {'flush_captured_monotonic_ns': '9007199254741001'},
                        {'flush_captured_monotonic_ns': '0'}):
            with self.subTest(changes=changes):
                bodies = payloads()
                event = next(p['event'] for k, p in bodies if k == 'io' and p['event']['kind'] == 'stdin_write_result')
                event.update(changes)
                self.assert_incomplete(self.verify_bodies(bodies))

    def test_all_four_members_original_close_dual_eof_exit_and_cleanup_are_required(self):
        for missing in ('final', 'stdout_eof', 'stderr_eof', 'process_exit', 'stdin_write_result'):
            with self.subTest(missing=missing):
                bodies = payloads()
                position = next(i for i, (kind, p) in enumerate(bodies) if kind == missing or
                                kind == 'io' and p['event']['kind'] == missing)
                del bodies[position]
                renumber_events(bodies)
                expected = {'final': 'missing_final_footer', 'stdout_eof': 'recording_incomplete',
                            'stderr_eof': 'recording_incomplete', 'process_exit': 'recording_incomplete',
                            'stdin_write_result': 'missing_write_result'}[missing]
                self.assert_incomplete(self.verify_bodies(bodies), expected)
        def no_owner_close(index, inputs, outputs):
            return (inputs[:-1], outputs[:-1]) if index == 0 else (inputs, outputs)
        self.assert_incomplete(self.verify_bodies(payloads(stream_transform=no_owner_close)), 'missing_start_or_owner_close')
        for member_change in ({'recording_status': None}, {'close_acknowledged': False}):
            bodies = payloads()
            bodies[-1][1]['members'][3].update(member_change)
            self.assert_incomplete(self.verify_bodies(bodies), 'recording_incomplete')
        for key, value in (('cleanup_unconfirmed', True), ('observer_failed', True), ('terminal', True),
                           ('active_callbacks', 1), ('complete', False)):
            bodies = payloads()
            bodies[-1][1]['members'][3]['recording_status'][key] = value
            self.assert_incomplete(self.verify_bodies(bodies), 'recording_incomplete')
        for exit_code in (-1, -2147483648, 2, 0xffffffff):
            bodies = payloads()
            next(p['event'] for k, p in bodies if k == 'io' and p['event']['kind'] == 'process_exit')['exit_code'] = exit_code
            self.assert_incomplete(self.verify_bodies(bodies), 'recording_incomplete')

    def test_truncated_or_noncanonical_archive_and_fixed_budgets_refuse(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            path = Path(directory) / 'originals.jsonl'
            receipt = write_archive(path, payloads())
            original = path.read_bytes()
            for raw in (original[:-1], original + b'\n', original.replace(b'"version":1', b'"version":1.0', 1),
                        original.replace(b'"version":1', b'"version":1,"version":1', 1),
                        original.replace(b'"version":1', b'"version": 1', 1),
                        original.replace(b'"record_sha256":"', b'"record_sha256":"0', 1)):
                with self.subTest(prefix=raw[:100]):
                    path.write_bytes(raw)
                    self.assert_incomplete(verify_room_native_archive(path, expected_header=header(), recording_receipt=receipt))
            path.write_bytes(original)
            for limits in ({'max_records': 3}, {'max_bytes': 10}, {'max_records': 8193}, {'max_bytes': 33554433}):
                with self.subTest(limits=limits):
                    self.assert_incomplete(verify_room_native_archive(path, expected_header=header(),
                                                                      recording_receipt=receipt, **limits))

    def test_original_request_response_correlations_times_and_frame_bounds_are_independent(self):
        def transform(change):
            def run(index, inputs, outputs):
                if index == 0:
                    objects = [json.loads(raw) for raw in outputs]
                    change(objects)
                    outputs = [canonical(obj) + b'\n' for obj in objects]
                return inputs, outputs
            return run

        changes = [lambda r: r[2].update(request_id='request-99'), lambda r: r[2].update(step_id='4'),
                   lambda r: r[2].update(processed_device_us='99'),
                   lambda r: r[2]['binding'].update(device_id='f' * 32),
                   lambda r: r[0].update(spotlight_CCT_calibrated=True),
                   lambda r: r[-1].update(exit_reason='input_eof'),
                   lambda r: r[-1].update(closed=False),
                   lambda r: r[2].update(accepted_observations=[{}]),
                   lambda r: r[2]['counters'].update(events='1'),
                   lambda r: r[2].update(extra='silently_accepted_extension')]
        for change in changes:
            with self.subTest(change=change):
                self.assert_incomplete(self.verify_bodies(payloads(stream_transform=transform(change))))

    def test_structurally_valid_raw_effect_is_not_an_observed_or_recomputed_outcome(self):
        def effects(index, inputs, outputs):
            responses = [json.loads(raw) for raw in outputs]
            responses[2]['effects'] = [{'effect_id': '1', 'step_id': '3', 'processed_device_us': '100',
                'kind': 'lighting_apply', 'at_device_us': '100', 'generation': '7',
                'returned_effect': 'submitted', 'frame24': [17] * 24, 'inhibit': False}]
            for response in responses[2:]:
                response['counters'].update(effects='1', lighting_attempts='1')
            return inputs, [canonical(obj) + b'\n' for obj in responses]

        bodies = payloads(stream_transform=effects)
        result = self.verify_bodies(bodies)
        self.assertEqual(result['status'], 'native_archive_verified')
        self.assertFalse(result['native_recomputed'])
        self.assertEqual(result['transcripts'][0]['responses'][2]['accepted_observations'], [])
        # A valid-looking but oversized emitter value is independently rejected,
        # even after original encoding and every archive chain hash are updated.
        def bad_frame(index, inputs, outputs):
            inputs, outputs = effects(index, inputs, outputs)
            response = json.loads(outputs[2])
            response['effects'][0]['frame24'][23] = 65536
            outputs[2] = canonical(response) + b'\n'
            return inputs, outputs
        self.assert_incomplete(self.verify_bodies(payloads(stream_transform=bad_frame)), 'integer_out_of_range')

    def test_wire_detail_retains_native_uint16_and_closed_wire_code_range(self):
        def exchange(code=1, detail=65535):
            def transform(index, inputs, outputs):
                request = json.loads(inputs[2])
                request.update(op='exchange', ingress_device_us='99', packet_hex='')
                response = json.loads(outputs[2])
                response.update(op='exchange', packet_hex='', reply_hex='', wire={
                    'code': code, 'detail': detail, 'authenticated': False,
                    'ingress_device_us': '99', 'processed_device_us': '100'})
                inputs[2], outputs[2] = canonical(request) + b'\n', canonical(response) + b'\n'
                return inputs, outputs
            return transform
        valid = self.verify_bodies(payloads(stream_transform=exchange()))
        self.assertEqual(valid['status'], 'native_archive_verified')
        self.assertEqual(valid['transcripts'][0]['responses'][2]['wire']['detail'], 65535)
        self.assert_incomplete(self.verify_bodies(payloads(stream_transform=exchange(detail=65536))), 'integer_out_of_range')
        self.assert_incomplete(self.verify_bodies(payloads(stream_transform=exchange(code=16))), 'integer_out_of_range')

    def test_second_successful_disconnect_refuses_even_with_new_original_and_chain_hashes(self):
        def disconnects(repeat):
            def transform(index, inputs, outputs):
                requests = [json.loads(raw) for raw in inputs]
                responses = [json.loads(raw) for raw in outputs]
                requests[2]['op'] = 'disconnect'
                responses[2].update(op='disconnect', disconnected=True)
                if repeat:
                    requests.insert(3, copy.deepcopy(requests[2]))
                    responses.insert(3, copy.deepcopy(responses[2]))
                for step, (request, response) in enumerate(zip(requests, responses), 1):
                    request['request_id'] = response['request_id'] = 'request-' + str(step)
                    response['step_id'] = response['counters']['steps'] = str(step)
                return ([canonical(obj) + b'\n' for obj in requests],
                        [canonical(obj) + b'\n' for obj in responses])
            return transform

        valid = self.verify_bodies(payloads(stream_transform=disconnects(False)))
        self.assertEqual(valid['status'], 'native_archive_verified')
        self.assert_incomplete(self.verify_bodies(payloads(stream_transform=disconnects(True))), 'disconnect_one_shot')

    def test_raw_malformed_stdout_tail_and_stderr_are_retained_but_never_confirmed(self):
        def malformed(index, inputs, outputs):
            if index == 0:
                outputs[-1] = outputs[-1][:-1] + b'\xffnot-json'
            return inputs, outputs
        self.assert_incomplete(self.verify_bodies(payloads(stream_transform=malformed)), 'truncated_stdout')
        def wrong_request(index, inputs, outputs):
            if index == 0:
                inputs[2] = inputs[2].replace(b'"op":"tick"', b'"op":"untrusted"')
            return inputs, outputs
        self.assert_incomplete(self.verify_bodies(payloads(stream_transform=wrong_request)), 'request_operation')
        bodies = payloads()
        position = next(i for i, (kind, p) in enumerate(bodies) if kind == 'io' and p['event']['kind'] == 'stderr_eof')
        diagnostic = copy.deepcopy(bodies[position][1])
        raw = b'\xffdiagnostic prefix\n'
        diagnostic['event']['kind'] = 'stderr_bytes'
        diagnostic['original'] = {'base64': base64.b64encode(raw).decode('ascii'), 'bytes': len(raw),
                                  'sha256': hashlib.sha256(raw).hexdigest()}
        bodies.insert(position, ('io', diagnostic))
        renumber_events(bodies)
        self.assert_incomplete(self.verify_bodies(bodies), 'missing_input_or_stderr')

    def test_eof_exit_and_response_causality_cannot_be_reordered_with_fresh_hashes(self):
        bodies = payloads()
        position = next(i for i, (kind, p) in enumerate(bodies) if kind == 'io' and p['event']['kind'] == 'stdout_eof')
        unexpected = copy.deepcopy(bodies[position - 2])
        self.assertEqual(unexpected[1]['event']['kind'], 'stdout_bytes')
        bodies.insert(position + 1, unexpected)
        renumber_events(bodies)
        self.assert_incomplete(self.verify_bodies(bodies), 'bytes_after_eof')
        bodies = payloads()
        position = next(i for i, (kind, p) in enumerate(bodies) if kind == 'io' and p['event']['kind'] == 'process_exit')
        bodies.insert(position + 1, copy.deepcopy(bodies[position - 1]))
        renumber_events(bodies)
        self.assert_incomplete(self.verify_bodies(bodies), 'event_after_exit')
        bodies = payloads()
        attempt = bodies.pop(1)
        position = next(i for i, (kind, p) in enumerate(bodies) if kind == 'io' and p['event']['kind'] == 'stdin_write_result')
        bodies.insert(position, attempt)
        renumber_events(bodies)
        self.assert_incomplete(self.verify_bodies(bodies), 'response_precedes_attempt')

    def test_explicit_consumer_is_gated_then_compares_original_bytes_not_just_parsed_objects(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            path = Path(directory) / 'originals.jsonl'
            receipt = write_archive(path, payloads())
            calls = []

            def consumer(*, header, transcripts):
                calls.append(header['session_id'])
                return [{key: t[key] for key in ('fixture_index', 'stdin_bytes', 'stdout_bytes', 'stderr_bytes')}
                        for t in transcripts]

            refused = replay_room_native_archive(path, expected_header=header(), consumer=consumer)
            self.assert_incomplete(refused, 'recording_receipt_required')
            self.assertEqual(calls, [])
            result = replay_room_native_archive(path, expected_header=header(), recording_receipt=receipt, consumer=consumer)
            self.assertEqual(result['status'], 'native_archive_recomputed')
            self.assertTrue(result['native_recomputed'])
            self.assertFalse(result['home_room_replayed'])
            self.assertFalse(result['physical_permission'])

            def whitespace_differs(**kwargs):
                streams = consumer(**kwargs)
                streams[0]['stdout_bytes'] = streams[0]['stdout_bytes'].replace(b'"version":1', b'"version": 1', 1)
                return streams

            changed = replay_room_native_archive(path, expected_header=header(), recording_receipt=receipt,
                                                  consumer=whitespace_differs)
            self.assertEqual(changed['status'], 'native_recompute_failed')
            self.assertFalse(changed['native_recomputed'])
            self.assertEqual(changed['reason'], 'native_replay_original_bytes_differ')

    def test_hardlinked_archive_is_refused_before_content_read(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            path, alias = Path(directory) / 'originals.jsonl', Path(directory) / 'alias.jsonl'
            receipt = write_archive(path, payloads())
            try:
                os.link(path, alias)
            except OSError as exc:
                self.skipTest('hardlink fixture unavailable: ' + type(exc).__name__)
            result = verify_room_native_archive(path, expected_header=header(), recording_receipt=receipt)
            self.assert_incomplete(result, 'archive_regular_single_link_required')
            self.assertIsNone(result['archive_sha256'])


if __name__ == '__main__':
    unittest.main()
