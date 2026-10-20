"""Bounded, inert view of existing Lab evidence for authenticated Home readers.

This is a diagnostic/semantic projection, not journal admission. It owns no
capture, model, clock, network connection or engagement history. Original Lab
evidence remains in its incumbent ledger; images, arrays and arbitrary text
are never copied into this view.
"""
from copy import deepcopy
import json
import math
import re

from jev_evidence import HA_FACTS, project, qwen_projection, vjepa_projection
from persisted_evidence import observation_record
from reasoning import ACTIVITIES, digest
import qwen_scene
import vjepa_scene
from room_words import projection as room_projection


SCHEMA = 'home-lab-stack-context-1'
FAMILIES = ('capture', 'depth', 'calibration', 'detector_pose', 'devices',
            'qwen_scene', 'reasoning', 'temporal_encoder', 'room_facts', 'lifecycle')
MAX_EVENTS, MAX_MEDIA, MAX_QWEN_RECEIPTS = 1024, 512, 32
MAX_INPUT_BYTES, MAX_OUTPUT_BYTES = 8 * 1024**2, 256 * 1024
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')
TIMES = ('observed_at', 'available_at', 'valid_until', 'generated_at', 'first_received_at')
RELATIVE_TIMES = ('source_end_s', 'available_at_s')
IDS = ('site_id', 'camera_id', 'session_id', 'capture_epoch', 'frame_id', 'request_id',
       'engine_epoch', 'model_version', 'run_id', 'prompt_version', 'rule', 'snapshot_key')
HASHES = ('artifact_sha256', 'body_sha256', 'response_sha256', 'snapshot_digest',
          'calibration_id', 'calibration_content_id')
STATUSES = frozenset(('requested', 'starting', 'recording', 'stopping', 'stopped',
    'running', 'idle', 'ready', 'completed', 'valid', 'invalid', 'abstain', 'stale',
    'unavailable', 'unknown', 'failed', 'refused', 'rejected', 'cancelled',
    'indeterminate', 'queued', 'dispatching', 'dropped', 'interrupted', 'active',
    'draining', 'quiescent', 'incomplete', 'not_admitted', 'queue_expired', 'in_flight'))
REASONS = frozenset(('occupancy_unknown', 'occupancy_vacant', 'source_sampling_cadence',
    'replaced_by_latest', 'queue_busy', 'thermal_shed', 'admission_closed',
    'engine_epoch_unavailable', 'transport_not_connected', 'terminal_proof_missing',
    'no_depth_in_window', 'model_failed', 'expired_on_receipt', 'expired_before_write',
    'worker_cleanup_pending', 'preparation_cancelled', 'owner_stop', 'controller_restart_execution_unknown'))
DEFAULT_HA_BINDINGS = {'derived.computer_active': 'computer_active',
                       'derived.computer_input_recency': 'computer_input_recency'}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _identifier(value):
    return type(value) is str and ID.fullmatch(value) is not None


def _encoded(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise ValueError('bounded_json_lab_inputs_required') from None


def _lineage(payload, session, *, event=None, external=False):
    result = {key: payload[key] for key in IDS if _identifier(payload.get(key))}
    for key in HASHES:
        if type(payload.get(key)) is str and HEX.fullmatch(payload[key]):
            result[key] = payload[key]
    for key in TIMES:
        if _finite(payload.get(key)):
            result[key] = payload[key]
    if all(_finite(payload.get(key)) for key in RELATIVE_TIMES) and _finite(session.get('started_at')):
        result['original_relative_clock'] = {key: payload[key] for key in RELATIVE_TIMES}
        result['original_relative_clock'].update(time_basis='session_relative_seconds',
            session_started_at=session['started_at'], mapping='lab_session_start_plus_original_offset')
    for key in ('evidence_roots', 'media_ids', 'frame_ids'):
        values = payload.get(key)
        if (isinstance(values, list) and len(values) <= 64
                and all(_identifier(item) for item in values)):
            result[key] = list(values)
    for key in ('generation', 'sequence', 'timing_generation'):
        if type(payload.get(key)) is int and payload[key] >= 0:
            result[key] = payload[key]
    if payload.get('source_clock') in {'depthai_device_monotonic', 'depthai_host_monotonic'}:
        result['source_clock'] = payload['source_clock']
    if _finite(payload.get('source_timestamp_seconds')):
        result['source_timestamp_seconds'] = payload['source_timestamp_seconds']
    timing = payload.get('source_timing_receipt')
    if isinstance(timing, dict):
        # JavaScript cannot represent current UTC nanoseconds exactly. Preserve
        # original integer values as decimal strings on this browser-facing view;
        # do not change the original receipt or the numerical source contract.
        original = {key: timing[key] for key in (
            'exposure_duration_seconds', 'uncertainty_seconds', 'host_clock_uncertainty_seconds')
            if _finite(timing.get(key))}
        for key in ('observed_at_unix_ns', 'frame_device_ns'):
            if key in timing:
                if type(timing[key]) is not int or timing[key] < 0:
                    raise ValueError('original_capture_clock_integer_required')
                original[key] = str(timing[key])
        if original:
            result['original_capture_clock'] = original
    if payload.get('arm') in ('a', 'b'):
        result['arm'] = payload['arm']
    if event is not None:
        result['source_event_id'] = event['id']
        result['source_event_kind'] = event['kind']
        if _finite(event.get('created_at')):
            result['event_first_received_at'] = event['created_at']
    result.setdefault('session_id', session['id'])
    result['session_relation'] = 'external_same_camera' if external else 'bound_lab_session'
    result['physical_roots_supplied'] = bool(result.get('evidence_roots'))
    return result


def _lease(payload, cutoff):
    if not all(_finite(payload.get(key)) for key in ('observed_at', 'available_at', 'valid_until')):
        return 'unavailable'
    if not payload['observed_at'] <= payload['available_at'] <= cutoff:
        return 'unavailable'
    if payload['valid_until'] < payload['observed_at']:
        return 'invalid'
    return 'current' if cutoff < payload['valid_until'] else 'stale'


def _reasoning_clock(payload, session):
    """Map the actual LiveReasoning output's documented relative clock fields.

    The source emits source_end_s/available_at_s relative to its saved session
    origin, with an absolute valid_until. Event receipt time is not that origin
    and cannot fill missing or invalid source-clock information.
    """
    if all(_finite(payload.get(key)) for key in ('observed_at', 'available_at')):
        return payload
    start = session.get('started_at')
    if (not _finite(start) or start < 0
            or not all(_finite(payload.get(key)) and payload[key] >= 0 for key in RELATIVE_TIMES)
            or payload['available_at_s'] < payload['source_end_s']):
        return payload
    return {**payload, 'observed_at': start + payload['source_end_s'],
            'available_at': start + payload['available_at_s']}


def _base(configured):
    return {'configured': configured,
            'state': 'not_configured' if configured is False else 'unavailable',
            'reason': 'source_not_configured' if configured is False else 'no_evidence_at_cutoff',
            'data': None, 'lineage': None, 'latest_attempt': None}


def _attempt(event, payload):
    status = payload.get('status', payload.get('state', event['kind'].rsplit('.', 1)[-1]))
    reason = payload.get('reason', payload.get('last_reason'))
    result = {'source_event_id': event['id'], 'source_event_kind': event['kind'],
              'status': status if type(status) is str and status in STATUSES else 'unknown'}
    when = payload.get('available_at', payload.get('completed_at', event.get('created_at')))
    if _finite(when):
        result['available_at'] = when
    if reason is not None:
        result['reason'] = reason if type(reason) is str and reason in REASONS else 'source_reported_reason'
        result['reason_digest'] = digest(reason)
    return result


def _head(records):
    return max(records, key=lambda item: (item[0].get('observed_at', -1),
                                          item[0].get('available_at', -1), str(item[1] or '')), default=None)


def project_lab_live_stack(session, *, events, media=(), cutoff, arm='b', generation,
                           ha_bindings=None, configured=None, qwen_receipts=()):
    """Project original incumbent records already read under existing authority.

    `configured` optionally maps family names to bools from trusted startup
    provisioning. No entry means unknown, not disabled. External Qwen receipts
    are original same-camera receipts supplied by the existing fact-log callback;
    their original day-session IDs remain explicit. Nothing is made fresh by a
    call to this function. Results confer no geometry/identity/engagement/beam
    authority and cannot be substituted for live journal observations.
    """
    if (not isinstance(session, dict)
            or any(not _identifier(session.get(key)) for key in ('id', 'site_id', 'camera_id'))
            or arm not in ('a', 'b') or type(generation) is not int or generation < 1
            or not _finite(cutoff) or not isinstance(events, (list, tuple)) or len(events) > MAX_EVENTS
            or not isinstance(media, (list, tuple)) or len(media) > MAX_MEDIA
            or not isinstance(qwen_receipts, (list, tuple)) or len(qwen_receipts) > MAX_QWEN_RECEIPTS):
        raise ValueError('bounded_bound_lab_stack_required')
    configured = {} if configured is None else configured
    bindings = DEFAULT_HA_BINDINGS if ha_bindings is None else ha_bindings
    if (not isinstance(configured, dict) or set(configured) - set(FAMILIES)
            or any(type(value) is not bool for value in configured.values())
            or not isinstance(bindings, dict) or len(bindings) > 64
            or any(not _identifier(key) or value not in HA_FACTS for key, value in bindings.items())):
        raise ValueError('registered_lab_projection_configuration_required')
    if len(_encoded([events, media, qwen_receipts])) > MAX_INPUT_BYTES:
        raise ValueError('lab_stack_input_budget_exhausted')
    families = {key: _base(configured.get(key)) for key in FAMILIES}
    heads = {key: [] for key in FAMILIES}
    attempts = {key: [] for key in FAMILIES}
    seen = {}
    known_requests = {event['payload']['request_id'] for event in events
        if isinstance(event, dict) and isinstance(event.get('payload'), dict)
        and event.get('session_id', session['id']) == session['id']
        and event['payload'].get('arm') == arm and event['payload'].get('generation') == generation
        and _identifier(event['payload'].get('request_id'))}

    for event in events:
        if (not isinstance(event, dict) or type(event.get('id')) is not int or event['id'] < 1
                or event.get('session_id', session['id']) != session['id']
                or not isinstance(event.get('payload'), dict) or not isinstance(event.get('kind'), str)):
            raise ValueError('original_bound_lab_event_required')
        fingerprint = digest(event)
        if event['id'] in seen:
            if seen[event['id']] != fingerprint:
                raise ValueError('lab_stack_event_identity_conflict')
            continue
        seen[event['id']] = fingerprint
        payload, kind = event['payload'], event['kind']
        if any(key in payload and payload[key] != session[key] for key in ('site_id', 'camera_id')):
            raise ValueError('lab_stack_source_scope_mismatch')
        if 'session_id' in payload and payload['session_id'] != session['id']:
            raise ValueError('lab_stack_source_session_mismatch')
        if kind == 'reasoning.output':
            payload = _reasoning_clock(payload, session)
        if payload.get('arm') is not None:
            if payload['arm'] != arm or payload.get('generation') not in (None, generation):
                continue
            if payload.get('generation') is None and payload.get('request_id') not in known_requests:
                continue
        when = payload.get('available_at', payload.get('completed_at', event.get('created_at')))
        if not _finite(when) or when > cutoff:
            continue
        family = ('detector_pose' if kind.startswith('perception.') else
                  'devices' if kind == 'ha.observation' else
                  'qwen_scene' if kind.startswith('qwen.') else
                  'temporal_encoder' if kind.startswith('vjepa.') else
                  'reasoning' if kind.startswith('reasoning.') else
                  'room_facts' if kind.startswith(('room.', 'spatial.')) else
                  'lifecycle' if kind.startswith(('recording.', 'runtime.', 'worker.', 'session.')) else None)
        if family is None:
            continue
        attempts[family].append(_attempt(event, payload))
        if kind not in {'perception.output', 'ha.observation', 'qwen.observation',
                        'vjepa.observation', 'reasoning.output', 'room.facts'}:
            continue
        if _lease(payload, cutoff) == 'unavailable':
            continue
        heads[family].append((payload, event['id'], event))

    for receipt in qwen_receipts:
        if (not isinstance(receipt, dict) or receipt.get('site_id') != session['site_id']
                or receipt.get('camera_id') != session['camera_id'] or not _identifier(receipt.get('session_id'))
                or not _identifier(receipt.get('request_id'))
                or receipt.get('schema') != qwen_scene.RECEIPT_SCHEMA):
            raise ValueError('original_same_camera_qwen_receipt_required')
        if qwen_scene.causal(receipt, cutoff) and _lease(receipt, cutoff) != 'unavailable':
            heads['qwen_scene'].append((receipt, receipt.get('request_id'), None))

    for family, records in heads.items():
        selected = _head(records)
        if selected is None:
            continue
        payload, _, event = selected
        state, data = _lease(payload, cutoff), None
        if family in {'detector_pose', 'devices'}:
            kind = 'perception.output' if family == 'detector_pose' else 'ha.observation'
            record = observation_record(event['id'], kind, payload, session)
            projected = project([record], ha_bindings=bindings) if record is not None else []
            if projected:
                if family == 'detector_pose':
                    data = {'coordinate_space': 'source_pixels',
                        **{key: projected[0][key] for key in ('objects', 'pose_instances', 'quality') if key in projected[0]}}
                else:
                    data = {'facts': deepcopy(projected[0].get('ha', {})),
                        'camera_derived_facts': deepcopy(projected[0].get('quality', {}).get('camera_derived_ha_facts', [])),
                        'meaning': 'device_state_not_person_identity_or_work_geometry'}
        elif family == 'qwen_scene':
            clean, reason = qwen_scene.validate_facts(payload.get('facts'))
            if payload.get('status') == 'valid' and reason is None:
                receipt = {**payload, 'facts': clean}
                data = qwen_projection(qwen_scene.projection([receipt], cutoff))
        elif family == 'temporal_encoder':
            data = vjepa_projection(vjepa_scene.projection([payload], cutoff))
            if data is not None and data.get('status') == 'invalid':
                data = None
        elif family == 'room_facts':
            data = room_projection((payload.get('result') or {}).get('room_facts'))
        elif family == 'reasoning':
            judgment = payload.get('judgment')
            if (isinstance(judgment, dict) and judgment.get('activity') in ACTIVITIES
                    and payload.get('status') in {'ready', 'abstain', 'stale'}):
                data = {'activity': judgment['activity'], 'source_role': 'activity_interpretation',
                        'engagement_authority': False, 'calibration': 'unvalidated'}
                for key in ('confidence', 'probability'):
                    if _finite(judgment.get(key)) and 0 <= judgment[key] <= 1:
                        data[key] = judgment[key]
                data['confidence_kind'] = 'provider_reported_uncalibrated'
        families[family].update(state=state if data is not None else 'invalid',
            reason='original_evidence' if data is not None else 'source_output_not_projectable',
            data=data, lineage=_lineage(payload, session, event=event,
                external=family == 'qwen_scene' and event is None and payload['session_id'] != session['id']))

    for item in media:
        if (not isinstance(item, dict) or not _identifier(item.get('id'))
                or item.get('session_id', session['id']) != session['id'] or not isinstance(item.get('metadata'), dict)):
            raise ValueError('original_bound_lab_media_required')
        meta = item['metadata']
        if meta.get('site_id') != session['site_id'] or meta.get('camera_id') != session['camera_id']:
            raise ValueError('lab_stack_media_scope_mismatch')
        received = item.get('created_at')
        if not _finite(received) or received > cutoff:
            continue
        stream = meta.get('stream')
        family = 'capture' if stream == 'rgb' else 'depth' if stream == 'rgbd_bundle' else None
        if family is None:
            continue
        selected = families[family]
        if selected['lineage'] is not None:
            old = selected['lineage']
            rank = (meta.get('observed_at', -1), received)
            previous = (old.get('observed_at', -1), old.get('media_first_received_at', -1))
            if rank < previous:
                continue
        lineage = _lineage(meta, session)
        lineage.update(media_id=item['id'], media_first_received_at=received)
        if type(item.get('sha256')) is str and HEX.fullmatch(item['sha256']):
            lineage['media_sha256'] = item['sha256']
        data = {'stream': stream, 'clock_mapping_status': meta.get('clock_mapping_status')
                if meta.get('clock_mapping_status') in {'verified', 'unverified'} else 'unknown'}
        if family == 'capture':
            selected.update(state=_lease(meta, cutoff) if data['clock_mapping_status'] == 'verified' else 'unavailable',
                            reason='registered_rgb_metadata', data=data, lineage=lineage)
        else:
            data.update(depth_alignment=meta.get('depth_alignment') if meta.get('depth_alignment') == 'rectified_left' else 'unknown',
                geometric_validity='unvalidated', rgb_aligned=meta.get('rgb_aligned') is True)
            frames = (meta.get('source_metadata') or {}).get('frames')
            if isinstance(frames, dict):
                lineage['original_source_frames'] = {name: {key: frames[name][key] for key in
                    ('sequence', 'device_timestamp_seconds', 'host_monotonic_timestamp_seconds')
                    if _finite(frames[name].get(key))}
                    for name in ('rgb', 'depth', 'left', 'right') if isinstance(frames.get(name), dict)}
            selected.update(state='available', reason='stored_depth_not_qualified_geometry', data=data, lineage=lineage)
            calibration = {key: meta[key] for key in ('calibration_id', 'calibration_content_id')
                           if type(meta.get(key)) is str and HEX.fullmatch(meta[key])}
            if calibration:
                families['calibration'].update(state='available', reason='stored_calibration_not_room_registration',
                    data=calibration, lineage=deepcopy(lineage))

    for family, records in attempts.items():
        if records:
            latest = max(records, key=lambda row: (row.get('available_at', -1), row['source_event_id']))
            families[family]['latest_attempt'] = latest
            if families[family]['data'] is None:
                state = latest['status']
                families[family].update(state='refused' if state in {'refused', 'rejected', 'not_admitted'} else
                    'invalid' if state == 'invalid' else 'unavailable', reason='source_status_at_cutoff')
        if configured.get(family) is False:
            families[family].update(state='not_configured', reason='source_not_configured')

    result = {'schema': SCHEMA, 'version': 1,
        'binding': {'site_id': session['site_id'], 'camera_id': session['camera_id'],
                    'lab_session_id': session['id'], 'arm': arm, 'generation': generation},
        'as_of': cutoff, 'families': families, 'operational_geometry': None,
        'identity_authority': False, 'engagement_authority': False, 'beam_authority': False}
    if len(_encoded(result)) > MAX_OUTPUT_BYTES:
        raise ValueError('lab_stack_output_budget_exhausted')
    return result
