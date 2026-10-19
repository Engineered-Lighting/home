"""Pure one-person/configured-desk preview; never operational geometry.

The caller owns the existing metric packet, original event and retirement clock.
This module opens no files, retains no event/clock, and issues no authority.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import re

SCHEMA = 'home-apartment-virtual-target-preview-1'
LABEL = 'Person visible · configured desk preview'
MAX_ORIGINAL_BYTES = 65536
PACK_KEYS = ('content_revision', 'registration_version', 'frame_sha256', 'collision_sha256')
OBSERVATION_FIELDS = ('request_id', 'arm', 'site_id', 'camera_id', 'frame_id', 'media_ids',
    'capture_epoch', 'generation', 'run_id', 'model_version', 'artifact_sha256', 'engine_epoch',
    'observed_at', 'available_at', 'valid_until')
NO_AUTHORITY = dict(operational_geometry=False, geometry_authority=False, identity_authority=False,
    engagement_authority=False, absence_authority=False, protection_authority=False,
    beam_authority=False, physical_permission=False, follow_allowed=False, lighting_authority=False,
    physical_commands=False)
_HEX = re.compile(r'[a-f0-9]{64}\Z')


class DeskPreviewRefusal(ValueError):
    pass


def _need(ok, reason):
    if not ok:raise DeskPreviewRefusal(reason)


def _finite(v):return type(v) in (int, float) and math.isfinite(v)
def _text(v):return isinstance(v, str) and 0 < len(v) <= 256
def _sha(v):return isinstance(v, str) and _HEX.fullmatch(v) is not None
def _vector(v):return isinstance(v, list) and len(v) == 3 and all(_finite(n) and abs(n) <= 1000 for n in v)
def _unit(v):return _vector(v) and abs(math.hypot(*v)-1) < 1e-6
def _exact(v, keys):return isinstance(v, dict) and set(v) == set(keys)
def _encoded(v):return json.dumps(v, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
def _digest(v):return hashlib.sha256(_encoded(v)).hexdigest()


def _iou(a, b):
    try:
        ax, ay, aw, ah, bx, by, bw, bh = (float(v[k]) for v in (a, b) for k in ('x', 'y', 'w', 'h'))
    except (KeyError, TypeError, ValueError):
        return 0.
    if not all(map(math.isfinite, (ax, ay, aw, ah, bx, by, bw, bh))) or min(aw, ah, bw, bh) <= 0:return 0.
    inter = max(0., min(ax+aw, bx+bw)-max(ax, bx))*max(0., min(ay+ah, by+bh)-max(ay, by))
    return inter/(aw*ah+bw*bh-inter)


def distinct_people(result):
    """Person entries of one model result, merging the detector's duplicate boxes.

    A person box with its own confidence below 0.4 and IoU >= 0.9 against a
    higher-confidence person box is the same person (it never gets a pose
    crop), so only the higher-confidence entry is kept. Everything else counts.
    """
    objects, boxes = result.get('objects'), result.get('boxes') or []
    people = [o for o in objects if o == 'person' or isinstance(o, dict) and o.get('label') == 'person']
    shapes = []
    for o in people:
        found = [b for b in boxes[:512] if isinstance(b, dict) and b.get('label') == 'person'
                 and isinstance(o, dict) and o.get('instance_id') is not None and b.get('instance_id') == o['instance_id']]
        box = found[0] if len(found) == 1 else None
        confidence = o.get('confidence', box.get('confidence') if box else None) if isinstance(o, dict) else None
        shapes.append((box, confidence if _finite(confidence) else None))
    return [o for i, o in enumerate(people) if not (shapes[i][0] is not None and shapes[i][1] is not None and shapes[i][1] < .4
        and any(j != i and box is not None and c is not None and c > shapes[i][1] and _iou(shapes[i][0], box) >= .9
                for j, (box, c) in enumerate(shapes)))]


def _json(raw):
    def pairs(rows):
        result = {}
        for k,v in rows:
            _need(k not in result, 'desk_preview_duplicate_configuration_key');result[k] = v
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: _need(False, 'desk_preview_nonfinite_configuration'))


def unavailable(reason):
    if not isinstance(reason, str) or re.fullmatch(r'[a-z][a-z0-9_]{0,159}', reason) is None:
        reason = 'desk_preview_unavailable'
    return dict(schema=SCHEMA, version=1, status='unavailable', available=False, reason=reason,
        label=LABEL, virtual_preview_only=True, qualification=None, scope=None, frame=None,
        observation=None, provenance=None, source_times=None, timing=None, valid_until=None,
        target=None, person_point=None, **NO_AUTHORITY)


def _configuration(raw, pack):
    c = _json(raw)
    _need(_exact(pack, PACK_KEYS) and _text(pack['content_revision'])
        and type(pack['registration_version']) is int and pack['registration_version'] >= 0
        and all(_sha(pack[k]) for k in PACK_KEYS[2:]), 'desk_preview_pack_required')
    _need(_exact(c, ('schema','version','scope','binding','equipment_ref','target_ref','target',
        'fixtures','omitted','truth_role','qualification','allocation','operational_geometry',
        'physical_commands','digest','input_files'))
        and c['schema'] == 'home-registered-desk-aim-candidate-1' and type(c['version']) is int
        and c['version'] == 1 and c['scope'] == dict(site_id='victoria',map_id='victoria-den',room_id='den')
        and c['truth_role'] == 'calculated_virtual_aim_candidate' and _sha(c['digest'])
        and all(c[k] is None for k in ('qualification','allocation','operational_geometry'))
        and c['physical_commands'] is False, 'desk_preview_closed_candidate_required')
    b, e, t, ref = c['binding'], c['equipment_ref'], c['target'], c['target_ref']
    _need(_exact(b, ('content_revision','registration_version','manifest_digest','frame_digest','canonical_collision_digest'))
        and (b['content_revision'],b['registration_version'],b['frame_digest'],b['canonical_collision_digest'])
            == tuple(pack[k] for k in PACK_KEYS) and _sha(b['manifest_digest'])
        and _exact(e, ('revision','digest')) and type(e['revision']) is int and e['revision'] >= 0 and _sha(e['digest'])
        and _exact(ref, ('schema','digest')) and ref['schema'] == 'home-registered-desk-target-2' and _sha(ref['digest'])
        and _exact(c['input_files'], ('target_sha256','equipment_sha256','manifest_sha256'))
        and all(_sha(v) for v in c['input_files'].values()), 'desk_preview_target_binding_changed')
    _need(_exact(t, ('kind','vertices_m','centre_m','normal_unit','planarity')) and t['kind'] == 'polygon'
        and isinstance(t['vertices_m'], list) and len(t['vertices_m']) == 4 and all(_vector(v) for v in t['vertices_m'])
        and _vector(t['centre_m']) and _unit(t['normal_unit']) and t['normal_unit'][2] > 0
        and _exact(t['planarity'], ('max_distance_m','qualified')) and t['planarity']['qualified'] is False
        and _finite(t['planarity']['max_distance_m']) and t['planarity']['max_distance_m'] >= 0
        and all(abs(t['centre_m'][i]-sum(v[i] for v in t['vertices_m'])/4) < 1e-6 for i in range(3)),
        'desk_preview_target_geometry_invalid')
    fixtures = c['fixtures']
    _need(isinstance(fixtures, list) and len(fixtures) == 4 and isinstance(c['omitted'], list)
        and len(c['omitted']) <= 32, 'desk_preview_fixture_binding_invalid')
    ids, mounts, calculated = set(), set(), 0
    for f in fixtures:
        _need(isinstance(f, dict) and _text(f.get('fixture_id')) and _text(f.get('mount_id'))
            and f['fixture_id'] not in ids and f['mount_id'] not in mounts
            and f.get('physical_commands') is False and f.get('optical_origin_m') is None
            and f.get('target_m') == t['centre_m'] and _vector(f.get('modeled_origin_m')),
            'desk_preview_fixture_binding_invalid')
        ids.add(f['fixture_id']);mounts.add(f['mount_id'])
        if f.get('status') == 'unavailable':
            _need(_exact(f, ('fixture_id','mount_id','status','reason','modeled_origin_m','optical_origin_m','target_m','physical_commands'))
                and _text(f['reason']), 'desk_preview_fixture_binding_invalid')
            continue
        _need(_exact(f, ('fixture_id','mount_id','status','display_anchor_m','modeled_origin_m','optical_origin_m',
            'origin_role','horizontal_world_reference','preview_angles','delivery_angles','target_m','direction_unit',
            'distance_m','coverage','obstruction','protection','hardware_zero_mapping','physical_commands'))
            and f['status'] == 'calculated' and _vector(f['display_anchor_m']) and _text(f['origin_role'])
            and _unit(f['horizontal_world_reference']) and _unit(f['direction_unit'])
            and _finite(f['distance_m']) and 0 < f['distance_m'] <= 100
            and all(f[k] == 'unassessed' for k in ('coverage','obstruction','protection'))
            and f['hardware_zero_mapping'] is None, 'desk_preview_fixture_binding_invalid')
        a, d = f['preview_angles'], f['delivery_angles']
        _need(_exact(a, ('convention','pan_deg','tilt_deg'))
            and a['convention'] == 'apartment_clockwise_pan_tilt_below_horizontal_degrees'
            and _exact(d, ('convention','local_to_room','pan_deg','tilt_deg'))
            and d['convention'] == 'counterclockwise_pan_tilt_from_down_minus_Z_degrees'
            and isinstance(d['local_to_room'], list) and len(d['local_to_room']) == 9
            and all(_finite(v) for v in d['local_to_room'])
            and all(_finite(x[k]) for x in (a,d) for k in ('pan_deg','tilt_deg')),
            'desk_preview_fixture_binding_invalid')
        calculated += 1
    _need(calculated > 0, 'desk_preview_no_calculated_fixture')
    return c


def bind_desk_preview(candidate_original_bytes, expected_sha256, pack):
    """Pin already reviewed private CLI bytes, without authenticating survey truth.

    The original's embedded JS digest is retained as provenance; the independently
    supplied SHA pins the exact bytes, so no Journal/canonical-number alias is used.
    """
    try:
        _need(type(candidate_original_bytes) is bytes and 0 < len(candidate_original_bytes) <= MAX_ORIGINAL_BYTES
            and _sha(expected_sha256) and hashlib.sha256(candidate_original_bytes).hexdigest() == expected_sha256,
            'desk_preview_original_pin_required')
        _configuration(candidate_original_bytes, pack)
        return ConfiguredDeskPreview(candidate_original_bytes, expected_sha256, tuple(pack[k] for k in PACK_KEYS))
    except DeskPreviewRefusal:raise
    except (ValueError,KeyError,TypeError,UnicodeError,OverflowError,RecursionError) as error:
        raise DeskPreviewRefusal('desk_preview_configuration_invalid') from error


@dataclass(frozen=True)
class ConfiguredDeskPreview:
    _original: bytes
    _sha256: str
    _pack: tuple

    @property
    def retained_byte_allocations(self):return (self._original,)

    def project(self, approximate, *, event, session_state, development=False):
        try:return self._project(approximate, event, session_state, development)
        except DeskPreviewRefusal as error:return unavailable(str(error))
        except (ValueError,KeyError,TypeError,AttributeError,OverflowError,RecursionError):
            return unavailable('desk_preview_original_input_invalid')

    def _project(self, a, event, state, development=False):
        _need(state == 'recording', 'desk_preview_source_stopped')
        _need(isinstance(a, dict) and a.get('schema') == 'home-apartment-approximate-room-pose-1'
            and a.get('status') == 'approximate' and a.get('renderable') is True
            and a.get('session_state') == state and a.get('room_pose') is None
            and a.get('physical_commands', False) is False
            and all(a.get(k) is False for k in NO_AUTHORITY if k != 'physical_commands'),
            'desk_preview_approximation_unavailable')
        pose, frame, timing = a['approximate_pose'], a['frame'], a['timing']
        provenance, scope = pose['provenance'], pose['scope']
        pack = dict(zip(PACK_KEYS, self._pack));c = _configuration(self._original, pack)
        _need(scope.get('siteId') == 'victoria' and scope.get('cameraId') == 'den'
            and (scope['contentRevision'],scope['registrationVersion'],scope['frameDigest'])
                == tuple(pack[k] for k in PACK_KEYS[:3])
            and pose['registration']['canonical_collision_digest'] == pack['collision_sha256']
            and pose['qualification'] == 'approximate_visual_alignment' and pose['error_bound_m'] is None
            and pose['registration']['source_fit_status'] == 'refused', 'desk_preview_scope_changed')
        _need(isinstance(event, dict) and len(_encoded(event)) <= 2*1024**2 and event.get('kind') == 'perception.output'
            and type(event.get('id')) is int and event['id'] > 0 and event['id'] == provenance['source_event_id']
            and _digest(event) == provenance['original_event_sha256'], 'desk_preview_original_event_changed')
        p = event['payload']
        _need(event['session_id'] == scope['sessionId'] and p['site_id'] == frame['site_id'] == 'victoria'
            and p['camera_id'] == frame['camera_id'] == 'den' and p['arm'] == 'b'
            and p['frame_id'] == frame['frame_id'] == pose['frame_id']
            and p['capture_epoch'] == frame['source_epoch'] == pose['source_epoch']
            and p['media_ids'] == [frame['media_id']] == [provenance['frame_media_id']]
            and _sha(frame['media_sha256']) and frame['media_sha256'] == provenance['frame_media_sha256']
            and (frame['width'],frame['height']) == (640,360)
            and p['result'].get('schema_version') == 1 and p['result'].get('coordinate_space') == 'source_pixels'
            and p['result'].get('frame_id') == p['frame_id']
            and frame['generation'] == provenance['media_generation'] == scope['generation']
            and type(p['generation']) is int and p['generation'] >= 1
            and p['generation'] == provenance['model_generation']
            and type(pose['instance_index']) is int and pose['instance_index'] == 0
            and all(_text(p[k]) for k in ('request_id','run_id','model_version','artifact_sha256','engine_epoch')),
            'desk_preview_original_frame_changed')
        from persisted_evidence import observation_record
        from jev_evidence import project
        objects, raw_poses = p['result'].get('objects'), p['result'].get('pose_instances')
        _need(isinstance(objects, list) and len(objects) <= 512 and isinstance(raw_poses, list) and len(raw_poses) == 1,
            'desk_preview_single_person_required')
        kept = distinct_people(p['result'])
        _need(len(kept) == 1 and isinstance(kept[0], dict) and kept[0].get('instance_id') is not None
            and isinstance(raw_poses[0], dict) and kept[0]['instance_id'] == raw_poses[0].get('instance_id'),
            'desk_preview_single_person_required')
        record = observation_record(event['id'], event['kind'], p,
            dict(id=event['session_id'],site_id='victoria',camera_id='den'))
        _need(record is not None, 'desk_preview_original_pose_invalid')
        normalized = project([record])[0]
        poses = normalized['pose_instances']
        _need(len(poses) == 1, 'desk_preview_single_person_required')
        people = [o for o in normalized['objects'] if o.get('label') == 'person'
                  and o.get('instance_ref') is not None and o['instance_ref'] == poses[0].get('instance_ref')]
        _need(len(people) == 1 and people[0]['instance_ref'] == pose['instance_ref'],
            'desk_preview_original_instance_changed')
        joints = pose['joints'];raw = {j['joint_id']:j for j in poses[0]['joints']}
        _need(isinstance(joints, list) and 1 <= len(joints) <= 17
            and len({j['id'] for j in joints}) == len(joints), 'desk_preview_shoulders_unavailable')
        selected = []
        for index in (5,6):
            rows = [j for j in joints if j['id'] == index]
            _need(len(rows) == 1, 'desk_preview_shoulders_unavailable')
            j = rows[0];original = raw.get(index)
            _need(j.get('visible') is True and j.get('error_bound_m') is None and _vector(j.get('position_m'))
                and type(j.get('depth_support')) is int and j['depth_support'] >= 3
                and original is not None and original.get('status') == 'supported'
                and original.get('pixel') == j.get('pixel'), 'desk_preview_shoulders_unavailable')
            selected.append(j)
        _need(timing.get('status') == 'unverified' and timing.get('clock_qualification') == 'unavailable'
            and timing.get('exposure_error_bound_s') is None
            and timing.get('projection_qualification') == 'unqualified_nominal_eeprom'
            and timing.get('receipt_semantics') == 'display_retirement_only_not_exposure_freshness'
            and _finite(timing.get('remaining_receipt_hold_s')) and 0 < timing['remaining_receipt_hold_s'] <= 2
            and _finite(timing.get('receipt_age_s')) and timing['receipt_age_s'] >= 0
            and timing['receipt_age_s']+timing['remaining_receipt_hold_s'] <= 2+1e-9,
            'desk_preview_receipt_expired')
        times = pose['source_times'];rgb = frame['original_times']
        # Development preview timing: a late output is a warning recorded by
        # the owner, never a rewritten time; strict refuses it as before.
        _need(all(_finite(p[k]) and times[k] == p[k] for k in ('observed_at','available_at','valid_until'))
            and p['observed_at'] <= p['available_at'] and (development or p['available_at'] < p['valid_until'])
            and all(_finite(rgb[k]) for k in ('observed_at','available_at','valid_until'))
            and rgb['observed_at'] <= rgb['available_at'] < rgb['valid_until'], 'desk_preview_original_times_invalid')
        observation = dict(schema='home-lab-arm-observation-1',session_id=event['session_id'],
            **{k:deepcopy(p[k]) for k in OBSERVATION_FIELDS})
        return dict(schema=SCHEMA,version=1,status='preview',available=True,reason=None,label=LABEL,
            virtual_preview_only=True,qualification=None,scope=deepcopy(scope),frame=deepcopy(frame),
            observation=observation,provenance={**deepcopy(provenance),'configured_candidate_sha256':self._sha256},
            source_times=deepcopy(times),timing=deepcopy(timing),valid_until=min(p['valid_until'],rgb['valid_until']),
            target=dict(kind='configured_desk_centre',frame='z_up_metric_floor0',unit='m',
                point_m=deepcopy(c['target']['centre_m']),normal_unit=deepcopy(c['target']['normal_unit']),
                target_ref=deepcopy(c['target_ref']),equipment_ref=deepcopy(c['equipment_ref']),binding=deepcopy(c['binding'])),
            person_point=dict(method='approximate_supported_shoulder_midpoint',joint_ids=[5,6],
                point_m=[sum(j['position_m'][i] for j in selected)/2 for i in range(3)],
                instance_ref=deepcopy(pose['instance_ref']),error_bound_m=None),**NO_AUTHORITY)
