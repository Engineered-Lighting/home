"""Private, bounded original Lab metadata; integer clocks round-trip exactly.

This sidecar does not create evidence authority. It retains the complete
originals referenced by the ordinary journal archive before admission.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import math

if __package__:
    from .archive import ArchiveRefusal, _json, _safe
else:
    from archive import ArchiveRefusal, _json, _safe


def original_digest(record):
    from live_capture import _original_digest
    limit = (512 * 1024 if record.get('schema') in {'home-original-lab-desk-1', 'home-original-lab-desk-2',
                                                   'home-original-lab-desk-3', 'home-original-lab-desk-4',
                                                   'home-original-lab-stereo-nose-1', 'home-original-lab-stereo-nose-2',
                                                   'home-original-lab-stereo-nose-3', 'home-original-lab-observation-2'} else
             2 * 1024**2 if record.get('schema') == 'home-original-lab-admission-1' else 65536)
    return _original_digest(record, limit=limit)


def _desk_binding(value, binding):
    if value is None:
        return None
    from journal import identifier, JournalError
    try:
        base = {'scope', 'policy_digest', 'source_id', 'method_ref', 'receipt_store_id', 'receipt_component_id'}
        bundled = isinstance(value, dict) and value.get('schema') == 'home-lab-desk-archive-binding-4'
        assessment = isinstance(value, dict) and value.get('schema') == 'home-lab-desk-archive-binding-3'
        stereo = bundled or assessment or isinstance(value, dict) and value.get('schema') == 'home-lab-desk-archive-binding-2'
        if (not isinstance(value, dict) or set(value) != base | (
                {'schema', 'version', 'stereo_policy_ref', 'lab_binding_digest'} if stereo else set()) | (
                {'original_family', 'assessment_policy_sha256'} if assessment else
                {'original_family', 'bundle_input_policy_sha256'} if bundled else set())
                or not isinstance(value['scope'], dict)
                or set(value['scope']) != {'site_id', 'room_id', 'session_id', 'domain_epoch'}
                or value['scope']['session_id'] != binding['session_id']):
            raise ValueError()
        for item in (*value['scope'].values(), *(value[k] for k in
                      ('source_id', 'receipt_store_id', 'receipt_component_id'))):
            identifier(item, 'desk_archive_identity')
        ref = value['method_ref']
        if not isinstance(ref, dict) or set(ref) != {'id', 'digest'}:
            raise ValueError()
        identifier(ref['id'], 'desk_method')
        for pin in (value['policy_digest'], ref['digest']):
            if not isinstance(pin, str) or len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin):
                raise ValueError()
        if stereo:
            pin = value['stereo_policy_ref']
            if (type(value['version']) is not int or value['version'] != (4 if bundled else 3 if assessment else 2)
                    or not isinstance(pin, dict) or set(pin) != {'id', 'digest'}):
                raise ValueError()
            identifier(pin['id'], 'stereo_policy_id')
            for item in (pin['digest'], value['lab_binding_digest']):
                if not isinstance(item, str) or len(item) != 64 or any(c not in '0123456789abcdef' for c in item):
                    raise ValueError()
        if assessment:
            pin = value['assessment_policy_sha256']
            if (value['original_family'] != 'existing-api-assessment-1' or not isinstance(pin, str)
                    or len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin)):
                raise ValueError()
        if bundled:
            pin = value['bundle_input_policy_sha256']
            if (value['original_family'] != 'existing-api-bundled-rgb-1' or not isinstance(pin, str)
                    or len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin)):
                raise ValueError()
        _safe(value)
        return _json(encoded(value))
    except (ValueError, TypeError, KeyError, JournalError):
        raise ArchiveRefusal('lab_desk_archive_binding_refused') from None


def _capture_clock_policy(value, desk):
    """Only source6 declares this fixed body; historical manifests stay exact."""
    from journal import JournalError
    from live_ingress_validation import _clock_policy
    bundled = isinstance(desk, dict) and desk.get('schema') == 'home-lab-desk-archive-binding-4'
    try:
        if not bundled:
            if value is not None: raise ValueError()
            return None
        _clock_policy(value)
        if value['original_unit'] != 'unix_s' or value['epoch_origin_s'] != 0:
            raise ValueError()
        return _json(encoded(value))
    except (ValueError, TypeError, KeyError, JournalError):
        raise ArchiveRefusal('lab_bundled_capture_clock_policy_required') from None


def _bundled_binding(desk):
    return {key: desk[key] for key in ('scope', 'lab_binding_digest', 'bundle_input_policy_sha256')}


def _bundled_capture_identity(record, binding, desk, stereo_media_resolver, capture_clock_policy):
    """Recompute the seven exact clock/media originals before capture ACK."""
    from journal import JournalError
    try:
        if (not isinstance(desk, dict) or desk.get('schema') != 'home-lab-desk-archive-binding-4'
                or set(record) != {'schema', 'version', 'event', 'frame_metadata',
                    'first_consumer_received_at', 'method_ref', 'model_ref', 'bundled_originals', 'clock_original'}
                or record.get('schema') != 'home-original-lab-observation-2'
                or type(record.get('version')) is not int or record['version'] != 2
                or record['method_ref'] != binding['method_ref'] or record['model_ref'] != binding['model_ref']
                or not callable(stereo_media_resolver) or capture_clock_policy is None):
            raise ValueError()
        from lab_bundled_rgb_stereo import validate_bundled_rgb_originals
        event, frame, clock = record['event'], record['frame_metadata'], record['clock_original']
        if (not isinstance(event, dict) or event.get('session_id') != binding['session_id']
                or event.get('kind') != 'perception.output' or type(event.get('id')) is not int or event['id'] < 1
                or record['bundled_originals'].get('clock_original') != clock):
            raise ValueError()
        validate_bundled_rgb_originals(record['bundled_originals'], media_refs=clock['media_refs'],
            expected_bundle_binding=_bundled_binding(desk), capture_clock_policy=capture_clock_policy,
            resolve_media=stereo_media_resolver, event=event, frame=frame)
        when = record['first_consumer_received_at']
        if (type(when) not in (int, float) or not math.isfinite(when)
                or when < max(clock['first_consumer_received_at'], event['payload']['available_at'], frame['available_at'])):
            raise ValueError()
        return event['id']
    except (ValueError, TypeError, KeyError, AttributeError, JournalError):
        raise ArchiveRefusal('lab_bundled_capture_original_binding_refused') from None


def _desk_record_identity(record, binding, desk, stereo_media_resolver=None, capture_clock_policy=None):
    """Check original attribution/integrity, never measurement truth/currentness."""
    from journal import digest, JournalError
    from live_capture import _original_digest
    raw_digest = lambda body: _original_digest(body, limit=512 * 1024)
    try:
        fields = {'schema', 'version', 'anchor_ref', 'inputs_digest', 'inputs', 'policy',
                  'first_consumer_received_at', 'produced_at'}
        version = record.get('version')
        if (desk is None or set(record) != fields or type(version) is not int or version not in (1, 2, 3, 4)
                or record.get('schema') != 'home-original-lab-desk-' + str(version)):
            raise ValueError()
        inputs, policy, anchor_ref = record['inputs'], record['policy'], record['anchor_ref']
        input_keys = set('candidate assessment anchor binding capture_ref capture_original capture_record '
                         'projection registration clock_mapping protection_policy'.split())
        if version in (2, 3, 4):
            input_keys |= {'stereo_support', 'stereo_qualification'}
        if (not isinstance(inputs, dict) or set(inputs) != input_keys
                or not isinstance(policy, dict)
                or policy.get('schema') != 'home-lab-live-desk-admission-policy-' + str(version)
                or type(policy.get('version')) is not int or policy['version'] != version
                or digest(policy) != desk['policy_digest']
                or any(policy.get(k) != desk[k] for k in
                       ('source_id', 'method_ref', 'receipt_store_id', 'receipt_component_id'))
                or raw_digest(inputs) != record['inputs_digest']):
            raise ValueError()
        b, anchor, assessment, candidate = (inputs[k] for k in ('binding', 'anchor', 'assessment', 'candidate'))
        if (not all(isinstance(v, dict) for v in (b, anchor, assessment, candidate))
                or b.get('schema') != 'home-desk-enclosure-binding-2' or b.get('version') != 2
                or b.get('scope') != desk['scope'] or anchor.get('scope') != desk['scope']
                or assessment.get('scope') != desk['scope']
                or b.get('method_ref') != desk['method_ref']
                or b.get('association_method_ref') != policy.get('association_method_ref')
                or b.get('anchor_original_ref') != anchor_ref
                or b.get('assessment_ref') != policy.get('assessment_ref')
                or b.get('candidate_digest') != policy.get('candidate_digest')
                or candidate.get('digest') != policy.get('candidate_digest')
                or raw_digest({k: v for k, v in candidate.items() if k != 'digest'}) != candidate['digest']
                or {'id': assessment.get('id'), 'digest': raw_digest(assessment)} != policy.get('assessment_ref')):
            raise ValueError()
        ref_keys = {'store_id', 'receipt_id', 'digest', 'component_id', 'observed_at', 'available_at', 'clock_mapping_id'}
        if (not isinstance(anchor_ref, dict) or set(anchor_ref) != ref_keys
                or anchor.get('schema') != 'home-desk-seated-anchor-2' or type(anchor.get('version')) is not int or anchor['version'] != 2
                or anchor.get('id') != anchor_ref['receipt_id'] or raw_digest(anchor) != anchor_ref['digest']
                or anchor.get('person_ref') != b.get('person_ref')
                or anchor.get('method_ref') != b.get('association_method_ref')
                or anchor.get('source_generation') != b.get('source_generation')
                or anchor.get('source_position') != b.get('source_position')
                or anchor.get('observed_at') != anchor_ref['observed_at']
                or anchor.get('available_at') != anchor_ref['available_at']
                or anchor.get('clock', {}).get('mapping_id') != anchor_ref['clock_mapping_id']):
            raise ValueError()
        from journal import identifier
        for k in ('store_id', 'receipt_id', 'component_id', 'clock_mapping_id'):
            identifier(anchor_ref[k], 'desk_anchor_reference')
        for key in ('first_consumer_received_at', 'produced_at'):
            if type(record[key]) not in (int, float) or not math.isfinite(record[key]) or record[key] < 0:
                raise ValueError()
        if not anchor['observed_at'] <= anchor['available_at'] <= record['produced_at'] or record['first_consumer_received_at'] > record['produced_at']:
            raise ValueError()
        capture, metadata, parent = (inputs[k] for k in ('capture_original', 'capture_record', 'capture_ref'))
        capture_fields = {'schema', 'version', 'event', 'frame_metadata',
                          'first_consumer_received_at', 'method_ref', 'model_ref'}
        if version == 4:
            capture_fields |= {'bundled_originals', 'clock_original'}
            _bundled_capture_identity(capture, binding, desk, stereo_media_resolver, capture_clock_policy)
        if (not isinstance(capture, dict) or set(capture) != capture_fields
                or capture.get('schema') != 'home-original-lab-observation-' + str(2 if version == 4 else 1)
                or capture.get('version') != (2 if version == 4 else 1)
                or capture['event'].get('session_id') != binding['session_id']
                or capture.get('method_ref') != binding['method_ref'] or capture.get('model_ref') != binding['model_ref']
                or not isinstance(metadata, dict) or set(metadata) != {'ref', 'capture_epoch', 'capture_seq', 'observation_root_times', 'sensor_root_bindings'}
                or not isinstance(metadata['ref'], dict) or set(metadata['ref']) != ref_keys
                or metadata['ref']['digest'] != _original_digest(capture)
                or not isinstance(parent, dict) or set(parent) != {'stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest', 'component_id'}
                or type(parent['cursor']) is not int or parent['cursor'] < 1 or parent['component_id'] is not None
                or b.get('parent_publication_refs') != [parent]
                or metadata['capture_epoch'] != anchor['source_position']['capture_epoch']
                or metadata['capture_seq'] != anchor['source_position']['capture_seq']
                or metadata['ref']['observed_at'] != anchor['observed_at']
                or capture['event'].get('kind') != 'perception.output'
                or capture['event']['payload'].get('observed_at') != anchor['observed_at']
                or capture['frame_metadata'].get('observed_at') != anchor['observed_at']
                or capture['event']['payload'].get('frame_id') != capture['frame_metadata'].get('frame_id')
                or (capture['clock_original']['clock']['original_value'] if version == 4 else
                    str(capture['frame_metadata']['source_timing_receipt']['observed_at_unix_ns'])) != anchor['clock'].get('original_value')):
            raise ValueError()
        if ({'id': inputs['projection'].get('id'), 'digest': digest(inputs['projection'])} != policy.get('projection_ref')
                or {'id': inputs['registration'].get('id'), 'digest': digest(inputs['registration'])} != b.get('registration_ref')
                or {'id': inputs['protection_policy'].get('id'), 'digest': digest(inputs['protection_policy'])} != policy.get('protection_policy_ref')
                or b.get('clock_mapping_ref') != {'id': inputs['clock_mapping'].get('mapping_id'), 'digest': inputs['clock_mapping'].get('mapping_digest')}):
            raise ValueError()
        if version in (2, 3, 4):
            if (not callable(stereo_media_resolver) or desk.get('version') != version
                    or policy['stereo_policy_ref'] != desk['stereo_policy_ref']
                    or inputs['stereo_support']['lab_binding_digest'] != desk['lab_binding_digest']):
                raise ValueError()
            if version == 4:
                from lab_bundled_rgb_stereo import validate_bundled_rgb_support as validate_support
                if (policy.get('original_family') != desk['original_family']
                        or policy.get('bundle_input_policy_sha256') != desk['bundle_input_policy_sha256']
                        or inputs['stereo_support'].get('bundled_originals') != capture['bundled_originals']):
                    raise ValueError()
                extra = {'expected_bundle_binding': _bundled_binding(desk),
                         'capture_clock_policy': capture_clock_policy}
            elif version == 3:
                from lab_existing_api_stereo import validate_existing_api_support as validate_support
                if (policy.get('original_family') != desk['original_family']
                        or policy.get('assessment_policy_sha256') != desk['assessment_policy_sha256']):
                    raise ValueError()
                extra = {'expected_assessment_binding': {k: desk[k] for k in
                    ('scope', 'lab_binding_digest', 'assessment_policy_sha256')}}
            else:
                from lab_live_desk_anchor import validate_stereo_support as validate_support
                extra = {}
            roots = metadata['sensor_root_bindings']
            sensors = sorted(set().union(*(set(v) for v in roots.values())))
            validate_support(inputs['stereo_support'], qualification=inputs['stereo_qualification'],
                anchor=anchor, capture_record=metadata, capture_original=capture,
                scope=desk['scope'], sensor_roots=sensors, policy_ref=policy['stereo_policy_ref'],
                now=record['produced_at'], resolve_media=stereo_media_resolver, **extra)
        # Preserve immutable anchor identity; changing body under the same ref is
        # caught above, changing other metadata under that identity is a retry conflict.
        return ('desk', desk['source_id'], desk['receipt_store_id'], desk['receipt_component_id'],
                anchor_ref['store_id'], anchor_ref['receipt_id'], anchor_ref['component_id'])
    except (ValueError, TypeError, KeyError, AttributeError, JournalError):
        raise ArchiveRefusal('lab_desk_original_binding_refused') from None


def _nose_record_identity(record, desk, stereo_media_resolver, capture_clock_policy=None):
    """Retain pre-selection originals; this receipt grants no geometry authority."""
    try:
        fields = {'schema', 'version', 'scope', 'lab_binding_digest', 'stereo_policy_ref', 'support',
                  'qualification', 'first_consumer_received_at', 'produced_at'}
        version = record.get('version')
        bundled = version == 3
        assessment = version == 2
        if (not isinstance(desk, dict) or desk.get('schema') != 'home-lab-desk-archive-binding-' + str(4 if bundled else 3 if assessment else 2)
                or not isinstance(record, dict) or set(record) != fields or type(record['version']) is not int
                or record['version'] not in (1, 2, 3)
                or record['schema'] != 'home-original-lab-stereo-nose-' + str(version)
                or record['scope'] != desk['scope']
                or record['lab_binding_digest'] != desk['lab_binding_digest']
                or record['stereo_policy_ref'] != desk['stereo_policy_ref'] or not callable(stereo_media_resolver)):
            raise ValueError()
        support, qualification = record['support'], record['qualification']
        roles = ({'support', 'anchor_a', 'anchor_b', 'npz', 'calibration_original', 'bundle', 'rgb', 'mapped_depth'}
                 if bundled else {'rgb', 'bundle', 'calibration_original', 'assessment_association', 'mapped_depth'}
                 if assessment else {'rgb', 'bundle', 'paired_original', 'mapped_depth'})
        if (support.get('schema') != 'home-desk-stereo-nose-anchor-' + str(version) or support.get('version') != version
                or support.get('scope') != desk['scope']
                or support.get('lab_binding_digest') != desk['lab_binding_digest']
                or support.get('stereo_policy_ref') != desk['stereo_policy_ref']
                or support.get('produced_at') != record['produced_at']
                or support.get('first_consumer_received_at') != record['first_consumer_received_at']
                or qualification.get('schema') != 'home-desk-stereo-qualification-1'
                or qualification.get('version') != 1
                or {'id': qualification['stereo_policy']['id'], 'digest': original_digest(qualification['stereo_policy'])}
                    != desk['stereo_policy_ref']
                or set(support['media_refs']) != roles):
            raise ValueError()
        for key in ('first_consumer_received_at', 'produced_at'):
            if type(record[key]) not in (int, float) or not math.isfinite(record[key]) or record[key] < 0:
                raise ValueError()
        if record['first_consumer_received_at'] > record['produced_at']:
            raise ValueError()
        for ref in support['media_refs'].values():
            retained = stereo_media_resolver(ref)
            if (not isinstance(retained, dict) or set(retained) != {'record', 'data'}
                    or any(retained['record'][key] != ref[key] for key in ('id', 'sha256', 'size'))
                    or type(retained['data']) is not bytes or len(retained['data']) != ref['size']
                    or hashlib.sha256(retained['data']).hexdigest() != ref['sha256']):
                raise ValueError()
        if bundled:
            from lab_bundled_rgb_stereo import validate_bundled_rgb_originals, validate_bundled_mapped_depth
            if (support.get('original_family') != desk['original_family'] or capture_clock_policy is None
                    or qualification.get('clock_mapping') != capture_clock_policy):
                raise ValueError()
            checked = validate_bundled_rgb_originals(support['bundled_originals'], media_refs=support['media_refs'],
                expected_bundle_binding=_bundled_binding(desk), capture_clock_policy=capture_clock_policy,
                resolve_media=stereo_media_resolver)
            validate_bundled_mapped_depth(checked['rows'], support=checked['support'],
                binding=support['bundled_originals']['start']['payload']['binding'],
                depth_clock_policy=qualification['depth_clock_policy'], projection=qualification['projection'])
            if support['first_consumer_received_at'] < max(checked['clock_original']['first_consumer_received_at'],
                    *(row['created_at'] for row, _ in checked['rows'].values())):
                raise ValueError()
        elif assessment:
            from lab_existing_api_stereo import validate_existing_api_originals
            if support.get('original_family') != desk['original_family']:
                raise ValueError()
            validate_existing_api_originals(support,
                expected_assessment_binding={k: desk[k] for k in
                    ('scope', 'lab_binding_digest', 'assessment_policy_sha256')},
                resolve_media=stereo_media_resolver)
        return ('stereo-nose', desk['source_id'], support['id'])
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ArchiveRefusal('lab_stereo_nose_original_binding_refused') from None


def semantic_archive_binding(scope, policy):
    """Fixed server composition, never browser-selected archive authority."""
    from journal import digest
    return {'scope': dict(scope), 'policy_digest': digest(policy),
            'sources': {family: {k: policy[family][k] for k in
                ('source_id', 'method_ref', 'model_ref', 'receipt_store_id', 'receipt_component_id')}
                for family in ('ha', 'jev')}}


def _semantic_binding(value, binding):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'scope', 'policy_digest', 'sources'}
            or not isinstance(value['scope'], dict)
            or set(value['scope']) != {'site_id', 'room_id', 'session_id', 'domain_epoch'}
            or value['scope']['session_id'] != binding['session_id']
            or not isinstance(value['policy_digest'], str) or len(value['policy_digest']) != 64
            or set(value['sources']) != {'ha', 'jev'}):
        raise ArchiveRefusal('lab_semantic_archive_binding_refused')
    fields = {'source_id', 'method_ref', 'model_ref', 'receipt_store_id', 'receipt_component_id'}
    if any(not isinstance(source, dict) or set(source) != fields for source in value['sources'].values()):
        raise ArchiveRefusal('lab_semantic_archive_binding_refused')
    return _json(encoded(value))


def _record_identity(record, binding, semantic, desk=None, stereo_media_resolver=None, capture_clock_policy=None):
    """Validate both write and read against the same immutable family binding."""
    if record.get('schema') in {'home-original-lab-desk-1', 'home-original-lab-desk-2', 'home-original-lab-desk-3', 'home-original-lab-desk-4'}:
        return _desk_record_identity(record, binding, desk, stereo_media_resolver, capture_clock_policy)
    if record.get('schema') in {'home-original-lab-stereo-nose-1', 'home-original-lab-stereo-nose-2', 'home-original-lab-stereo-nose-3'}:
        return _nose_record_identity(record, desk, stereo_media_resolver, capture_clock_policy)
    if record.get('schema') == 'home-original-lab-observation-2':
        return _bundled_capture_identity(record, binding, desk, stereo_media_resolver, capture_clock_policy)
    if record.get('schema') == 'home-original-lab-observation-1':
        if (set(record) != {'schema', 'version', 'event', 'frame_metadata',
                'first_consumer_received_at', 'method_ref', 'model_ref'}
                or record['version'] != 1 or record['event'].get('session_id') != binding['session_id']
                or record['method_ref'] != binding['method_ref'] or record['model_ref'] != binding['model_ref']
                or isinstance(desk, dict) and desk.get('version') == 4):
            raise ArchiveRefusal('lab_original_binding_refused')
        identity = record['event'].get('id')
        if type(identity) is not int or identity < 1:
            raise ArchiveRefusal('lab_original_event_id_refused')
        return identity
    fields = {'schema', 'version', 'session_id', 'family', 'source_id', 'method_ref',
              'model_ref', 'original_ref', 'receipt_record', 'original', 'first_consumer_received_at'}
    if (semantic is None or set(record) != fields
            or record.get('schema') != 'home-original-lab-admission-1'
            or type(record.get('version')) is not int or record['version'] != 1
            or record['session_id'] != binding['session_id'] or record['family'] not in semantic['sources']):
        raise ArchiveRefusal('lab_original_binding_refused')
    source = semantic['sources'][record['family']]
    ref, receipt, original = record['original_ref'], record['receipt_record'], record['original']
    when = record['first_consumer_received_at']
    if (any(record[k] != source[k] for k in ('source_id', 'method_ref', 'model_ref'))
            or not isinstance(ref, dict) or not isinstance(receipt, dict) or not isinstance(original, dict)
            or ref.get('store_id') != source['receipt_store_id']
            or ref.get('component_id') != source['receipt_component_id'] or receipt.get('ref') != ref
            or not isinstance(ref.get('receipt_id'), str) or not ref['receipt_id']
            or type(when) not in (int, float) or not math.isfinite(when) or when < 0):
        raise ArchiveRefusal('lab_semantic_original_binding_refused')
    if original.get('schema') == 'home-original-pc-input-1':
        from journal import digest
        if (record['family'] != 'ha' or type(original.get('version')) is not int or original['version'] != 1
                or original.get('scope') != semantic['scope']
                or original.get('source_id') != source['source_id']
                or original.get('method_ref') != source['method_ref']
                or receipt.get('pc_input_original') != original or digest(original) != ref.get('digest')):
            raise ArchiveRefusal('lab_semantic_original_binding_refused')
    elif original.get('schema') in {'home-original-lab-semantic-1', 'home-original-lab-semantic-2'}:
        from live_capture import _original_digest
        session = original.get('session', {})
        expected_version = 2 if original['schema'].endswith('-2') else 1
        if (type(original.get('version')) is not int or original['version'] != expected_version
                or session.get('id') != binding['session_id'] or session.get('site_id') != semantic['scope']['site_id']
                or original.get('policy_digest') != semantic['policy_digest']
                or original.get('first_consumer_received_at') != when
                or _original_digest(original, limit=2 * 1024**2) != ref.get('digest')
                or 'pc_input_original' in receipt):
            raise ArchiveRefusal('lab_semantic_original_binding_refused')
    else:
        raise ArchiveRefusal('lab_semantic_original_binding_refused')
    return (record['family'], ref['store_id'], ref['receipt_id'])


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


def sync_parent(path):
    """The Victoria/Linux runtime must confirm namespace durability too."""
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class LabOriginalArchive:
    def __init__(self, filename, *, binding, max_records=24, max_bytes=2 * 1024**2,
                 min_free_bytes=1024**2, semantic_binding=None, desk_binding=None, stereo_media_resolver=None,
                 capture_clock_policy=None):
        self.path = Path(filename)
        if (not self.path.is_absolute() or not self.path.parent.is_dir()
                or set(binding) != {'session_id', 'method_ref', 'model_ref'}
                or not isinstance(binding['session_id'], str) or not binding['session_id']
                or type(max_records) is not int or not 1 <= max_records <= 96
                or type(max_bytes) is not int or not 1024 <= max_bytes <= 8 * 1024**2
                or type(min_free_bytes) is not int or min_free_bytes < 1):
            raise ArchiveRefusal('lab_original_archive_configuration_refused')
        _safe(binding)
        self.binding = _json(encoded(binding))
        self.semantic_binding = _semantic_binding(semantic_binding, self.binding)
        _safe(self.semantic_binding)
        self.desk_binding = _desk_binding(desk_binding, self.binding)
        self.capture_clock_policy = _capture_clock_policy(capture_clock_policy, self.desk_binding)
        if stereo_media_resolver is not None and not callable(stereo_media_resolver):
            raise ArchiveRefusal('fixed_stereo_media_resolver_required')
        self.stereo_media_resolver = stereo_media_resolver
        self.max_records, self.max_bytes, self.min_free_bytes = max_records, max_bytes, min_free_bytes
        self.lock, self.records = threading.RLock(), {}
        self.bytes, self.failed, self.closed = 0, False, False
        self.previous = None
        self.file_hash = hashlib.sha256()
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            self._append({'kind': 'manifest', 'binding': self.binding,
                **({'semantic_binding': self.semantic_binding} if self.semantic_binding is not None else {}),
                **({'desk_binding': self.desk_binding} if self.desk_binding is not None else {}),
                **({'capture_clock_policy': self.capture_clock_policy} if self.capture_clock_policy is not None else {})})
            sync_parent(self.path.parent)
        except BaseException:
            os.close(self.fd)
            self.closed = True
            raise

    def _append(self, record):
        if self.failed or self.closed:
            raise ArchiveRefusal('lab_original_archive_unavailable')
        body = {'schema': 'home-lab-original-record-1', 'version': 1,
                'previous': self.previous, **record}
        raw = encoded(body)
        identity = hashlib.sha256(raw).hexdigest()
        line = encoded({'body': body, 'sha256': identity}) + b'\n'
        if self.bytes + len(line) > self.max_bytes:
            self.failed = True
            raise ArchiveRefusal('lab_original_archive_byte_limit')
        if shutil.disk_usage(self.path.parent).free - len(line) < self.min_free_bytes:
            self.failed = True
            raise ArchiveRefusal('lab_original_archive_disk_headroom')
        try:
            offset = 0
            while offset < len(line):
                count = os.write(self.fd, line[offset:])
                if count <= 0:
                    raise OSError('short_write')
                offset += count
            os.fsync(self.fd)
        except BaseException:
            self.failed = True
            raise
        self.bytes += len(line)
        self.file_hash.update(line)
        self.previous = identity

    def __call__(self, record):
        with self.lock:
            if self.closed or self.failed:
                raise ArchiveRefusal('lab_original_archive_unavailable')
            _safe(record)
            if not isinstance(record, dict):
                raise ArchiveRefusal('lab_original_binding_refused')
            identity = _record_identity(record, self.binding, self.semantic_binding, self.desk_binding,
                                       self.stereo_media_resolver, self.capture_clock_policy)
            raw = encoded(record)
            fingerprint = original_digest(record)
            if identity in self.records:
                if self.records[identity] != (fingerprint, raw):
                    raise ArchiveRefusal('lab_original_identity_conflict')
                return {'status': 'retained', 'digest': fingerprint}
            if len(self.records) >= self.max_records:
                raise ArchiveRefusal('lab_original_archive_record_limit')
            # JSON text protects original nanosecond integers from JavaScript
            # number rounding in surrounding runtime formats.
            self._append({'kind': 'original', 'digest': fingerprint, 'original_json': raw.decode('utf-8')})
            self.records[identity] = (fingerprint, raw)
            return {'status': 'retained', 'digest': fingerprint}

    def finalize(self):
        with self.lock:
            if not self.closed:
                try:
                    if not self.failed:
                        self._append({'kind': 'end', 'count': len(self.records)})
                finally:
                    os.close(self.fd)
                    self.closed = True
            return {'status': 'incomplete' if self.failed else 'finalized',
                    'records': len(self.records), 'sha256': self.file_hash.hexdigest()}


def read_lab_originals(filename, *, expected_digests=(), expected_desk_binding=None, stereo_media_resolver=None,
                       capture_clock_policy=None):
    with Path(filename).open('rb') as stream:
        raw = stream.read(8 * 1024**2 + 1)
    if len(raw) > 8 * 1024**2 or not raw.endswith(b'\n'):
        raise ArchiveRefusal('lab_original_archive_incomplete')
    lines = raw.splitlines()
    if not 2 <= len(lines) <= 98:
        raise ArchiveRefusal('lab_original_archive_record_limit')
    previous, originals, identities, binding, semantic, desk, clock_policy = None, {}, set(), None, None, None, None
    for index, line in enumerate(lines):
        wrapper = _json(line)
        if set(wrapper) != {'body', 'sha256'}:
            raise ArchiveRefusal('lab_original_archive_structure_refused')
        body = wrapper['body']
        if (body.get('schema') != 'home-lab-original-record-1' or body.get('version') != 1
                or body.get('previous') != previous
                or hashlib.sha256(encoded(body)).hexdigest() != wrapper['sha256']):
            raise ArchiveRefusal('lab_original_archive_digest_refused')
        previous = wrapper['sha256']
        if index == 0:
            if body.get('kind') != 'manifest':
                raise ArchiveRefusal('lab_original_archive_manifest_required')
            binding = body['binding']
            semantic = _semantic_binding(body.get('semantic_binding'), binding)
            desk = _desk_binding(body.get('desk_binding'), binding)
            clock_policy = _capture_clock_policy(body.get('capture_clock_policy'), desk)
            if clock_policy is None and 'capture_clock_policy' in body:
                raise ArchiveRefusal('lab_bundled_capture_clock_policy_required')
            if clock_policy != _capture_clock_policy(capture_clock_policy, desk):
                raise ArchiveRefusal('lab_bundled_capture_clock_policy_changed')
            if desk is not None:
                if (set(body) != {'schema', 'version', 'previous', 'kind', 'binding', 'desk_binding'}
                        | ({'semantic_binding'} if semantic is not None else set())
                        | ({'capture_clock_policy'} if clock_policy is not None else set())
                        or expected_desk_binding is None
                        or encoded(desk) != encoded(_desk_binding(expected_desk_binding, binding))):
                    raise ArchiveRefusal('lab_desk_archive_binding_refused')
            elif expected_desk_binding is not None:
                raise ArchiveRefusal('lab_desk_archive_binding_refused')
        elif index == len(lines) - 1:
            if body.get('kind') != 'end' or body.get('count') != len(originals):
                raise ArchiveRefusal('lab_original_archive_incomplete')
        else:
            if body.get('kind') != 'original':
                raise ArchiveRefusal('lab_original_archive_structure_refused')
            record = _json(body['original_json'])
            _safe(record)
            fingerprint = original_digest(record)
            identity = _record_identity(record, binding, semantic, desk, stereo_media_resolver, clock_policy)
            if (fingerprint != body.get('digest') or fingerprint in originals or identity in identities):
                raise ArchiveRefusal('lab_original_archive_binding_refused')
            originals[fingerprint] = record
            identities.add(identity)
    if not set(expected_digests) <= originals.keys():
        raise ArchiveRefusal('lab_original_archive_missing_original')
    return originals
