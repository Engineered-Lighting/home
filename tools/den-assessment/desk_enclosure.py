"""Pure, owner-local desk-envelope eligibility; no publication or qualification.

The caller must resolve immutable assessment/anchor originals from fixed trusted
stores and supply current publication/head bindings. This module checks their
internal consistency; a matching hash is not independent measurement truth.
The guide/scorer do not yet produce the independently reviewed assessment used
here. Successful output is an admission proposal, never a live geometry event.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math
import re

METHOD = 'desk-static-envelope-v1'
DISTINCT_METHOD = 'desk-distinct-parts-envelope-v2'
PARTS = ('body', 'head', 'hand_left', 'hand_right', 'protection')
SCOPE = ('site_id', 'room_id', 'session_id', 'domain_epoch')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')


class Refusal(ValueError):
    pass


def _need(value, reason):
    if not value:
        raise Refusal(reason)


def _exact(value, keys, reason):
    _need(type(value) is dict and set(value) == set(keys.split()), reason)


def _number(value, low=0, high=10**12):
    _need(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
          'finite_bounded_value_required')
    return value


def _identifier(value):
    _need(type(value) is str and ID.fullmatch(value), 'identifier_required')


def _encoded(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise Refusal('bounded_plain_json_required') from None
    _need(len(raw) <= 128 * 1024, 'input_budget_exhausted')
    return raw


def digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _ref(value):
    _exact(value, 'id digest', 'immutable_reference_required')
    _identifier(value['id'])
    _need(type(value['digest']) is str and SHA.fullmatch(value['digest']), 'digest_required')


def _scope(value):
    _exact(value, ' '.join(SCOPE), 'scope_required')
    for field in SCOPE:
        _identifier(value[field])


def _person(value, scope):
    _exact(value, ' '.join((*SCOPE, 'pid', 'track_epoch')), 'person_scope_required')
    _need(all(value[key] == scope[key] for key in SCOPE), 'person_scope_mismatch')
    _need(type(value['pid']) is int and 0 < value['pid'] <= 9007199254740991, 'person_id_required')
    _identifier(value['track_epoch'])


def _subject(value):
    _exact(value, 'subject_id subject_epoch', 'subject_epoch_required')
    _identifier(value['subject_id'])
    _identifier(value['subject_epoch'])


def _position(value, version=1):
    _exact(value, 'capture_epoch capture_seq revision', 'source_position_required')
    _identifier(value['capture_epoch'])
    if version == 1:
        _identifier(value['revision'])
    else:
        _need(type(value['revision']) is int and 0 <= value['revision'] <= 9007199254740991,
              'integer_source_revision_required')
    _need(type(value['capture_seq']) is int and 0 <= value['capture_seq'] <= 9007199254740991,
          'capture_sequence_required')


def _original(value):
    _exact(value, 'store_id receipt_id digest component_id observed_at available_at clock_mapping_id',
           'original_receipt_reference_required')
    for key in ('store_id', 'receipt_id', 'component_id', 'clock_mapping_id'):
        _identifier(value[key])
    _need(type(value['digest']) is str and SHA.fullmatch(value['digest']), 'digest_required')
    _number(value['observed_at']); _number(value['available_at'])


def _parents(values):
    _need(type(values) is list and 1 <= len(values) <= 8, 'current_parent_publications_required')
    seen = set()
    for value in values:
        _exact(value, 'stream_epoch cursor producer_id id payload_digest component_id',
               'publication_reference_required')
        for key in ('stream_epoch', 'producer_id', 'id'):
            _identifier(value[key])
        _need(type(value['cursor']) is int and 0 < value['cursor'] <= 9007199254740991,
              'publication_cursor_required')
        _need(type(value['payload_digest']) is str and SHA.fullmatch(value['payload_digest']),
              'digest_required')
        if value['component_id'] is not None:
            _identifier(value['component_id'])
        key = (value['stream_epoch'], value['cursor'])
        _need(key not in seen, 'duplicate_parent_publication')
        seen.add(key)


def _vector(value):
    _need(type(value) is list and len(value) == 3, 'metric_vector_required')
    return [_number(item, -100, 100) for item in value]


def _box(value):
    _exact(value, 'kind center_m half_extents_m', 'conservative_box_required')
    _need(value['kind'] == 'aabb', 'conservative_box_required')
    _vector(value['center_m'])
    half = _vector(value['half_extents_m'])
    _need(all(item > 0 for item in half), 'positive_enclosing_bounds_required')


def _contains(box, point, radius):
    errors = radius if type(radius) is list else [radius] * 3
    return all(abs(point[i] - box['center_m'][i]) + errors[i] <= box['half_extents_m'][i]
               for i in range(3))


def _inflate(box, error):
    return {**deepcopy(box), 'half_extents_m': [item + error for item in box['half_extents_m']]}


def _binding(value):
    _exact(value, 'schema version scope person_ref subject_ref device_id workstation_id method_ref '
           'association_method_ref assessment_ref candidate_digest registration_ref clock_mapping_ref '
           'source_generation source_position anchor_original_ref parent_publication_refs', 'binding_required')
    version = value['version']
    _need(type(version) is int and version in (1, 2)
          and value['schema'] == 'home-desk-enclosure-binding-' + str(version), 'binding_version_required')
    _scope(value['scope']); _person(value['person_ref'], value['scope']); _subject(value['subject_ref'])
    for key in ('device_id', 'workstation_id'):
        _identifier(value[key])
    for key in ('method_ref', 'association_method_ref', 'assessment_ref', 'registration_ref', 'clock_mapping_ref'):
        _ref(value[key])
    _need(value['method_ref']['id'] == (METHOD if version == 1 else DISTINCT_METHOD), 'desk_method_required')
    _need(type(value['candidate_digest']) is str and SHA.fullmatch(value['candidate_digest']), 'digest_required')
    _need(type(value['source_generation']) is int and 0 <= value['source_generation'] <= 9007199254740991,
          'source_generation_required')
    _position(value['source_position'], version); _original(value['anchor_original_ref'])
    _parents(value['parent_publication_refs'])


def _candidate(value, binding):
    version = binding['version']
    _need(type(value) is dict and value.get('schema') == 'home-desk-geometry-candidate-' + str(version)
          and type(value.get('version')) is int and value['version'] == version
          and value.get('status') == 'candidate' and value.get('qualified') is False
          and value.get('proposed_method') == (METHOD if version == 1 else DISTINCT_METHOD)
          and value.get('frame') == 'z_up_metric_floor0' and value.get('unit') == 'm'
          and value.get('operational_geometry') is None and value.get('person_identity') is None
          and value.get('authority') == dict(geometry=False, engagement=False, beam=False, physical=False),
          'unqualified_guide_candidate_required')
    _need(value.get('digest') == binding['candidate_digest']
          == digest({key: item for key, item in value.items() if key != 'digest'}),
          'candidate_digest_mismatch')
    _need(value.get('survey_sha256') == digest(value.get('original_survey')), 'survey_original_mismatch')
    _box(value.get('work_patch'))
    if version == 1:
        _box(value.get('seated_person_envelope'))
    else:
        _exact(value.get('parts'), ' '.join(PARTS), 'separately_measured_parts_required')
        for part in value['parts'].values():
            _box(part)
        _ref(value.get('protection_policy_ref'))
        survey = value.get('original_survey')
        _need(type(survey) is dict and survey.get('schema') == 'home-desk-geometry-survey-2'
              and type(survey.get('version')) is int and survey['version'] == 2,
              'original_distinct_survey_required')
        _need(type(survey.get('protection_policy')) is dict
              and survey['protection_policy'].get('policy_ref') == value['protection_policy_ref'],
              'original_protection_policy_mismatch')


def _assessment(value, binding, candidate, now):
    _need(value is not None, 'independent_assessment_unavailable')
    version = binding['version']
    shape_keys = ('enclosure' if version == 1 else
                  'work parts protection_policy_ref protected_parts anchor_part')
    _exact(value, 'schema version id status scope person_ref subject_ref device_id workstation_id '
           'candidate_digest method_ref association_method_ref registration clock_mapping anchor_region '
           + shape_keys + ' part_coverage hidden_configuration_basis reference_protocol_ref requirements_ref '
           'assessment_report_ref observed_at available_at valid_until max_anchor_age_s '
           'max_anchor_error_m motion_bound', 'independent_assessment_contract_required')
    _need(value['schema'] == 'home-desk-enclosure-assessment-' + str(version) and type(value['version']) is int
          and value['version'] == version and value['status'] == 'independently_assessed',
          'independent_assessment_unavailable')
    _need(value['id'] == binding['assessment_ref']['id']
          and digest(value) == binding['assessment_ref']['digest'], 'assessment_original_mismatch')
    for key in ('scope', 'person_ref', 'subject_ref', 'device_id', 'workstation_id',
                'candidate_digest', 'method_ref', 'association_method_ref'):
        _need(value[key] == binding[key], 'assessment_scope_or_epoch_mismatch')
    for key in ('reference_protocol_ref', 'requirements_ref', 'assessment_report_ref'):
        _ref(value[key])
    _need(value['hidden_configuration_basis'] == 'independent_all_supported_hidden_configurations'
          and value['part_coverage'] == list(PARTS), 'complete_hidden_configuration_bound_unavailable')
    _box(value['anchor_region'])
    if version == 1:
        _box(value['enclosure'])
        _need(_contains(value['enclosure'], candidate['seated_person_envelope']['center_m'],
                        candidate['seated_person_envelope']['half_extents_m']), 'survey_enclosure_undercovered')
    else:
        _exact(value['parts'], ' '.join(PARTS), 'assessed_distinct_parts_required')
        for name in PARTS:
            _box(value['parts'][name])
            measured = candidate['parts'][name]
            _need(_contains(value['parts'][name], measured['center_m'], measured['half_extents_m']),
                  'survey_' + name + '_undercovered')
        _box(value['work'])
        _need(_contains(value['work'], candidate['work_patch']['center_m'], candidate['work_patch']['half_extents_m']),
              'survey_work_undercovered')
        _ref(value['protection_policy_ref'])
        _need(value['protection_policy_ref'] == candidate['protection_policy_ref'], 'protection_policy_mismatch')
        protected = value['protected_parts']
        _need(type(protected) is list and 1 <= len(protected) <= 4
              and all(type(name) is str and name in PARTS[:-1] for name in protected)
              and len(set(protected)) == len(protected), 'explicit_policy_protected_parts_required')
        for name in protected:
            protected_part = value['parts'][name]
            _need(_contains(value['parts']['protection'], protected_part['center_m'],
                            protected_part['half_extents_m']), 'policy_protection_undercovered')
        _need(value['anchor_part'] in ('body', 'head'), 'explicit_observed_anchor_part_required')
    for item, kind, ref, bound in ((value['registration'], 'registration', binding['registration_ref'], 'error_bound_m'),
                                    (value['clock_mapping'], 'clock', binding['clock_mapping_ref'], 'uncertainty_s')):
        _exact(item, 'ref status valid_until ' + bound, kind + '_assessment_required')
        _ref(item['ref'])
        _need(item['ref'] == ref and item['status'] == 'qualified', kind + '_qualification_unavailable')
        _number(item[bound], 0, 10)
        _need(now < _number(item['valid_until']), kind + '_assessment_expired')
    _need(_number(value['observed_at']) <= _number(value['available_at']) <= now
          < _number(value['valid_until']), 'assessment_not_current')
    _need(0 < _number(value['max_anchor_age_s'], 0, 3), 'bounded_anchor_age_required')
    _need(0 < _number(value['max_anchor_error_m'], 0, 10), 'anchor_error_bound_unavailable')
    _exact(value['motion_bound'], 'max_speed_m_s max_accel_m_s2', 'motion_bound_required')
    for field in ('max_speed_m_s', 'max_accel_m_s2'):
        _number(value['motion_bound'][field], 0, 100)


def _anchor(value, binding, assessment, now, *, original_family=None):
    _need(value is not None, 'current_seated_anchor_unavailable')
    _exact(value, 'schema version id scope person_ref method_ref source_generation source_position '
           'frame unit state occupant_count point_m error_bound_m clock observed_at available_at valid_until',
           'anchor_original_contract_required')
    version = binding['version']
    _need(value['schema'] == 'home-desk-seated-anchor-' + str(version) and type(value['version']) is int
          and value['version'] == version, 'anchor_version_required')
    original = binding['anchor_original_ref']
    _need(value['id'] == original['receipt_id'] and digest(value) == original['digest'],
          'anchor_original_mismatch')
    _need(value['scope'] == binding['scope'] and value['person_ref'] == binding['person_ref'],
          'anchor_scope_or_epoch_mismatch')
    _need(value['source_generation'] == binding['source_generation']
          and value['source_position'] == binding['source_position'], 'anchor_not_current_head')
    _need(value['method_ref'] == binding['association_method_ref'], 'anchor_method_mismatch')
    _need(value['frame'] == 'z_up_metric_floor0' and value['unit'] == 'm', 'anchor_frame_required')
    _need(value['state'] == 'seated_unique', 'anchor_applicability_unavailable')
    _need(type(value['occupant_count']) is int and value['occupant_count'] == 1,
          'unique_person_applicability_unavailable')
    _vector(value['point_m']); _number(value['error_bound_m'], 0, 10)
    clock = value['clock']
    _exact(clock, 'status mapping_id mapping_digest source_epoch original_unit original_value uncertainty_s',
           'original_qualified_clock_required')
    _need(clock['status'] == 'qualified'
          and {'id': clock['mapping_id'], 'digest': clock['mapping_digest']} == binding['clock_mapping_ref']
          and clock['source_epoch'] == binding['source_position']['capture_epoch']
          and type(clock['original_value']) is str, 'clock_qualification_unavailable')
    if original_family is None:
        _need(clock['original_unit'] in (('ns',) if version == 1 else ('unix_ns', 'device_ns'))
              and re.fullmatch(r'[0-9]{1,24}', clock['original_value']), 'clock_qualification_unavailable')
    else:
        # An opt-in DERIVED UTC original, never a reinterpreted camera stamp.
        # Live admission must still recompute its full immutable source proof.
        _need(version == 2 and original_family == 'existing-api-bundled-rgb-1'
              and clock['original_unit'] == 'unix_s'
              and re.fullmatch(r'(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,18})?', clock['original_value'])
              and float(Decimal(clock['original_value'])) == value['observed_at'],
              'clock_qualification_unavailable')
    uncertainty = _number(clock['uncertainty_s'], 0, 10)
    _need(uncertainty <= assessment['clock_mapping']['uncertainty_s'], 'clock_bound_exceeded')
    observed, available, until = (_number(value[key]) for key in ('observed_at', 'available_at', 'valid_until'))
    _need(observed == original['observed_at'] and available == original['available_at']
          and clock['mapping_id'] == original['clock_mapping_id'], 'original_anchor_clocks_mismatch')
    _need(assessment['available_at'] <= observed <= available <= now and now + uncertainty < until,
          'anchor_not_current')
    age = now + uncertainty - observed
    _need(age < assessment['max_anchor_age_s'], 'anchor_age_exceeded')
    motion = assessment['motion_bound']
    error = value['error_bound_m'] + assessment['registration']['error_bound_m']
    error += motion['max_speed_m_s'] * age + .5 * motion['max_accel_m_s2'] * age * age
    _need(error <= assessment['max_anchor_error_m'], 'anchor_motion_error_exceeded')
    _need(_contains(assessment['anchor_region'], value['point_m'], error),
          'anchor_outside_assessed_applicability')
    enclosing = (assessment['enclosure'] if version == 1 else assessment['parts'][assessment['anchor_part']])
    _need(_contains(enclosing, value['point_m'], error),
          'observed_anchor_not_enclosed')
    lease_until = min(until - uncertainty, assessment['valid_until'],
                      assessment['registration']['valid_until'], assessment['clock_mapping']['valid_until'],
                      observed + assessment['max_anchor_age_s'] - uncertainty)
    # Speed and acceleration bounds are nonnegative: error grows monotonically.
    # Check the entire exclusive lease conservatively at its upper endpoint;
    # present containment alone cannot justify future applicability.
    horizon_age = lease_until + uncertainty - observed
    horizon_error = value['error_bound_m'] + assessment['registration']['error_bound_m']
    horizon_error += (motion['max_speed_m_s'] * horizon_age
                      + .5 * motion['max_accel_m_s2'] * horizon_age * horizon_age)
    _need(horizon_error <= assessment['max_anchor_error_m'], 'anchor_lease_motion_error_exceeded')
    _need(_contains(assessment['anchor_region'], value['point_m'], horizon_error),
          'anchor_lease_outside_assessed_applicability')
    _need(_contains(enclosing, value['point_m'], horizon_error),
          'anchor_lease_not_enclosed')
    return lease_until


def evaluate_desk_enclosure(candidate, *, binding, assessment=None, anchor=None, now, hazards=(),
                            original_family=None):
    """Consume resolved originals into local proposals; never clear hazards.

    binding is trusted composition input: current heads, exact scope/epochs and
    pre-reviewed assessment pins. Do not deserialize browser declarations into it.
    Assessment authenticity/independent truth and current journal heads must be
    checked by the owning resolver before calling. now is its qualified,
    conservative UTC upper bound. Proposal available_at retains source
    availability; a live publisher must additionally record actual immutable
    first-consumer/production availability and may not backdate publication.
    No such live resolver or publication/schema grant is supplied here.
    """
    result_version = 2 if (type(binding) is dict and binding.get('schema') == 'home-desk-enclosure-binding-2'
                          and type(binding.get('version')) is int and binding['version'] == 2) else 1
    result = {'schema': 'home-desk-enclosure-result-' + str(result_version), 'version': result_version,
        'status': 'unavailable', 'reason': None, 'geometry_proposal': None,
        'association_proposal': None, 'operational_geometry': None,
        'inherited_hazards': None, 'hazard_clearance': None,
        'journal_authority': False, 'engagement_authority': False, 'beam_authority': False,
        'physical_authority': False, 'moving_work': 'unsupported_static_method'}
    try:
        _number(now)
        _need(type(hazards) in (list, tuple) and len(hazards) <= 64, 'bounded_inherited_hazards_required')
        _encoded(hazards)
        result['inherited_hazards'] = deepcopy(list(hazards))
        _encoded([candidate, binding, assessment, anchor, result['inherited_hazards']])
        _binding(binding); _candidate(candidate, binding)
        _assessment(assessment, binding, candidate, now)
        _need(original_family is None or binding['version'] == 2
              and original_family == 'existing-api-bundled-rgb-1', 'unsupported_original_clock_family')
        until = _anchor(anchor, binding, assessment, now, original_family=original_family)
        _need(now < until, 'support_intersection_expired')
        registration_error = assessment['registration']['error_bound_m']
        version = binding['version']
        parts = {name: _inflate(assessment['enclosure'] if version == 1 else assessment['parts'][name],
                                registration_error) for name in PARTS}
        association = {key: deepcopy(binding[key]) for key in
            ('scope', 'person_ref', 'subject_ref', 'device_id', 'workstation_id', 'method_ref',
             'association_method_ref', 'assessment_ref', 'parent_publication_refs', 'anchor_original_ref')}
        association.update(schema='home-desk-association-proposal-' + str(version), version=version,
            observed_at=anchor['observed_at'], available_at=max(anchor['available_at'], assessment['available_at']),
            valid_until=until, source_generation=binding['source_generation'],
            source_position=deepcopy(binding['source_position']), candidate_digest=candidate['digest'])
        association['id'] = 'desk-association:' + digest(association)
        geometry = dict(schema='home-desk-enclosure-proposal-' + str(version), version=version,
            frame='z_up_metric_floor0', unit='m', method_ref=deepcopy(binding['method_ref']),
            work=_inflate(candidate['work_patch'] if version == 1 else assessment['work'], registration_error), parts=parts,
            body_forward=None, head_forward=None, observed_at=anchor['observed_at'],
            available_at=association['available_at'], valid_until=until,
            candidate_digest=candidate['digest'], assessment_ref=deepcopy(binding['assessment_ref']),
            anchor_original_ref=deepcopy(binding['anchor_original_ref']))
        if version == 2:
            geometry.update(protection_policy_ref=deepcopy(assessment['protection_policy_ref']),
                            protected_parts=deepcopy(assessment['protected_parts']))
            result.update(schema='home-desk-enclosure-result-2', version=2)
        result.update(status='eligible_for_admission_review', reason='pinned_assessment_and_current_anchor',
                      geometry_proposal=geometry, association_proposal=association)
    except Refusal as error:
        result['reason'] = str(error)
    return result
