"""Strict canonical-response validation over existing four-member raw archives.

No provider import, native execution or command encoding. Raw originals are
never rewritten. Only an explicit trusted consumer may recompute a verified
transcript; verification alone claims neither Home replay nor physical truth.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
import re
import threading

import room_fake_plant_replay as legacy
from room_canonical_plant_recording import ABI, RECORD_SCHEMA, RECEIPT_SCHEMA, validate_header

ArchiveInvalid = legacy.ArchiveInvalid
need, keys, integer, u64 = legacy._require, legacy._keys, legacy._int, legacy._u64
canonical = legacy._canonical
BASE = 'beda4eca32a9cccf7d0b5d35d91077810b23d6b2'
RESPONSE_BYTES = 1048576
OPS = ('describe', 'initialize', 'exchange', 'observe', 'can_port', 'hal_fault', 'tick',
       'set_effect', 'disconnect', 'close')
REQUEST_EXTRAS = {**legacy.REQUEST_EXTRAS,
    'can_port': ('at_device_us', 'completed', 'failed', 'running', 'tx_count', 'frames'),
    'hal_fault': ('at_device_us', 'mode')}
CAN_FIELDS = ('can_id', 'dlc', 'extended', 'remote', 'data_hex')
REPORT_FIELDS = ('phase', 'result', 'revision_id', 'motion_id', 'state_generation',
    'motion_deadline_us', 'protective_device_deadline_us', 'context_sha256',
    'boot_generation', 'session_id', 'ownership_generation', 'original_plan')
PLAN_FIELDS = ('plan_sha256', 'revision_id', 'predecessor_id', 'motion_id', 'issued_home_us',
    'support_expiry_home_us', 'start_deadline_device_us', 'evidence_not_after_home_us',
    'protection_not_after_home_us', 'authority_not_after_home_us', 'clock_qualification_not_after_home_us')
OP_FIELDS = {
    'can_port_transmit_once': CAN_FIELDS + ('axis', 'logical_opcode', 'protective', 'feedback',
        'speed_stage', 'motion_generation', 'admitted_us', 'attempted_us', 'returned', 'coordinate_space'),
    'can_observation': ('axis', 'opcode', 'observation', 'observed_us', 'result'),
    'axis_observation': ('axis', 'angle_centideg', 'speed_centideg_s', 'sampled_us', 'received_us',
        'stationary', 'motion_generation', 'result'),
    'can_terminal_fault': ('terminal',),
    'lighting_observation': ('observed_us', 'verified', 'applied', 'result'),
    'wire_set_buffer_size': ('bytes', 'returned'), 'wire_set_timeout': ('milliseconds',),
    'wire_begin': ('sda', 'scl', 'hz', 'returned'), 'wire_set_clock': ('hz', 'returned'),
    'wire_begin_transmission': ('address',), 'wire_write_byte': ('data_hex', 'returned'),
    'wire_write_bytes': ('data_hex', 'returned'),
    'wire_end_transmission': ('address', 'stop', 'data_hex', 'returned'),
    'wire_request_from': ('address', 'register', 'count', 'stop', 'data_hex', 'returned'),
    'wire_read_byte': ('returned',), 'ledc_attach': ('pin', 'hz', 'bits', 'channel', 'returned'),
    'ledc_write': ('pin', 'duty', 'returned'),
    'ledc_set': ('mode', 'channel', 'duty', 'returned'),
    'ledc_update': ('mode', 'channel', 'returned'), 'ledc_get': ('mode', 'channel', 'returned'),
    'delay_us': ('requested_us', 'clock_advanced'), 'enable_request': ('enabled', 'returned'),
    'guard_trip': ('enabled',)}


def _json(raw):
    need(type(raw) is bytes and 0 < len(raw) < RESPONSE_BYTES and b'\r' not in raw,
         'canonical_json_byte_bound')
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def invalid(_):
        raise ArchiveInvalid('integer_only_json_required')
    def tree(value, depth=0):
        need(depth <= 32, 'canonical_json_depth')
        if value is None or type(value) is bool:
            return
        if type(value) is int:
            integer(value, -(1 << 63), (1 << 64) - 1)
        elif type(value) is str:
            need(value.isascii() and len(value) <= RESPONSE_BYTES, 'canonical_json_string')
        elif type(value) is list:
            need(len(value) <= 2048, 'canonical_json_array')
            for item in value: tree(item, depth + 1)
        elif type(value) is dict:
            need(len(value) <= 128 and all(type(k) is str and k.isascii() and len(k) <= 128 for k in value),
                 'canonical_json_object')
            for item in value.values(): tree(item, depth + 1)
        else:
            raise ArchiveInvalid('integer_only_json_required')
    try:
        value = json.loads(raw.decode('ascii'), object_pairs_hook=pairs, parse_float=invalid,
                           parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ArchiveInvalid('malformed_ascii_json') from exc
    tree(value)
    return value


def _can_frame(frame):
    keys(frame, CAN_FIELDS, 'canonical_can_frame_fields')
    integer(frame['can_id'], 0, 2047); need(type(frame['dlc']) is int and frame['dlc'] == 8, 'canonical_can_dlc')
    need(frame['extended'] is False and frame['remote'] is False, 'canonical_can_flags')
    legacy._hex(frame['data_hex'], 16)


def _request(raw, index, header):
    need(type(raw) is bytes and len(raw) <= 16384 and raw.endswith(b'\n') and raw.count(b'\n') == 1,
         'request_framing')
    request = _json(raw[:-1]); op = request.get('op') if type(request) is dict else None
    need(op in REQUEST_EXTRAS, 'request_operation')
    keys(request, ('schema', 'version', 'op', 'request_id') + REQUEST_EXTRAS[op], 'request_fields')
    need(request['schema'] == ABI and type(request['version']) is int and request['version'] == 1, 'request_abi')
    legacy._ascii(request['request_id'], 64)
    need(canonical(request) + b'\n' == raw, 'noncanonical_original_request')
    if 'at_device_us' in request: u64(request['at_device_us'])
    if op == 'initialize':
        need(type(request['fixture_index']) is int and request['fixture_index'] == index
             and request['expected_config_sha256'] == header['producer']['config_sha256'], 'initialize_binding')
    elif op == 'exchange':
        need(u64(request['ingress_device_us']) <= u64(request['at_device_us']), 'ingress_after_processing')
        legacy._packet(request['packet_hex'])
    elif op == 'observe':
        need(type(request['observation']) is dict and request['observation'].get('kind') == 'temperature',
             'canonical_temperature_only')
        legacy._observation(request['observation'])
    elif op == 'set_effect':
        need(request['driver'] in ('can', 'lighting') and request['effect'] in legacy.EFFECTS, 'explicit_effect_required')
    elif op == 'can_port':
        for key in ('completed', 'failed', 'running'): legacy._bool(request[key])
        integer(request['tx_count'], 0, 16)
        need(type(request['frames']) is list and len(request['frames']) <= 16, 'canonical_can_input_bound')
        for frame in request['frames']: _can_frame(frame)
    elif op == 'hal_fault':
        need(request['mode'] == 'ledc_same_cycle_stale', 'canonical_hal_fault')
    return request


def _report(report, fixture):
    keys(report, REPORT_FIELDS, 'canonical_report_fields')
    need(report['phase'] in legacy.PHASES and report['result'] in legacy.RESULTS, 'canonical_report_enum')
    legacy._hex(report['context_sha256'])
    for key in REPORT_FIELDS:
        if key not in ('phase', 'result', 'context_sha256', 'original_plan'): u64(report[key])
    plan = report['original_plan']
    if plan is not None:
        keys(plan, PLAN_FIELDS, 'canonical_original_plan_fields')
        legacy._hex(plan['plan_sha256'])
        for key in PLAN_FIELDS:
            if key != 'plan_sha256': u64(plan[key])
        need(plan['revision_id'] == report['revision_id'] and plan['motion_id'] == report['motion_id']
             and report['boot_generation'] == fixture['boot'], 'canonical_plan_lineage')


def _operations(response, state, fixture):
    operations = response['canonical_ops']
    need(type(operations) is list and len(operations) <= 2048, 'canonical_operation_bound')
    for operation in operations:
        keys(operation, ('operation_id', 'step_id', 'processed_device_us', 'at_device_us', 'generation',
            'kind', 'lineage', 'fields'), 'canonical_operation_fields')
        need(u64(operation['operation_id'], True) == state['operations'] + 1
             and operation['step_id'] == response['step_id']
             and operation['processed_device_us'] == operation['at_device_us'] == response['processed_device_us'],
             'canonical_operation_order_or_time')
        generation = u64(operation['generation'])
        kind, fields = operation['kind'], operation['fields']
        need(type(kind) is str and kind in OP_FIELDS, 'canonical_operation_kind')
        keys(fields, OP_FIELDS[kind], 'canonical_primitive_fields')
        if operation['lineage'] is not None: _report(operation['lineage'], fixture)
        returned_bool = kind in ('can_port_transmit_once', 'wire_begin', 'wire_set_clock',
            'ledc_attach', 'ledc_write', 'ledc_set', 'ledc_update', 'enable_request')
        if 'returned' in fields:
            need(type(fields['returned']) is (bool if returned_bool else int), 'canonical_primitive_return_type')
        for name, value in fields.items():
            if name in ('motion_generation', 'admitted_us', 'attempted_us', 'observed_us', 'sampled_us', 'received_us'):
                u64(value)
            elif name in ('extended', 'remote', 'protective', 'feedback', 'speed_stage', 'stationary',
                          'verified', 'stop', 'clock_advanced', 'enabled', 'terminal'):
                legacy._bool(value)
            elif name == 'data_hex':
                need(type(value) is str and len(value) <= 58 and len(value) % 2 == 0
                     and re.fullmatch('[0-9a-f]*', value) is not None, 'canonical_primitive_bytes')
            elif name == 'applied': legacy._frame(value)
            elif name == 'result': need(value in legacy.RESULTS, 'canonical_primitive_result')
            elif name == 'mode': need(value == 'low_speed', 'canonical_ledc_mode')
            elif name == 'coordinate_space': need(value == 'raw_motor_post_arbiter', 'canonical_coordinate_space')
            elif name == 'returned' and type(value) is bool: pass
            else: integer(value, -(1 << 31), (1 << 32) - 1)
        if kind == 'can_port_transmit_once':
            _can_frame({key: fields[key] for key in CAN_FIELDS})
            integer(fields['axis'], 0, 1)
            need(fields['can_id'] == 0x141 + fields['axis'] and fields['logical_opcode'] in (0xa4, 0x92, 0x80, 0x81)
                 and type(fields['returned']) is bool and generation > 0
                 and operation['lineage'] is not None
                 and u64(fields['admitted_us']) <= u64(fields['attempted_us']) <= u64(operation['at_device_us']),
                 'canonical_can_transaction')
        if kind in ('can_observation', 'axis_observation'):
            integer(fields['axis'], 0, 1)
        if kind == 'axis_observation':
            need(u64(fields['sampled_us']) <= u64(fields['received_us']) <= u64(operation['at_device_us']),
                 'canonical_axis_clock')
        if kind in ('can_observation', 'lighting_observation'):
            need(u64(fields['observed_us']) <= u64(operation['at_device_us']), 'canonical_observation_clock')
        if kind in ('ledc_set', 'ledc_update', 'ledc_get', 'ledc_attach'):
            integer(fields['channel'], 0, 2)
        if kind == 'delay_us': need(fields['clock_advanced'] is False, 'canonical_fake_delay')
        if kind == 'guard_trip': need(fields['enabled'] is False, 'canonical_guard_trip')
        state['operations'] += 1
    need(response['counters'].get('canonical_ops') == str(state['operations']), 'canonical_cumulative_count')


def _describe(response, producer):
    expected = {'build_id': producer['build_id'], 'base_revision': BASE,
        'source_sha256': producer['source_sha256'], 'config_sha256': producer['config_sha256'],
        'operations': list(OPS), 'topology': {'fixture_count': 4, 'axis_count': 2, 'lighting_channels': 24,
            'ambient_zones': 7, 'domain_count': 8, 'total_domains': 32, 'total_axes': 8,
            'total_lighting_channels': 96, 'spotlight_channels': [21, 22, 23],
            'spotlight_names': ['TEST_WARM_WHITE', 'TEST_NEUTRAL_WHITE', 'TEST_PC_AMBER']},
        'limits': {'request_bytes': 16384, 'response_bytes': RESPONSE_BYTES, 'steps': 100000,
            'wire_packet_bytes': 64, 'effects_per_step': 64, 'events_per_step': 64, 'admitted_lineages': 100000},
        'explicit_ticks_only': True, 'initial_device_us': '0', 'default_effect': 'rejected',
        'logical_can_only': False, 'supports_axis_acquisition': True, 'spotlight_CCT_calibrated': False,
        'capture_layer': 'canonical_ports_and_shared_production_lighting_driver',
        'coordinate_contract': 'joint_input_once_to_raw_motor_output'}
    need({key: response[key] for key in expected} == expected, 'canonical_describe_pins')


class CanonicalStreamValidator:
    """Called only after the raw callback fsync; rejects before client decode."""
    def __init__(self, header, index):
        validate_header(header)
        self.header, self.index = copy.deepcopy(header), integer(index, 0, 3)
        self.lock = threading.RLock(); self.buffer = bytearray(); self.pending = []
        self.ids = set(); self.step = 0; self.current = None; self.closed = False; self.disconnected = False
        self.last_response = None
        self.counts = {key: 0 for key in ('events', 'effects', 'accepted_observations', 'can_attempts', 'lighting_attempts')}
        self.state = {'operations': 0}

    def request(self, raw):
        with self.lock:
            need(not self.closed and not self.pending and len(self.ids) < 100000, 'canonical_request_sequence')
            request = _request(raw, self.index, self.header)
            need(request['request_id'] not in self.ids, 'canonical_request_identity')
            if self.step < 2:
                need(request['op'] == ('describe', 'initialize')[self.step], 'canonical_start_order')
            else: need(request['op'] != 'initialize', 'canonical_reinitialize')
            self.ids.add(request['request_id']); self.pending.append(request)

    def stdout(self, raw):
        with self.lock:
            need(type(raw) is bytes and 0 < len(raw) <= 4096, 'canonical_stdout_chunk')
            self.buffer.extend(raw)
            results = []
            while b'\n' in self.buffer:
                end = self.buffer.index(10) + 1
                need(end <= RESPONSE_BYTES and self.pending, 'canonical_response_bound_or_unsolicited')
                line = bytes(self.buffer[:end]); del self.buffer[:end]
                response = _json(line[:-1]); request = self.pending.pop(0)
                self._response(response, request)
                results.append(response)
            need(len(self.buffer) < RESPONSE_BYTES, 'canonical_response_byte_bound')
            return results

    def finish(self):
        need(not self.buffer and not self.pending and self.closed and self.step >= 3, 'canonical_stream_incomplete')

    def _response(self, response, request):
        op = request['op']; self.step += 1
        extra = (legacy.DESCRIBE_FIELDS + ('capture_layer', 'coordinate_contract') if op == 'describe'
                 else ('can_port_input',) if op == 'can_port' else ('mode',) if op == 'hal_fault'
                 else legacy.RESPONSE_EXTRAS[op])
        keys(response, legacy.COMMON_RESPONSE + ('physical_truth', 'canonical_ops', 'report') + extra,
             'canonical_response_fields')
        need(response['schema'] == ABI and type(response['version']) is int and response['version'] == 1
             and response['request_id'] == request['request_id'] and response['op'] == op
             and response['ok'] is True and response['physical_truth'] is False
             and response['physical_permission'] is False and response['evidence_source'] == 'simulated'
             and response['step_id'] == str(self.step), 'canonical_response_correlation_or_provenance')
        fixture, producer = self.header['fixtures'][self.index], self.header['producer']
        if self.step == 1:
            need(response['binding'] is None and response['processed_device_us'] is None and response['report'] is None,
                 'canonical_preinitialize_binding')
        else:
            expected = {'fixture_index': self.index, 'device_id': fixture['device_id'], 'boot': fixture['boot'],
                'config_sha256': producer['config_sha256'], 'source_sha256': producer['source_sha256'], 'build_id': producer['build_id']}
            need(response['binding'] == expected, 'canonical_response_binding')
            at = request.get('at_device_us', '0' if self.current is None else str(self.current))
            need(response['processed_device_us'] == at and (self.current is None or u64(at) >= self.current),
                 'canonical_device_clock')
            self.current = u64(at); _report(response['report'], fixture)
        # Validate the complete successor before considering a logical view.
        _operations(response, self.state, fixture)
        if op == 'describe': _describe(response, producer)
        elif op == 'initialize':
            need(type(response['fixture_index']) is int and response['fixture_index'] == self.index
                 and response['device_id_hex'] == fixture['device_id'] and response['boot_generation'] == fixture['boot']
                 and response['at_device_us'] == '0' and response['profile'] == 'fictional-c12-v1', 'canonical_initialize')
        elif op == 'exchange':
            need(not self.disconnected and response['packet_hex'] == request['packet_hex'], 'canonical_exchange_echo')
            legacy._packet(response['reply_hex']); wire = response['wire']
            keys(wire, ('code', 'detail', 'authenticated', 'ingress_device_us', 'processed_device_us'), 'wire_fields')
            integer(wire['code'], 0, 15); integer(wire['detail'], 0, 65535); legacy._bool(wire['authenticated'])
            need(wire['ingress_device_us'] == request['ingress_device_us']
                 and wire['processed_device_us'] == request['at_device_us'], 'canonical_wire_clocks')
        elif op == 'can_port': need(response['can_port_input'] == request['frames'], 'canonical_can_input_echo')
        elif op == 'hal_fault': need(response['mode'] == request['mode'], 'canonical_hal_fault_echo')
        elif op == 'observe': need(response['observation_result'] in legacy.RESULTS, 'observation_result_enum')
        elif op == 'set_effect': need(response['driver'] == request['driver'] and response['effect'] == request['effect'], 'effect_setting_echo')
        elif op == 'disconnect':
            need(not self.disconnected and response['disconnected'] is True, 'canonical_disconnect')
            self.disconnected = True
        elif op == 'close':
            need(response['closed'] is True and response['terminal'] is True and type(response['exit_code']) is int
                 and response['exit_code'] == 0 and response['exit_reason'] == 'owner_close', 'canonical_close')
            self.closed = True
        view = copy.deepcopy(response)
        for effect in view['effects']:
            if effect.get('kind') == 'can_send':
                need(set(effect) == {'effect_id', 'step_id', 'processed_device_us', 'kind', 'at_device_us',
                    'generation', 'returned_effect', 'can_id', 'data_hex', 'coordinate_space'}
                    and effect['coordinate_space'] == 'joint_input_pre_arbiter', 'canonical_logical_coordinate')
                effect.pop('coordinate_space')
        view['counters'].pop('canonical_ops')
        # This detached validation view is never retained, published or returned.
        legacy._traces(view, self.counts, request)
        self.last_response = copy.deepcopy(response)


def _receipt(receipt, path, final, terminal_sha, record_count):
    need(type(receipt) is dict and receipt.get('schema') == RECEIPT_SCHEMA
         and receipt.get('physical_truth') is False, 'canonical_recording_receipt')
    view = copy.deepcopy(receipt)
    view['schema'] = 'home-room-fake-plant-recording-receipt-1'; view.pop('physical_truth')
    legacy._receipt(view, path, final, terminal_sha, record_count)


def _transcript(index, member, header):
    need(member['attempts'] and not member['stderr'] and len(member['attempts']) <= 100000, 'canonical_missing_input_or_stderr')
    need(len(member['results']) == len(member['attempts']), 'missing_write_result')
    validator = CanonicalStreamValidator(header, index)
    raw_output = bytes(member['stdout']); lines = raw_output.splitlines(keepends=True)
    need(raw_output.endswith(b'\n') and len(lines) == len(member['attempts']), 'canonical_response_count_or_framing')
    requests, responses, offset = [], [], 0
    for attempt, line in zip(member['attempts'], lines):
        result = member['results'].get(attempt['request_id'])
        need(result is not None and type(result['reported_bytes']) is int and result['reported_bytes'] == len(attempt['raw'])
             and result['flush_completed'] is True and result['error'] is None, 'unconfirmed_pipe_write')
        offset += len(line)
        completed = next(ordinal for end, ordinal in member['stdout_segments'] if end >= offset)
        need(completed > attempt['ordinal'], 'response_precedes_attempt')
        validator.request(attempt['raw']); requests.append(_request(attempt['raw'], index, header))
        for start in range(0, len(line), 4096): responses.extend(validator.stdout(line[start:start + 4096]))
    validator.finish()
    return {'fixture_index': index, 'fixture': copy.deepcopy(header['fixtures'][index]),
        'stdin_bytes': b''.join(a['raw'] for a in member['attempts']), 'stdout_bytes': raw_output,
        'stderr_bytes': bytes(member['stderr']), 'requests': requests, 'responses': responses}


def verify_room_canonical_archive(path, *, expected_header, recording_receipt=None,
                                  max_records=8192, max_bytes=33554432):
    result = dict(status='incomplete', reason='archive_unavailable', native_recomputed=False,
        home_room_replayed=False, evidence_source='simulated', physical_permission=False, physical_truth=False,
        archive_sha256=None, archive_bytes=0, record_count=0)
    try:
        integer(max_records, 1, 8192); integer(max_bytes, 1, 33554432); validate_header(expected_header)
        raw = legacy._read_archive(path, max_bytes)
        result.update(archive_sha256=hashlib.sha256(raw).hexdigest(), archive_bytes=len(raw))
        need(raw and raw.endswith(b'\n'), 'truncated_archive')
        lines = raw.splitlines(keepends=True); need(len(lines) <= max_records, 'archive_record_budget')
        result['record_count'] = len(lines)
        previous, header, final = '0' * 64, None, None
        members = [legacy._member() for _ in range(4)]
        for sequence, line in enumerate(lines, 1):
            need(len(line) <= 49152, 'archive_row_budget')
            row = legacy._json(line[:-1])
            keys(row, ('schema', 'version', 'sequence', 'kind', 'previous_sha256', 'payload', 'record_sha256'), 'archive_row_fields')
            need(row['schema'] == RECORD_SCHEMA and type(row['version']) is int and row['version'] == 1
                 and row['sequence'] == str(sequence) and canonical(row) + b'\n' == line, 'canonical_archive_abi_or_sequence')
            need(row['previous_sha256'] == previous and row['record_sha256'] == hashlib.sha256(canonical(
                {key: value for key, value in row.items() if key != 'record_sha256'})).hexdigest(), 'archive_chain_or_digest')
            previous = row['record_sha256']; need(final is None, 'record_after_final')
            if sequence == 1:
                need(row['kind'] == 'header', 'header_first_required'); validate_header(row['payload'])
                need(row['payload'] == expected_header, 'expected_header_mismatch'); header = row['payload']
            elif row['kind'] == 'io': legacy._io(row['payload'], header, members)
            elif row['kind'] == 'final': final = row['payload']
            else: raise ArchiveInvalid('archive_kind_or_duplicate_header')
        need(final is not None, 'missing_final_footer')
        legacy._footer(final, header, members); _receipt(recording_receipt, path, final, previous, len(lines))
        transcripts = [_transcript(index, member, header) for index, member in enumerate(members)]
        result.update(status='native_archive_verified', reason='complete_original_canonical_transcripts',
                      header=header, transcripts=transcripts)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, StopIteration) as exc:
        result['reason'] = str(exc) if isinstance(exc, ArchiveInvalid) else 'canonical_archive_unavailable_or_malformed'
    return result


def replay_room_canonical_archive(path, *, expected_header, consumer, recording_receipt=None,
                                  max_records=8192, max_bytes=33554432):
    result = verify_room_canonical_archive(path, expected_header=expected_header,
        recording_receipt=recording_receipt, max_records=max_records, max_bytes=max_bytes)
    if result['status'] != 'native_archive_verified': return result
    try:
        need(callable(consumer), 'explicit_native_consumer_required')
        expected = [{key: transcript[key] for key in ('fixture_index', 'stdin_bytes', 'stdout_bytes', 'stderr_bytes')}
                    for transcript in result['transcripts']]
        actual = consumer(header=copy.deepcopy(result['header']), transcripts=copy.deepcopy(result['transcripts']))
        need(type(actual) is list and len(actual) == 4, 'four_native_replays_required')
        for index, (received, original) in enumerate(zip(actual, expected)):
            keys(received, ('fixture_index', 'stdin_bytes', 'stdout_bytes', 'stderr_bytes'), 'native_replay_fields')
            need(type(received['fixture_index']) is int and received['fixture_index'] == index
                 and all(type(received[key]) is bytes and received[key] == original[key]
                     for key in ('stdin_bytes', 'stdout_bytes', 'stderr_bytes')), 'native_replay_original_bytes_differ')
        result.update(status='native_archive_recomputed', reason='exact_original_native_streams', native_recomputed=True)
    except Exception as exc:
        result.update(status='native_recompute_failed', reason=str(exc) if isinstance(exc, ArchiveInvalid)
                      else 'native_consumer_failed', native_recomputed=False)
    return result
