"""Independent, bounded verification of original four-process fake-plant archives.

This module deliberately imports neither the recorder nor the producer client.
Archive verification is a structural/original-byte check, not native execution,
Home authority admission, a clock qualification, or physical acceptance.
"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat


ABI = 'c12-fake-plant-bridge-1'
BASE_REVISION = 'd97c6bf58353d5f808774893c7559e799008f791'
MAX_RECORDS = 8192
MAX_BYTES = 33554432
MAX_ROW_BYTES = 49152
U64_MAX = (1 << 64) - 1
OPERATIONS = ('describe', 'initialize', 'exchange', 'observe', 'tick', 'set_effect', 'disconnect', 'close')
RESULTS = frozenset(('ok duplicate changed_duplicate capability_denied invalid_profile context_mismatch '
                     'no_session invalid_clock clock_rollback invalid_plan stale_revision predecessor_mismatch '
                     'cas_mismatch ownership invalid_frame mixer_mismatch power_limit unsupported expired '
                     'reserve_exceeded busy stale_angles not_stationary thermal_missing thermal_trip thermal_stale '
                     'thermal_latched driver_rejected driver_uncertain can_rejected can_uncertain motion_timeout '
                     'observation_rejected stop_unconfirmed event_overflow travel revoked manual_lock '
                     'invalid_manual_duration').split())
EVENTS = frozenset(('session_opened admitted duplicate refused lighting_requested lighting_verified can_submitted '
                    'controller_completed motor_reply axis_observed settling settled phase_changed withdrawn '
                    'ambient_retained manual_expired thermal_derated thermal_fault protective_submitted '
                    'unconfirmed fault clock_mapped').split())
PHASES = frozenset(('inhibited idle darkening decaying launch_pan launch_tilt moving settling illuminating lit '
                    'protecting unconfirmed fault travel').split())
EFFECTS = ('submitted', 'rejected', 'uncertain')
REQUEST_EXTRAS = {
    'describe': (), 'initialize': ('fixture_index', 'expected_config_sha256'),
    'exchange': ('at_device_us', 'ingress_device_us', 'packet_hex'),
    'observe': ('at_device_us', 'observation'), 'tick': ('at_device_us',),
    'set_effect': ('at_device_us', 'driver', 'effect'), 'disconnect': ('at_device_us',), 'close': ()}
DESCRIBE_FIELDS = ('build_id', 'base_revision', 'source_sha256', 'config_sha256', 'operations', 'topology', 'limits',
                   'explicit_ticks_only', 'initial_device_us', 'default_effect', 'logical_can_only',
                   'supports_axis_acquisition', 'spotlight_CCT_calibrated')
RESPONSE_EXTRAS = {'describe': DESCRIBE_FIELDS,
                   'initialize': ('fixture_index', 'device_id_hex', 'boot_generation', 'at_device_us', 'profile'),
                   'exchange': ('packet_hex', 'reply_hex', 'wire'), 'observe': ('observation_result',),
                   'tick': (), 'set_effect': ('driver', 'effect'), 'disconnect': ('disconnected',),
                   'close': ('closed', 'terminal', 'exit_code', 'exit_reason')}
COMMON_RESPONSE = ('schema', 'version', 'request_id', 'op', 'ok', 'step_id', 'evidence_source', 'physical_permission',
                   'binding', 'processed_device_us', 'counters', 'events', 'effects', 'accepted_observations')
IO_EXTRAS = {'stdin_attempt': ('request_id',),
             'stdin_write_result': ('request_id', 'reported_bytes', 'flush_completed', 'error', 'effect',
                                    'flush_captured_monotonic_ns', 'flush_captured_utc_ns'),
             'stdout_bytes': (), 'stderr_bytes': (), 'stdout_eof': (), 'stderr_eof': (),
             'process_exit': ('exit_code',)}


class ArchiveInvalid(ValueError):
    """A bounded archive cannot establish a complete original transcript."""


def _require(condition, reason):
    if not condition:
        raise ArchiveInvalid(reason)


def _keys(value, keys, reason):
    _require(type(value) is dict and set(value) == set(keys), reason)


def _int(value, low=0, high=(1 << 32) - 1):
    _require(type(value) is int and low <= value <= high, 'integer_out_of_range')
    return value


def _u64(value, nonzero=False):
    _require(type(value) is str and re.fullmatch(r'0|[1-9][0-9]{0,19}', value) is not None,
             'noncanonical_uint64')
    number = int(value)
    _require(number <= U64_MAX and (not nonzero or number != 0), 'uint64_out_of_range')
    return number


def _ascii(value, maximum=128):
    _require(type(value) is str and 0 < len(value) <= maximum and
             all(32 <= ord(c) <= 126 for c in value), 'bounded_ascii_required')
    return value


def _hex(value, digits=64):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{' + str(digits) + '}', value) is not None,
             'lowercase_digest_required')


def _packet(value, maximum=64):
    _require(type(value) is str and len(value) <= maximum * 2 and len(value) % 2 == 0 and
             re.fullmatch('[0-9a-f]*', value) is not None, 'bounded_packet_required')


def _bool(value):
    _require(type(value) is bool, 'literal_boolean_required')


def _frame(value):
    _require(type(value) is list and len(value) == 24, 'frame24_required')
    for duty in value:
        _int(duty, 0, 65535)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _tree(value, depth=0):
    _require(depth <= 32, 'json_depth_exceeded')
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        _int(value, -(1 << 31), (1 << 32) - 1)
    elif type(value) is str:
        _require(len(value) <= 65536 and value.isascii(), 'bounded_ascii_json_required')
    elif type(value) is list:
        _require(len(value) <= 1024, 'json_array_exceeded')
        for item in value:
            _tree(item, depth + 1)
    elif type(value) is dict:
        _require(len(value) <= 128, 'json_object_exceeded')
        for key, item in value.items():
            _require(type(key) is str and len(key) <= 128 and key.isascii(), 'json_key_invalid')
            _tree(item, depth + 1)
    else:
        raise ArchiveInvalid('integer_only_json_required')


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result

    def invalid_number(_):
        raise ArchiveInvalid('integer_only_json_required')

    _require(type(raw) is bytes and b'\r' not in raw, 'ascii_json_required')
    try:
        value = json.loads(raw.decode('ascii'), object_pairs_hook=pairs,
                           parse_float=invalid_number, parse_constant=invalid_number)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ArchiveInvalid('malformed_ascii_json') from exc
    _tree(value)
    return value


def _header(h):
    _keys(h, ('session_id', 'producer', 'fixtures', 'evidence_source', 'physical_permission'), 'header_fields')
    _ascii(h['session_id'])
    p = h['producer']
    _keys(p, ('abi', 'build_id', 'manifest_sha256', 'source_sha256', 'config_sha256', 'executable_sha256'),
          'producer_fields')
    _require(p['abi'] == ABI, 'producer_abi')
    _ascii(p['build_id'])
    for key in ('manifest_sha256', 'source_sha256', 'config_sha256', 'executable_sha256'):
        _hex(p[key])
    _require(type(h['fixtures']) is list and len(h['fixtures']) == 4, 'four_fixture_header_required')
    for index, fixture in enumerate(h['fixtures']):
        _keys(fixture, ('fixture_index', 'fixture_id', 'device_id', 'boot', 'launch_id'), 'fixture_fields')
        _require(_int(fixture['fixture_index'], 0, 3) == index, 'fixture_index_order')
        _ascii(fixture['fixture_id'])
        _ascii(fixture['launch_id'])
        _hex(fixture['device_id'], 32)
        _u64(fixture['boot'], True)
    for key in ('fixture_id', 'device_id', 'launch_id'):
        _require(len({f[key] for f in h['fixtures']}) == 4, 'duplicate_fixture_identity')
    _require(h['evidence_source'] == 'simulated' and h['physical_permission'] is False, 'header_provenance')


def _read_archive(path, maximum):
    """Refuse aliases before reads; bound and verify the same regular-file handle."""
    target = Path(os.path.abspath(os.fspath(path)))
    _require(not str(target).startswith(('\\\\', '//')) and
             all(':' not in part for part in target.parts[1:]), 'archive_network_or_stream_path_refused')
    for entry in reversed((target, *target.parents)):
        info = entry.lstat()
        _require(not stat.S_ISLNK(info.st_mode) and not (getattr(info, 'st_file_attributes', 0) & 0x400),
                 'archive_alias_refused')
    before = target.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'archive_regular_single_link_required')
    _require(before.st_size <= maximum, 'archive_byte_budget')
    fd = os.open(target, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        _require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino) and
                 stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1, 'archive_changed_before_read')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    final = target.lstat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
    _require(signature(before) == signature(after) == signature(final) and len(raw) <= maximum,
             'archive_changed_during_read')
    return raw


def _original(original, maximum):
    _keys(original, ('base64', 'bytes', 'sha256'), 'original_fields')
    _int(original['bytes'], 1, maximum)
    _hex(original['sha256'])
    _require(type(original['base64']) is str, 'original_base64_required')
    try:
        raw = base64.b64decode(original['base64'], validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ArchiveInvalid('original_base64_invalid') from exc
    _require(base64.b64encode(raw).decode('ascii') == original['base64'] and len(raw) == original['bytes'] and
             hashlib.sha256(raw).hexdigest() == original['sha256'], 'original_bytes_differ')
    return raw


def _member():
    return {'ordinal': 0, 'attempts': [], 'results': {}, 'stdout': bytearray(), 'stderr': bytearray(),
            'stdout_segments': [], 'stdout_eof': False, 'stderr_eof': False, 'exit': None}


def _io(payload, h, members):
    _keys(payload, ('fixture_index', 'launch_id', 'event', 'original', 'callback_monotonic_ns', 'callback_utc_ns'),
          'io_fields')
    index = _int(payload['fixture_index'], 0, 3)
    _require(payload['launch_id'] == h['fixtures'][index]['launch_id'], 'launch_binding')
    callback = _u64(payload['callback_monotonic_ns'])
    _u64(payload['callback_utc_ns'])
    e, m = payload['event'], members[index]
    _require(type(e) is dict and e.get('kind') in IO_EXTRAS, 'io_kind')
    kind = e['kind']
    _keys(e, ('schema', 'version', 'ordinal', 'kind', 'captured_monotonic_ns', 'captured_utc_ns') + IO_EXTRAS[kind],
          'producer_event_fields')
    _require(e['schema'] == 'c12-fake-plant-io-1' and type(e['version']) is int and e['version'] == 1,
             'producer_event_abi')
    _require(_u64(e['ordinal'], True) == m['ordinal'] + 1, 'producer_ordinal')
    m['ordinal'] += 1
    _require(m['exit'] is None, 'event_after_exit')
    _require(_u64(e['captured_monotonic_ns']) <= callback, 'capture_after_callback')
    _u64(e['captured_utc_ns'])
    raw_kind = kind in ('stdin_attempt', 'stdout_bytes', 'stderr_bytes')
    if raw_kind:
        maximum = {'stdin_attempt': 16384, 'stdout_bytes': 4096, 'stderr_bytes': 4097}[kind]
        raw = _original(payload['original'], maximum)
    else:
        _require(payload['original'] is None, 'unexpected_original_bytes')
        raw = None
    if kind == 'stdin_attempt':
        rid = _ascii(e['request_id'], 64)
        _require(rid not in {a['request_id'] for a in m['attempts']}, 'duplicate_stdin_attempt')
        m['attempts'].append({'request_id': rid, 'raw': raw, 'ordinal': m['ordinal']})
    elif kind == 'stdin_write_result':
        rid = _ascii(e['request_id'], 64)
        _require(rid in {a['request_id'] for a in m['attempts']} and rid not in m['results'], 'write_result_order')
        if e['reported_bytes'] is not None:
            _int(e['reported_bytes'], -(1 << 31), (1 << 32) - 1)
        _bool(e['flush_completed'])
        if e['error'] is not None:
            _ascii(e['error'])
        _require(e['effect'] == 'unknown', 'write_effect_must_remain_unknown')
        flush_mono, flush_utc = e['flush_captured_monotonic_ns'], e['flush_captured_utc_ns']
        _require((flush_mono is None) == (flush_utc is None), 'flush_clock_pair')
        if flush_mono is not None:
            _require(_u64(e['captured_monotonic_ns']) <= _u64(flush_mono) <= callback,
                     'flush_clock_order')
            _u64(flush_utc)
        _require(not e['flush_completed'] or flush_mono is not None, 'flush_clock_missing')
        m['results'][rid] = e
    elif kind in ('stdout_bytes', 'stderr_bytes'):
        pipe = 'stdout' if kind == 'stdout_bytes' else 'stderr'
        _require(not m[pipe + '_eof'], 'bytes_after_eof')
        m[pipe].extend(raw)
        if pipe == 'stdout':
            m['stdout_segments'].append((len(m[pipe]), m['ordinal']))
    elif kind in ('stdout_eof', 'stderr_eof'):
        _require(not m[kind], 'duplicate_eof')
        m[kind] = True
    else:
        _int(e['exit_code'], -(1 << 31), (1 << 32) - 1)
        m['exit'] = e['exit_code']


def _footer(f, h, members):
    _keys(f, ('status', 'failure_reason', 'members'), 'footer_fields')
    _require(f['status'] in ('complete', 'incomplete'), 'footer_status')
    if f['failure_reason'] is not None:
        _ascii(f['failure_reason'])
    _require(type(f['members']) is list and len(f['members']) == 4, 'four_member_footer_required')
    clean = f['status'] == 'complete' and f['failure_reason'] is None
    for index, member in enumerate(f['members']):
        _keys(member, ('fixture_index', 'launch_id', 'close_acknowledged', 'recording_status'), 'member_fields')
        _require(_int(member['fixture_index'], 0, 3) == index and
                 member['launch_id'] == h['fixtures'][index]['launch_id'], 'footer_member_binding')
        _bool(member['close_acknowledged'])
        s = member['recording_status']
        if s is None:
            clean = False
        else:
            _keys(s, ('complete', 'cleanup_unconfirmed', 'observer_failed', 'terminal', 'active_callbacks'),
                  'recording_status_fields')
            for key in ('complete', 'cleanup_unconfirmed', 'observer_failed', 'terminal'):
                _bool(s[key])
            _int(s['active_callbacks'])
            clean = clean and s['complete'] and not any(s[k] for k in
                        ('cleanup_unconfirmed', 'observer_failed', 'terminal', 'active_callbacks'))
        m = members[index]
        clean = clean and member['close_acknowledged'] and m['stdout_eof'] and m['stderr_eof'] and m['exit'] == 0
    _require(clean, 'recording_incomplete')


def _receipt(receipt, path, final, terminal_sha, record_count):
    # Bytes readable now do not establish the historical final fsync ACK. This
    # original caller-supplied causal receipt is a separate required input.
    _require(receipt is not None, 'recording_receipt_required')
    _keys(receipt, ('schema', 'version', 'status', 'failure_reason', 'path', 'records', 'terminal_record_sha256',
                    'members', 'footer_durable', 'cleanup_unconfirmed', 'native_recomputed', 'home_room_replayed',
                    'evidence_source', 'physical_permission'), 'recording_receipt_fields')
    _require(receipt['schema'] == 'home-room-fake-plant-recording-receipt-1' and
             type(receipt['version']) is int and receipt['version'] == 1, 'recording_receipt_abi')
    _require(receipt['status'] == 'complete' and receipt['failure_reason'] is None and
             receipt['footer_durable'] is True and receipt['cleanup_unconfirmed'] is False,
             'recording_receipt_not_durable')
    _require(receipt['native_recomputed'] is False and receipt['home_room_replayed'] is False and
             receipt['evidence_source'] == 'simulated' and receipt['physical_permission'] is False,
             'recording_receipt_provenance')
    _require(type(receipt['path']) is str and bool(receipt['path']) and '\x00' not in receipt['path'] and
             os.path.normcase(os.path.abspath(receipt['path'])) == os.path.normcase(os.path.abspath(os.fspath(path))),
             'recording_receipt_path')
    _require(type(receipt['records']) is int and receipt['records'] == record_count and
             receipt['terminal_record_sha256'] == terminal_sha and
             _canonical(receipt['members']) == _canonical(final['members']), 'recording_receipt_archive_binding')


def _observation(o):
    _require(type(o) is dict, 'typed_observation_required')
    kind = o.get('kind')
    fields = {'temperature': ('kind', 'millic', 'sampled_us', 'valid', 'config_valid', 'conversion_ready'),
              'axis': ('kind', 'axis', 'opcode', 'angle_centideg', 'speed_centideg_s', 'sampled_us', 'received_us',
                       'motion_generation', 'matched', 'stationary'),
              'lighting_observation': ('kind', 'generation', 'observed_us', 'applied', 'verified'),
              'can_observation': ('kind', 'axis', 'opcode', 'command_generation', 'observed_us', 'observation')}
    _require(kind in fields, 'unknown_observation')
    _keys(o, fields[kind], 'observation_fields')
    for key in ('axis', 'opcode'):
        if key in o:
            _int(o[key], 0, 255)
    for key in ('millic', 'angle_centideg', 'speed_centideg_s'):
        if key in o:
            _int(o[key], -(1 << 31), (1 << 31) - 1)
    for key in ('sampled_us', 'received_us', 'motion_generation', 'generation', 'observed_us', 'command_generation'):
        if key in o:
            _u64(o[key])
    for key in ('valid', 'config_valid', 'conversion_ready', 'matched', 'stationary', 'verified'):
        if key in o:
            _bool(o[key])
    if 'applied' in o:
        _frame(o['applied'])
    if 'observation' in o:
        _int(o['observation'], 0, 5)


def _request(raw, index, h):
    _require(len(raw) <= 16384 and raw.endswith(b'\n') and raw.count(b'\n') == 1, 'request_framing')
    r = _json(raw[:-1])
    _require(type(r) is dict and r.get('op') in REQUEST_EXTRAS, 'request_operation')
    _keys(r, ('schema', 'version', 'op', 'request_id') + REQUEST_EXTRAS[r['op']], 'request_fields')
    _require(r['schema'] == ABI and type(r['version']) is int and r['version'] == 1, 'request_abi')
    _ascii(r['request_id'], 64)
    _require(_canonical(r) + b'\n' == raw, 'noncanonical_original_request')
    if r['op'] == 'initialize':
        _require(_int(r['fixture_index'], 0, 3) == index and
                 r['expected_config_sha256'] == h['producer']['config_sha256'], 'initialize_binding')
    if 'at_device_us' in r:
        _u64(r['at_device_us'])
    if r['op'] == 'exchange':
        _require(_u64(r['ingress_device_us']) <= _u64(r['at_device_us']), 'ingress_after_processing')
        _packet(r['packet_hex'])
    elif r['op'] == 'observe':
        _observation(r['observation'])
    elif r['op'] == 'set_effect':
        _require(r['driver'] in ('can', 'lighting') and r['effect'] in EFFECTS, 'explicit_effect_required')
    return r


def _describe(response, p):
    expected = {'build_id': p['build_id'], 'base_revision': BASE_REVISION, 'source_sha256': p['source_sha256'],
                'config_sha256': p['config_sha256'], 'operations': list(OPERATIONS),
                'topology': {'fixture_count': 4, 'axis_count': 2, 'lighting_channels': 24, 'ambient_zones': 7,
                             'spotlight_channels': [21, 22, 23],
                             'spotlight_names': ['TEST_WARM_WHITE', 'TEST_NEUTRAL_WHITE', 'TEST_PC_AMBER']},
                'limits': {'request_bytes': 16384, 'response_bytes': 65536, 'steps': 100000,
                           'wire_packet_bytes': 64, 'effects_per_step': 64, 'events_per_step': 64,
                           'admitted_lineages': 100000},
                'explicit_ticks_only': True, 'initial_device_us': '0', 'default_effect': 'rejected',
                'logical_can_only': True, 'supports_axis_acquisition': False, 'spotlight_CCT_calibrated': False}
    _require(_canonical({k: response[k] for k in DESCRIBE_FIELDS}) == _canonical(expected), 'describe_pins')


def _traces(response, counts, request):
    for category, id_key in (('events', 'event_id'), ('effects', 'effect_id'),
                             ('accepted_observations', 'observation_id')):
        rows = response[category]
        _require(type(rows) is list and len(rows) <= 64, 'per_step_trace_bound')
        for row in rows:
            _require(type(row) is dict, 'trace_object_required')
            if category == 'events':
                _keys(row, ('event_id', 'step_id', 'processed_device_us', 'kind', 'result', 'phase', 'at_device_us',
                            'revision_id', 'motion_id', 'state_generation', 'effect_generation', 'sampled_us',
                            'received_us', 'axis', 'value', 'frame24', 'simulated', 'physical_pass', 'context_sha256',
                            'boot_generation', 'session_id', 'ownership_generation', 'plan_sha256'), 'event_fields')
                _require(row['kind'] in EVENTS and row['result'] in RESULTS and row['phase'] in PHASES, 'event_enum')
                for key in ('at_device_us', 'revision_id', 'motion_id', 'state_generation', 'effect_generation',
                            'sampled_us', 'received_us', 'boot_generation', 'session_id', 'ownership_generation'):
                    _u64(row[key])
                _require(_u64(row['at_device_us']) <= _u64(response['processed_device_us']), 'event_future_time')
                _int(row['axis'], 0, 255)
                _int(row['value'], -(1 << 31), (1 << 31) - 1)
                _frame(row['frame24'])
                _require(row['simulated'] is True and row['physical_pass'] is False, 'event_provenance')
                _hex(row['context_sha256'])
                if row['plan_sha256'] is not None:
                    _hex(row['plan_sha256'])
            elif category == 'effects':
                kind = row.get('kind')
                _require(kind in ('can_send', 'lighting_apply', 'lighting_inhibit'), 'effect_kind')
                extra = ('can_id', 'data_hex') if kind == 'can_send' else ('frame24', 'inhibit')
                _keys(row, ('effect_id', 'step_id', 'processed_device_us', 'kind', 'at_device_us', 'generation',
                            'returned_effect') + extra, 'effect_fields')
                _u64(row['generation'])
                _require(row['at_device_us'] == response['processed_device_us'] and row['returned_effect'] in EFFECTS,
                         'effect_original_time_or_result')
                if kind == 'can_send':
                    _int(row['can_id'], 0, 65535)
                    _hex(row['data_hex'], 16)
                    counts['can_attempts'] += 1
                else:
                    _frame(row['frame24'])
                    _require(row['inhibit'] is (kind == 'lighting_inhibit'), 'lighting_inhibit_shape')
                    if kind == 'lighting_inhibit':
                        _require(row['frame24'] == [0] * 24, 'inhibit_frame_not_dark')
                    counts['lighting_attempts'] += 1
            else:
                _keys(row, ('observation_id', 'step_id', 'processed_device_us', 'at_device_us', 'result', 'observation'),
                      'accepted_observation_fields')
                _observation(row['observation'])
                _require(request['op'] == 'observe' and row['observation'] == request['observation'] and
                         row['at_device_us'] == request['at_device_us'] and
                         row['result'] == response['observation_result'] and
                         row['result'] in ('ok', 'thermal_trip'), 'accepted_observation_correlation')
            _require(_u64(row[id_key], True) == counts[category] + 1 and row['step_id'] == response['step_id'] and
                     row['processed_device_us'] == response['processed_device_us'], 'trace_order_or_time')
            counts[category] += 1
    if request['op'] == 'observe':
        accepted = response['observation_result'] == 'ok' or (
            request['observation']['kind'] == 'temperature' and response['observation_result'] == 'thermal_trip')
        _require(len(response['accepted_observations']) == int(accepted), 'observation_acceptance_missing')
    else:
        _require(not response['accepted_observations'], 'unsolicited_accepted_observation')
    _keys(response['counters'], ('steps', *counts), 'counter_fields')
    _require(response['counters']['steps'] == response['step_id'] and all(
        response['counters'][key] == str(value) for key, value in counts.items()), 'counter_gap_or_rewrite')


def _transcript(index, m, h):
    _require(m['attempts'] and len(m['attempts']) <= 100000 and not m['stderr'], 'missing_input_or_stderr')
    _require(len(m['results']) == len(m['attempts']), 'missing_write_result')
    for a in m['attempts']:
        result = m['results'].get(a['request_id'])
        _require(result is not None and result['reported_bytes'] == len(a['raw']) and
                 type(result['reported_bytes']) is int and result['flush_completed'] is True and
                 result['error'] is None, 'unconfirmed_pipe_write')
    requests = [_request(a['raw'], index, h) for a in m['attempts']]
    _require(len(requests) >= 3 and requests[0]['op'] == 'describe' and requests[1]['op'] == 'initialize' and
             requests[-1]['op'] == 'close', 'missing_start_or_owner_close')
    raw_output = bytes(m['stdout'])
    _require(raw_output.endswith(b'\n'), 'truncated_stdout')
    lines = raw_output.splitlines(keepends=True)
    _require(len(lines) == len(requests), 'request_response_count')
    responses, counts = [], {key: 0 for key in ('events', 'effects', 'accepted_observations', 'can_attempts', 'lighting_attempts')}
    current, disconnected, offset = None, False, 0
    fixture, producer = h['fixtures'][index], h['producer']
    for step, (request, raw, attempt) in enumerate(zip(requests, lines, m['attempts']), 1):
        _require(len(raw) <= 65536 and raw.endswith(b'\n'), 'response_framing')
        offset += len(raw)
        completed_ordinal = next(ordinal for end, ordinal in m['stdout_segments'] if end >= offset)
        _require(completed_ordinal > attempt['ordinal'], 'response_precedes_attempt')
        r, op = _json(raw[:-1]), request['op']
        _keys(r, COMMON_RESPONSE + RESPONSE_EXTRAS[op], 'response_fields')
        _require(r['schema'] == ABI and type(r['version']) is int and r['version'] == 1 and
                 r['request_id'] == request['request_id'] == attempt['request_id'] and r['op'] == op,
                 'response_correlation')
        _require(r['ok'] is True and r['evidence_source'] == 'simulated' and r['physical_permission'] is False,
                 'response_refusal_or_provenance')
        _require(r['step_id'] == str(step), 'response_step_order')
        if step > 2:
            _require(op != 'initialize' and (op != 'close' or step == len(requests)), 'reinitialize_or_input_after_close')
        if step == 1:
            _require(r['binding'] is None and r['processed_device_us'] is None, 'invented_preinitialize_binding')
        else:
            expected_binding = {'fixture_index': index, 'device_id': fixture['device_id'], 'boot': fixture['boot'],
                                'config_sha256': producer['config_sha256'], 'source_sha256': producer['source_sha256'],
                                'build_id': producer['build_id']}
            _require(_canonical(r['binding']) == _canonical(expected_binding), 'response_binding')
            at = request.get('at_device_us', '0' if current is None else str(current))
            _require(r['processed_device_us'] == at, 'processed_time_differs')
            number = _u64(at)
            _require(current is None or number >= current, 'device_clock_rollback')
            current = number
        if op == 'describe':
            _describe(r, producer)
        elif op == 'initialize':
            _require(type(r['fixture_index']) is int and r['fixture_index'] == index and
                     r['device_id_hex'] == fixture['device_id'] and r['boot_generation'] == fixture['boot'] and
                     r['at_device_us'] == '0' and r['profile'] == 'fictional-c12-v1', 'initialize_response')
        elif op == 'exchange':
            _require(not disconnected and r['packet_hex'] == request['packet_hex'], 'exchange_echo_or_disconnected')
            _packet(r['reply_hex'])
            wire = r['wire']
            _keys(wire, ('code', 'detail', 'authenticated', 'ingress_device_us', 'processed_device_us'), 'wire_fields')
            _int(wire['code'], 0, 15)
            _int(wire['detail'], 0, 65535)
            _bool(wire['authenticated'])
            _require(wire['ingress_device_us'] == request['ingress_device_us'] and
                     wire['processed_device_us'] == request['at_device_us'], 'wire_time_differs')
        elif op == 'observe':
            _require(r['observation_result'] in RESULTS, 'observation_result_enum')
        elif op == 'set_effect':
            _require(r['driver'] == request['driver'] and r['effect'] == request['effect'], 'effect_setting_echo')
        elif op == 'disconnect':
            _require(not disconnected, 'disconnect_one_shot')
            _require(r['disconnected'] is True, 'disconnect_echo')
            disconnected = True
        elif op == 'close':
            _require(r['closed'] is True and r['terminal'] is True and type(r['exit_code']) is int and
                     r['exit_code'] == 0 and r['exit_reason'] == 'owner_close', 'close_acknowledgement')
        _traces(r, counts, request)
        responses.append(r)
    return {'fixture_index': index, 'fixture': fixture, 'stdin_bytes': b''.join(a['raw'] for a in m['attempts']),
            'stdout_bytes': raw_output, 'stderr_bytes': bytes(m['stderr']), 'requests': requests, 'responses': responses}


def verify_room_native_archive(path, *, expected_header, recording_receipt=None,
                               max_records=MAX_RECORDS, max_bytes=MAX_BYTES):
    """Verify a bounded original archive; confirmed transcripts exist only on success."""
    result = {'status': 'incomplete', 'reason': 'archive_unavailable', 'native_recomputed': False,
              'home_room_replayed': False, 'physical_permission': False,
              'archive_sha256': None, 'archive_bytes': 0, 'record_count': 0}
    try:
        _int(max_records, 1, MAX_RECORDS)
        _int(max_bytes, 1, MAX_BYTES)
        _tree(expected_header)
        _header(expected_header)
        raw = _read_archive(path, max_bytes)
        result.update(archive_sha256=hashlib.sha256(raw).hexdigest(), archive_bytes=len(raw))
        _require(raw and raw.endswith(b'\n'), 'truncated_archive')
        lines = raw.splitlines(keepends=True)
        _require(len(lines) <= max_records, 'archive_record_budget')
        result['record_count'] = len(lines)
        previous, actual_header, final = '0' * 64, None, None
        members = [_member() for _ in range(4)]
        for sequence, line in enumerate(lines, 1):
            _require(len(line) <= MAX_ROW_BYTES and line.endswith(b'\n'), 'archive_row_budget_or_framing')
            row = _json(line[:-1])
            _keys(row, ('schema', 'version', 'sequence', 'kind', 'previous_sha256', 'payload', 'record_sha256'),
                  'archive_row_fields')
            _require(_canonical(row) + b'\n' == line, 'noncanonical_archive_row')
            _require(row['schema'] == 'home-room-fake-plant-record-1' and type(row['version']) is int and
                     row['version'] == 1 and row['sequence'] == str(sequence), 'archive_abi_or_sequence')
            _hex(row['record_sha256'])
            _require(row['previous_sha256'] == previous, 'archive_chain_link')
            unhashed = {k: v for k, v in row.items() if k != 'record_sha256'}
            _require(hashlib.sha256(_canonical(unhashed)).hexdigest() == row['record_sha256'], 'archive_record_digest')
            previous = row['record_sha256']
            _require(final is None, 'record_after_final')
            if sequence == 1:
                _require(row['kind'] == 'header', 'header_first_required')
                _header(row['payload'])
                _require(_canonical(row['payload']) == _canonical(expected_header), 'expected_header_mismatch')
                actual_header = row['payload']
            elif row['kind'] == 'io':
                _io(row['payload'], actual_header, members)
            elif row['kind'] == 'final':
                final = row['payload']
            else:
                raise ArchiveInvalid('archive_kind_or_duplicate_header')
        _require(final is not None, 'missing_final_footer')
        _footer(final, actual_header, members)
        _receipt(recording_receipt, path, final, previous, len(lines))
        transcripts = [_transcript(index, member, actual_header) for index, member in enumerate(members)]
        result.update(status='native_archive_verified', reason='complete_original_transcripts',
                      header=actual_header, transcripts=transcripts)
    except (ArchiveInvalid, OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, StopIteration) as exc:
        result['reason'] = str(exc) if isinstance(exc, ArchiveInvalid) else 'archive_unavailable_or_malformed'
    return result


def replay_room_native_archive(path, *, expected_header, consumer, recording_receipt=None,
                               max_records=MAX_RECORDS, max_bytes=MAX_BYTES):
    """Explicit trusted native recomputation, after independent complete verification.

    consumer(header=..., transcripts=...) must return exactly four index-ordered
    dictionaries containing fixture_index and the three reassembled original byte
    streams. No callback clock, chunk, or cross-pipe ordering comparison is made.
    """
    result = verify_room_native_archive(path, expected_header=expected_header,
                                        recording_receipt=recording_receipt,
                                        max_records=max_records, max_bytes=max_bytes)
    if result['status'] != 'native_archive_verified':
        return result
    try:
        _require(callable(consumer), 'explicit_native_consumer_required')
        expected = [{key: t[key] for key in ('fixture_index', 'stdin_bytes', 'stdout_bytes', 'stderr_bytes')}
                    for t in result['transcripts']]
        actual = consumer(header=copy.deepcopy(result['header']), transcripts=copy.deepcopy(result['transcripts']))
        _require(type(actual) is list and len(actual) == 4, 'four_native_replays_required')
        for index, (received, original) in enumerate(zip(actual, expected)):
            _keys(received, ('fixture_index', 'stdin_bytes', 'stdout_bytes', 'stderr_bytes'), 'native_replay_fields')
            _require(type(received['fixture_index']) is int and received['fixture_index'] == index,
                     'native_replay_fixture_order')
            _require(all(type(received[k]) is bytes and received[k] == original[k] for k in
                         ('stdin_bytes', 'stdout_bytes', 'stderr_bytes')), 'native_replay_original_bytes_differ')
        result.update(status='native_archive_recomputed', reason='exact_original_native_streams', native_recomputed=True)
    except Exception as exc:
        result.update(status='native_recompute_failed', reason=str(exc) if isinstance(exc, ArchiveInvalid)
                      else 'native_consumer_failed', native_recomputed=False)
    return result
