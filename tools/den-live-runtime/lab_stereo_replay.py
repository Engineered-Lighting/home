"""Replay a pinned stereo session only after all its private originals verify.

This local tool combines binary/original integrity and ordinary worker replay.
It does not rerun inference, establish measurement truth, or grant authority.
Expected pins come from the finalized operator receipt, outside these archives.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / 'stack/services/home-context', ROOT / 'stack/services/perception-lab',
                  ROOT / 'tools/shared-home', Path(__file__).parent):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

if __package__:
    from .archive import ArchiveRefusal, _json, _safe
    from .lab_original_archive import encoded, original_digest, read_lab_originals, _capture_clock_policy
    from .lab_stereo_media import SealedStereoMedia
else:
    from archive import ArchiveRefusal, _json, _safe
    from lab_original_archive import encoded, original_digest, read_lab_originals, _capture_clock_policy
    from lab_stereo_media import SealedStereoMedia
from journal import digest


EXPECTED_KEYS = set('schema version causal_sha256 originals_sha256 manifest_digest config_digest scope '
                    'capture_binding capture_receipt_binding semantic_binding desk_binding original_digests media_binding media_refs '
                    'media_manifest_digest replay_source_sha256'.split())
METADATA_KEYS = set('ref capture_epoch capture_seq observation_root_times sensor_root_bindings'.split())
REF_KEYS = set('store_id receipt_id digest component_id observed_at available_at clock_mapping_id'.split())


def _need(condition, reason):
    if not condition:
        raise ArchiveRefusal(reason)


def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _pinned(path, maximum, pin):
    path = Path(path)
    _need(path.is_absolute() and not path.is_symlink() and _sha(pin), 'stereo_replay_fixed_file_required')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(fd)
        _need(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= maximum, 'stereo_replay_file_bound')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        _need(len(raw) <= maximum and hashlib.sha256(raw).hexdigest() == pin, 'stereo_replay_file_pin_mismatch')
        return raw
    finally:
        os.close(fd)


def _expectations(expected):
    _need(isinstance(expected, dict) and set(expected) == EXPECTED_KEYS | (
              {'capture_clock_policy'} if expected.get('version') == 3 else set())
          and type(expected['version']) is int and expected['version'] in (1, 2, 3)
          and expected['schema'] == 'home-live-stereo-replay-inputs-' + str(expected['version']),
          'stereo_replay_expected_pins_required')
    _safe(expected)
    for key in ('causal_sha256', 'originals_sha256', 'manifest_digest', 'config_digest',
                'media_manifest_digest', 'replay_source_sha256'):
        _need(_sha(expected[key]), 'stereo_replay_expected_pins_required')
    scope = expected['scope']
    _need(isinstance(scope, dict) and set(scope) == {'site_id', 'room_id', 'session_id', 'domain_epoch'}
          and all(isinstance(v, str) and v for v in scope.values()), 'stereo_replay_scope_required')
    pins = expected['original_digests']
    _need(isinstance(pins, list) and 1 <= len(pins) <= 96 and all(_sha(p) for p in pins)
          and len(set(pins)) == len(pins), 'stereo_replay_original_pins_required')
    _need(isinstance(expected['capture_binding'], dict)
          and expected['capture_binding'].get('session_id') == scope['session_id']
          and all(isinstance(expected[key], dict) and expected[key].get('scope') == scope
                  for key in ('semantic_binding', 'desk_binding', 'media_binding'))
          and expected['desk_binding'].get('schema') == 'home-lab-desk-archive-binding-' + str(expected['version'] + 1)
          and expected['desk_binding'].get('lab_binding_digest') == expected['media_binding'].get('lab_binding_digest'),
          'stereo_replay_binding_mismatch')
    media_keys = {'scope', 'lab_binding_digest', 'media_generation', 'camera_id'}
    if expected['version'] == 3:
        desk, media = expected['desk_binding'], expected['media_binding']
        _need(set(media) == media_keys | {'original_family', 'bundle_input_policy_sha256'}
              and desk.get('version') == 4
              and desk.get('original_family') == media['original_family'] == 'existing-api-bundled-rgb-1'
              and _sha(media['bundle_input_policy_sha256'])
              and desk.get('bundle_input_policy_sha256') == media['bundle_input_policy_sha256'],
              'stereo_replay_bundled_binding_mismatch')
        _capture_clock_policy(expected['capture_clock_policy'], desk)
    elif expected['version'] == 2:
        desk, media = expected['desk_binding'], expected['media_binding']
        _need(set(media) == media_keys | {'original_family', 'assessment_policy_sha256'}
              and desk.get('version') == 3
              and desk.get('original_family') == media['original_family'] == 'existing-api-assessment-1'
              and _sha(media['assessment_policy_sha256'])
              and desk.get('assessment_policy_sha256') == media['assessment_policy_sha256'],
              'stereo_replay_assessment_binding_mismatch')
    else:
        _need(set(expected['media_binding']) == media_keys, 'stereo_replay_binding_mismatch')
    binding = expected['capture_receipt_binding']
    _need(isinstance(binding, dict) and set(binding) == {'source_id', 'receipt_store_id', 'receipt_component_id', 'root_lineage'}
          and binding['root_lineage'] == 'persisted-observation-record-1'
          and all(isinstance(v, str) and v for v in binding.values()), 'stereo_replay_capture_binding_required')
    return deepcopy(expected)


def _desk_metadata(original):
    inputs = original['inputs']
    anchor, capture = inputs['anchor'], inputs['capture_record']
    policy, support = original['policy'], inputs['stereo_support']
    roots = deepcopy(capture['observation_root_times'])
    sensors = deepcopy(capture['sensor_root_bindings'])
    depth = support['depth']
    _need(depth['root'] not in roots, 'stereo_replay_original_root_conflict')
    roots[depth['root']] = depth['observed_at']
    sensors[depth['root']] = sorted(set().union(*(set(v) for v in sensors.values())))
    return {'ref': {'store_id': policy['receipt_store_id'],
                    'receipt_id': 'desk-original:' + digest([anchor['scope'], policy['source_id'], original['anchor_ref']]),
                    'digest': original_digest(original), 'component_id': policy['receipt_component_id'],
                    'observed_at': anchor['observed_at'], 'available_at': original['produced_at'],
                    'clock_mapping_id': anchor['clock']['mapping_id']},
            'capture_epoch': anchor['source_position']['capture_epoch'],
            'capture_seq': anchor['source_position']['capture_seq'],
            'observation_root_times': roots, 'sensor_root_bindings': sensors}


def _capture_metadata(original, manifest, expected):
    """Reconstruct source attribution and physical roots, including unused cuts."""
    from persisted_evidence import observation_record
    scope = expected['scope']
    payload, event, frame = original['event']['payload'], original['event'], original['frame_metadata']
    sources = manifest['worker_config']['live_manifest']['sources']
    fixed = expected['capture_receipt_binding']
    source_id = fixed['source_id']
    source = sources.get(source_id)
    _need(source is not None and original['method_ref'] in source['method_refs']
          and original['method_ref'] in source['association_method_refs']
          and original['model_ref'] in source['model_refs']
          and fixed['receipt_store_id'] in source['receipt_store_ids']
          and fixed['receipt_component_id'] in source['receipt_component_ids']
          and len(source['sensor_roots']) == 1, 'stereo_replay_capture_source_binding')
    bundled = expected['version'] == 3
    _need(original['schema'] == 'home-original-lab-observation-' + str(2 if bundled else 1),
          'stereo_replay_capture_original_version')
    if bundled:
        _need(source['capture_clock'] == expected['capture_clock_policy']
              and original['bundled_originals']['clock_original'] == original['clock_original'],
              'stereo_replay_bundled_clock_policy_mismatch')
    projected = observation_record(event['id'], event['kind'], payload,
        {'id': scope['session_id'], 'site_id': scope['site_id'], 'camera_id': payload['camera_id']})
    _need(projected is not None, 'stereo_replay_capture_original_invalid')
    observed = payload['observed_at']
    roots = projected['evidence_roots']
    identity = [scope, source_id, payload['arm'], payload['generation'], event['id']]
    return {'ref': {'store_id': fixed['receipt_store_id'], 'receipt_id': 'lab-original:' + digest(identity),
                    'digest': original_digest(original), 'component_id': fixed['receipt_component_id'],
                    'observed_at': observed,
                    'available_at': (max(payload['available_at'], original['first_consumer_received_at'])
                                     if bundled else payload['available_at']),
                    'clock_mapping_id': source['capture_clock']['mapping_id']},
            'capture_epoch': payload['capture_epoch'],
            'capture_seq': (original['clock_original']['component']['sequence'] if bundled else
                            frame['source_timing_receipt']['sequence']),
            'observation_root_times': {root: observed for root in roots},
            'sensor_root_bindings': {root: deepcopy(source['sensor_roots']) for root in roots}}


def _correspondence(records, originals, expected):
    """Require retained bodies for every committed original, including PC/JEV."""
    semantic, captures, desks, capture_metadata = {}, {}, {}, {}
    for pin, original in originals.items():
        family = original['schema']
        if family == 'home-original-lab-admission-1':
            key = (original['original_ref']['store_id'], original['original_ref']['receipt_id'])
            _need(key not in semantic, 'stereo_replay_original_identity_conflict')
            semantic[key] = original['receipt_record']
        elif family in {'home-original-lab-observation-1', 'home-original-lab-observation-2'}:
            captures[pin] = _capture_metadata(original, records[0]['data']['manifest'], expected)
        elif family in {'home-original-lab-desk-2', 'home-original-lab-desk-3', 'home-original-lab-desk-4'}:
            metadata = _desk_metadata(original)
            desks[pin] = metadata
            inputs = original['inputs']
            capture = inputs['capture_record']
            capture_pin = capture['ref']['digest']
            _need(originals.get(capture_pin) == inputs['capture_original'], 'stereo_replay_capture_original_missing')
            _need(capture_pin not in capture_metadata or capture_metadata[capture_pin] == capture,
                  'stereo_replay_capture_metadata_conflict')
            capture_metadata[capture_pin] = capture
            support = inputs['stereo_support']
            nose_version = int(family.rsplit('-', 1)[1]) - 1
            nose = {'schema': 'home-original-lab-stereo-nose-' + str(nose_version), 'version': nose_version, 'scope': expected['scope'],
                    'lab_binding_digest': expected['desk_binding']['lab_binding_digest'],
                    'stereo_policy_ref': expected['desk_binding']['stereo_policy_ref'],
                    'support': support, 'qualification': inputs['stereo_qualification'],
                    'first_consumer_received_at': support['first_consumer_received_at'],
                    'produced_at': support['produced_at']}
            _need(originals.get(original_digest(nose)) == nose, 'stereo_replay_nose_original_missing')
    _need(desks and captures, 'stereo_replay_stereo_originals_required')
    _need(all(captures.get(pin) == metadata for pin, metadata in capture_metadata.items()),
          'stereo_replay_capture_metadata_mismatch')
    prepared, checked = {}, 0
    for row in records[1:]:
        data = row['data']
        if row['kind'] == 'journal_prepare':
            _need(data['tx_id'] not in prepared, 'stereo_replay_transaction_conflict')
            prepared[data['tx_id']] = data
        elif row['kind'] in {'journal_commit', 'journal_abort'}:
            transaction = prepared.pop(data['tx_id'], None)
            _need(transaction is not None, 'stereo_replay_transaction_missing')
            if row['kind'] == 'journal_abort':
                continue
            for metadata in transaction['original_receipts']:
                _need(isinstance(metadata, dict) and METADATA_KEYS <= set(metadata)
                      and isinstance(metadata['ref'], dict) and set(metadata['ref']) == REF_KEYS,
                      'stereo_replay_receipt_shape')
                ref = metadata['ref']
                semantic_record = semantic.get((ref['store_id'], ref['receipt_id']))
                if semantic_record is not None:
                    _need(metadata == semantic_record, 'stereo_replay_semantic_original_mismatch')
                elif ref['digest'] in desks:
                    _need(metadata == desks[ref['digest']], 'stereo_replay_desk_original_mismatch')
                elif ref['digest'] in captures:
                    _need(metadata == captures[ref['digest']],
                          'stereo_replay_capture_original_mismatch')
                else:
                    raise ArchiveRefusal('stereo_replay_committed_original_missing')
                checked += 1
    _need(not prepared and checked > 0, 'stereo_replay_original_effect_unconfirmed')
    return checked


def replay_stereo(causal_archive, original_archive, media_directory, *, expected, timeout_s=15):
    """Verify pinned private inputs, then invoke the unchanged actual JS replay.

    Raises ValueError (or an I/O/subprocess error) for incomplete evidence. This
    function grants no interpretation/geometry/physical authority. In particular
    complete originals are not proof of perception accuracy or full Store export.
    """
    expected = _expectations(expected)
    _need(type(timeout_s) in (int, float) and 0 < timeout_s <= 30, 'stereo_replay_deadline_required')
    causal = _pinned(causal_archive, 64 * 1024**2, expected['causal_sha256'])
    raw_originals = _pinned(original_archive, 8 * 1024**2, expected['originals_sha256'])
    _need(causal.endswith(b'\n') and 2 <= len(causal.splitlines()) <= 16384, 'stereo_replay_causal_bound')
    rows = [_json(line)['record'] for line in causal.splitlines()]
    _need(rows[0]['kind'] == 'session_manifest' and rows[-1]['kind'] == 'session_end'
          and rows[-1]['data']['status'] == 'complete', 'stereo_replay_session_incomplete')
    manifest = rows[0]['data']['manifest']
    _need(digest(manifest) == expected['manifest_digest']
          and manifest.get('session_ref') == expected['scope']
          and manifest.get('configuration_digest') == expected['config_digest']
          and manifest.get('worker_config', {}).get('identity') == expected['scope'], 'stereo_replay_manifest_mismatch')
    if expected['version'] == 3:
        source = manifest['worker_config']['live_manifest']['sources'].get(expected['capture_receipt_binding']['source_id'])
        _need(isinstance(source, dict) and source.get('capture_clock') == expected['capture_clock_policy'],
              'stereo_replay_bundled_clock_policy_mismatch')
    first = _json(raw_originals.splitlines()[0])['body']
    _need(all(first.get(field) == expected[field] for field in ('semantic_binding', 'desk_binding'))
          and first.get('binding') == expected['capture_binding'], 'stereo_replay_original_binding_mismatch')
    sealed = SealedStereoMedia(media_directory, expected_binding=expected['media_binding'],
        expected_refs=expected['media_refs'], manifest_digest=expected['media_manifest_digest'])
    originals = read_lab_originals(original_archive, expected_digests=expected['original_digests'],
        expected_desk_binding=expected['desk_binding'], stereo_media_resolver=sealed.resolve_media,
        **({'capture_clock_policy': expected['capture_clock_policy']} if expected['version'] == 3 else {}))
    _need(set(originals) == set(expected['original_digests']), 'stereo_replay_original_set_mismatch')
    receipts = _correspondence(rows, originals, expected)
    script = Path(__file__).with_name('replay.cjs')
    _pinned(script, 1024**2, expected['replay_source_sha256'])
    node = shutil.which('node')
    _need(node is not None, 'stereo_replay_node_unavailable')
    process = subprocess.run([node, str(script), str(Path(causal_archive))],
        cwd=script.parents[2], capture_output=True, timeout=timeout_s)
    _need(len(process.stdout) <= 65536 and len(process.stderr) <= 65536, 'stereo_replay_output_bound')
    result = _json(process.stdout)
    _need(process.returncode == 0 and result.get('schema') == 'home-live-ordinary-replay-result-1'
          and result.get('status') == 'reproduced'
          and result.get('manifest_digest') == expected['manifest_digest']
          and result.get('config_digest') == expected['config_digest']
          and result.get('physical_authority') is False and result.get('acceptance_eligible') is False,
          'stereo_replay_worker_incomplete')
    # Recheck inputs after separate readers/processes. No changed-on-read file
    # can be certified by a check performed before it was replaced.
    _pinned(causal_archive, 64 * 1024**2, expected['causal_sha256'])
    _pinned(original_archive, 8 * 1024**2, expected['originals_sha256'])
    _pinned(script, 1024**2, expected['replay_source_sha256'])
    for ref in expected['media_refs']:
        sealed.resolve_media(ref)
    _pinned(Path(media_directory) / 'manifest.json', 128 * 1024, expected['media_manifest_digest'])
    return {'schema': 'home-live-stereo-ordinary-replay-result-1', 'version': 1, 'status': 'reproduced',
            'originals_verified': True, 'worker_reproduced': True, 'originals': len(originals),
            'media_objects': len(expected['media_refs']), 'committed_original_receipts': receipts,
            'worker': result, 'physical_authority': False, 'acceptance_eligible': False,
            'inference_reexecuted': False, 'whole_store_replay_proven': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--causal', required=True, type=Path)
    parser.add_argument('--originals', required=True, type=Path)
    parser.add_argument('--media', required=True, type=Path)
    parser.add_argument('--expected', required=True, type=Path,
                        help='Externally retained finalized receipt; never reconstructed from suspect archives')
    args = parser.parse_args()
    try:
        _need(args.expected.is_absolute() and not args.expected.is_symlink(), 'stereo_replay_expected_file_required')
        with args.expected.open('rb') as stream:
            raw = stream.read(256 * 1024 + 1)
        _need(0 < len(raw) <= 256 * 1024, 'stereo_replay_expected_file_bound')
        result = replay_stereo(args.causal, args.originals, args.media, expected=_json(raw))
        code = 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result = {'schema': 'home-live-stereo-ordinary-replay-result-1', 'version': 1, 'status': 'incomplete',
                  'reason': str(error) if isinstance(error, ArchiveRefusal) else 'stereo_replay_inputs_or_worker_unavailable',
                  'originals_verified': False, 'worker_reproduced': False,
                  'physical_authority': False, 'acceptance_eligible': False}
        code = 2
    print(encoded(result).decode('utf-8'))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
