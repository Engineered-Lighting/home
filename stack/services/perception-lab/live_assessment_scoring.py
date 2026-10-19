"""Owner-local independent held-pose scoring; standard library, no model calls.

Reference annotations never enter inference or operational geometry. Candidate
coverage and actual supplied-part availability are deliberately different results.
Intervals split at original result availability/expiry and reference boundaries;
missing predictions remain in eligible-reference denominators.
"""
from itertools import product
import math
import re

PARTS = ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection')
GROUPS = {'task': ('work',), 'body': ('body',), 'head': ('head',),
          'hands': ('hand_left', 'hand_right'), 'protection': ('protection',)}
REQUIREMENTS = ('max_work_center_error_m', 'min_containment_fraction', 'max_undercoverage_fraction',
                'min_complete_protection_fraction', 'min_reference_s', 'max_capture_to_available_s')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def vector(value):
    return isinstance(value, list) and len(value) == 3 and all(finite(x) for x in value)


def primitives(shape):
    if not isinstance(shape, dict):
        raise ValueError('explicit reference/prediction shape required')
    if shape.get('kind') in ('opaque_primitives', 'protected_primitives'):
        items = shape.get('items')
        if not isinstance(items, list) or not 1 <= len(items) <= 12:
            raise ValueError('bounded nonempty reference primitives required')
        if any(x.get('kind') not in ('aabb', 'capsule') for x in items if isinstance(x, dict)):
            raise ValueError('nested reference primitives refused')
        return [p for item in items for p in primitives(item)]
    if shape.get('kind') == 'aabb':
        if (not vector(shape.get('center_m')) or not vector(shape.get('half_extents_m'))
                or any(x <= 0 for x in shape['half_extents_m'])):
            raise ValueError('positive finite reference box required')
    elif shape.get('kind') == 'capsule':
        if (not vector(shape.get('a_m')) or not vector(shape.get('b_m'))
                or not finite(shape.get('radius_m')) or shape['radius_m'] <= 0):
            raise ValueError('positive finite reference capsule required')
    else:
        raise ValueError('unsupported reference shape')
    return [shape]


def outer_box(shape, margin=0):
    if shape['kind'] == 'aabb':
        return ([c - h - margin for c, h in zip(shape['center_m'], shape['half_extents_m'])],
                [c + h + margin for c, h in zip(shape['center_m'], shape['half_extents_m'])])
    return ([min(a, b) - shape['radius_m'] - margin for a, b in zip(shape['a_m'], shape['b_m'])],
            [max(a, b) + shape['radius_m'] + margin for a, b in zip(shape['a_m'], shape['b_m'])])


def point_inside(point, shape):
    if shape['kind'] == 'aabb':
        return all(abs(x - c) <= h + 1e-12 for x, c, h in zip(point, shape['center_m'], shape['half_extents_m']))
    a, b = shape['a_m'], shape['b_m']
    axis = [y - x for x, y in zip(a, b)]
    length = sum(x * x for x in axis)
    along = max(0, min(1, sum((x - y) * z for x, y, z in zip(point, a, axis)) / length)) if length else 0
    return math.dist(point, [x + along * y for x, y in zip(a, axis)]) <= shape['radius_m'] + 1e-12


def contains(predicted, reference, reference_margin):
    """Sufficient conservative containment, never a center/IoU substitute.

    Each independent reference outer AABB must fit a single convex predicted
    primitive. This may refuse a covered union seam; it cannot call that seam
    covered from checking a few points in different disjoint primitives.
    """
    candidates = primitives(predicted)
    for item in primitives(reference):
        low, high = outer_box(item, reference_margin)
        corners = list(product(*zip(low, high)))
        if not any(all(point_inside(point, candidate) for point in corners) for candidate in candidates):
            return False
    return True


def summary(values):
    total = sum(weight for _, weight in values)
    if not values or total <= 0:
        return {'samples': 0, 'eligible_s': 0, 'p50': None, 'p95': None, 'max': None}
    ordered = sorted(values)
    def quantile(q):
        seen = 0
        for value, weight in ordered:
            seen += weight
            if seen >= total * q:
                return value
        return ordered[-1][0]
    return {'samples': len(values), 'eligible_s': total, 'p50': quantile(.5), 'p95': quantile(.95),
            'max': ordered[-1][0]}


def record_summary(values, total, *, denominator='records'):
    result = summary([(value, 1) for value in values])
    result.pop('eligible_s')
    return {**result, 'status': 'available' if values else 'unavailable',
            'denominator_unit': denominator, 'eligible_' + denominator: len(values),
            'unavailable_' + denominator: total - len(values), 'total_' + denominator: total}


def envelope(shape):
    boxes = [outer_box(item) for item in primitives(shape)]
    low = [min(box[0][axis] for box in boxes) for axis in range(3)]
    high = [max(box[1][axis] for box in boxes) for axis in range(3)]
    return low, high, [(a + b) / 2 for a, b in zip(low, high)]


def pipeline_timings(records, qualified, facts, semantic_checks):
    """Subtract only explicitly recorded UTC boundary clocks, never SDK mono clocks."""
    spans = {'capture': ('capture_started_at', 'capture_available_at'),
             'observation_to_capture_received': ('observed_at', 'capture_received_at'),
             'observation_to_capture_available': ('observed_at', 'capture_available_at'),
             'capture_publication_to_commit': ('capture_available_at', 'capture_committed_at'),
             'detector_inference': ('detector_started_at', 'detector_available_at'),
             'pose_inference': ('pose_started_at', 'pose_available_at'),
             'spatial_preparation': ('pose_available_at', 'spatial_available_at'),
             'observation_to_candidate_available': ('observed_at', 'available_at'),
             'observation_to_spatial_publication': ('observed_at', 'spatial_available_at'),
             'spatial_publication_to_commit': ('spatial_available_at', 'spatial_committed_at'),
             'observation_to_spatial_commit': ('observed_at', 'spatial_committed_at'),
             'observation_to_object_interaction_publication': ('observed_at', 'interaction_available_at'),
             'object_interaction_publication_to_commit': ('interaction_available_at', 'interaction_committed_at'),
             'observation_to_object_interaction_commit': ('observed_at', 'interaction_committed_at'),
             'observation_to_la_available': ('observed_at', 'la_available_at'),
             'la_available_to_admission': ('la_available_at', 'la_checked_at'),
             'observation_to_worker_commit': ('observed_at', 'worker_committed_at'),
             'observation_to_display': ('observed_at', 'displayed_at')}
    values = {name: [] for name in spans}
    for row in records:
        extra = row.get('pipeline_times', {})
        allowed = {'capture_started_at', 'capture_received_at', 'capture_available_at',
                   'capture_committed_at', 'detector_started_at', 'pose_started_at'}
        if not isinstance(extra, dict) or set(extra) - allowed or any(not finite(v) for v in extra.values()):
            raise ValueError('closed finite actual UTC boundary clocks required')
        times = {key: row[key] for key in ('observed_at', 'available_at', 'detector_available_at', 'pose_available_at')
                 if key in row}
        times.update(extra)
        publication = qualified.get(row['capture_id'])
        if publication:
            times.update(spatial_available_at=publication['available_at'],
                         spatial_committed_at=publication['receipt']['committed_at'])
        interaction = next((fact for fact in facts if fact['capture_id'] == row['capture_id']
                            and fact['predicate'] == 'object_interaction'), None)
        if interaction:
            times.update(interaction_available_at=interaction['available_at'],
                         interaction_committed_at=interaction['receipt']['committed_at'])
        admitted = [check for check in semantic_checks if check['capture_id'] == row['capture_id'] and check['admitted']]
        if admitted:
            check = min(admitted, key=lambda check: check['checked_at'])
            times.update(la_available_at=check['available_at'], la_checked_at=check['checked_at'])
        for name, (before, after) in spans.items():
            if before not in times or after not in times:
                continue
            if not finite(times[before]) or not finite(times[after]) or times[after] < times[before]:
                raise ValueError('ordered finite actual UTC boundary clocks required')
            values[name].append(times[after] - times[before])
    return {name: {**record_summary(items, len(records)), 'clock': 'utc_epoch_seconds',
                   'unavailable_reason': 'stage_not_recorded' if len(items) < len(records) else None}
            for name, items in values.items()}


def score_assessment(measurements, references):
    if (measurements.get('schema') != 'home-live-assessment-measurements-1' or measurements.get('version') != 1
            or references.get('schema') != 'home-live-assessment-references-1' or references.get('version') != 1
            or measurements.get('binding') != references.get('binding')
            or references.get('truth_role') != 'independent_evaluation_reference'
            or references.get('frame') != 'z_up_metric_floor0' or references.get('unit') != 'm'):
        raise ValueError('independent same-version same-scope metric references required')
    binding = measurements.get('binding')
    if (not isinstance(binding, dict) or set(binding) != {'session_ref', 'source_id', 'configuration_digest'}
            or not isinstance(binding['session_ref'], dict)
            or set(binding['session_ref']) != {'site_id', 'room_id', 'session_id', 'domain_epoch'}
            or not all(isinstance(v, str) and ID.fullmatch(v) for v in binding['session_ref'].values())
            or not isinstance(binding['source_id'], str) or not ID.fullmatch(binding['source_id'])
            or not isinstance(binding['configuration_digest'], str) or not SHA.fullmatch(binding['configuration_digest'])):
        raise ValueError('closed opaque assessment identity required')
    protocol = references.get('reference_protocol_ref')
    if (not isinstance(protocol, dict) or set(protocol) != {'id', 'digest'}
            or not isinstance(protocol['id'], str) or not ID.fullmatch(protocol['id'])
            or not isinstance(protocol['digest'], str) or len(protocol['digest']) != 64
            or any(c not in '0123456789abcdef' for c in protocol['digest'])):
        raise ValueError('independent reference protocol pin required')
    windows, records = references.get('windows'), measurements.get('records')
    if not isinstance(windows, list) or not 1 <= len(windows) <= 128 or not isinstance(records, list) or len(records) > 24:
        raise ValueError('bounded reference windows and candidates required')
    ids, capture_ids = set(), set()
    for row in records:
        if (not isinstance(row, dict) or row.get('schema') != 'home-live-assessment-candidate-1'
                or row.get('version') != 1 or row.get('candidate_only') is not True
                or row.get('truth_role') != 'runtime_model_estimate' or not isinstance(row.get('id'), str)
                or row['id'] in ids or not all(finite(row.get(key)) for key in ('observed_at', 'available_at', 'valid_until'))
                or not isinstance(row.get('capture_id'), str) or row['capture_id'] in capture_ids
                or row['observed_at'] > row['available_at'] or row['valid_until'] <= row['observed_at']):
            raise ValueError('immutable finite candidate identities and original clocks required')
        ids.add(row['id'])
        capture_ids.add(row['capture_id'])
        if not isinstance(row.get('parts'), dict):
            raise ValueError('explicit candidate parts required')
        for part in row['parts'].values():
            if part.get('status') == 'candidate':
                primitives(part.get('shape'))
    windows = sorted(windows, key=lambda row: row.get('start_at', -math.inf))
    prior_end, window_ids = -math.inf, set()
    for window in windows:
        if (not isinstance(window, dict) or not isinstance(window.get('id'), str) or window['id'] in window_ids
                or not all(finite(window.get(k)) for k in ('start_at', 'end_at', 'reference_uncertainty_m'))
                or window['start_at'] < prior_end or window['end_at'] <= window['start_at']
                or window['reference_uncertainty_m'] < 0 or window.get('activity') not in ('focused', 'not_engaged', 'unknown')
                or not isinstance(window.get('parts'), dict)):
            raise ValueError('disjoint finite held-pose reference intervals required')
        prior_end = window['end_at']
        window_ids.add(window['id'])
        for part in window['parts'].values():
            if part.get('status') == 'available':
                primitives(part.get('shape'))
            elif part.get('status') not in ('unavailable', 'unknown'):
                raise ValueError('reference availability must be explicit')
    requirements = references.get('requirements', {})
    if not isinstance(requirements, dict) or set(requirements) - set(REQUIREMENTS):
        raise ValueError('closed owner requirements required')
    if any(value is not None and (not finite(value) or value < 0
           or ('fraction' in key and value > 1)) for key, value in requirements.items()):
        raise ValueError('finite nonnegative owner requirements required')
    groups = {name: dict(eligible_s=0., contained_s=0., undercovered_s=0., unavailable_reference_s=0.,
                         missing_prediction_s=0.) for name in GROUPS}
    duration = sum(w['end_at'] - w['start_at'] for w in windows)
    errors, latency = [], []
    part_values = {part: {'center': [], 'boundary': [], 'reference_eligible_s': 0.,
                         'missing_prediction_s': 0., 'unavailable_reference_s': 0.} for part in PARTS}
    def service():
        return dict(candidate_available_s=0., qualified_available_s=0., duration_s=duration,
                    candidate_longest_gap_s=0., qualified_longest_gap_s=0.,
                    candidate_longest_service_s=0., qualified_longest_service_s=0.)
    complete, complete_service = service(), service()
    publications = measurements.get('publications', [])
    if (not isinstance(publications, list) or len(publications) > 24
            or any(not isinstance(row, dict) or row.get('capture_id') not in capture_ids for row in publications)
            or len({row['capture_id'] for row in publications}) != len(publications)):
        raise ValueError('bounded original spatial publications required')
    qualified = {row['capture_id']: row for row in publications}
    barriers = measurements.get('barriers', [])
    if not isinstance(barriers, list) or len(barriers) > 25 or any(
            not isinstance(row, dict) or type(row.get('source_generation')) is not int
            or not finite(row.get('receipt', {}).get('committed_at')) for row in barriers):
        raise ValueError('bounded original source controls required')
    publication_cuts = []
    for publication in qualified.values():
        if (not finite(publication.get('available_at')) or not finite(publication.get('valid_until'))
                or not finite(publication.get('receipt', {}).get('committed_at'))
                or publication['receipt']['committed_at'] < publication['available_at']
                or not isinstance(publication.get('parts'), dict)):
            raise ValueError('qualified availability needs actual publication clocks and part horizons')
        publication_cuts.extend([publication['available_at'], publication['valid_until'], publication['receipt']['committed_at']])
        for part in publication['parts'].values():
            if not isinstance(part, dict) or not finite(part.get('effective_valid_until')):
                raise ValueError('qualified availability needs exclusive part horizons')
            publication_cuts.append(part['effective_valid_until'])
    semantic_checks, facts = measurements.get('semantic_checks', []), measurements.get('interaction_publications', [])
    la_configured = measurements.get('optional_la_configured', False)
    if (type(la_configured) is not bool or not isinstance(semantic_checks, list) or len(semantic_checks) > 72
            or not la_configured and semantic_checks):
        raise ValueError('bounded actual optional source checks required')
    for row in semantic_checks:
        if (not isinstance(row, dict) or row.get('capture_id') not in capture_ids or not finite(row.get('checked_at'))
                or row.get('status') not in {'fresh_proposal', 'conflict', 'unavailable'}
                or type(row.get('exact_frame')) is not bool or type(row.get('admitted')) is not bool
                or row['admitted'] and (not row['exact_frame'] or row['status'] != 'fresh_proposal'
                    or not all(finite(row.get(k)) for k in ('observed_at', 'available_at', 'valid_until'))
                    or not row['observed_at'] <= row['available_at'] <= row['checked_at'] < row['valid_until'])):
            raise ValueError('exact-frame semantic admission needs original finite clocks')
    if not isinstance(facts, list) or len(facts) > 120:
        raise ValueError('bounded actual interaction publications required')
    fact_ids = set()
    for row in facts:
        identity = (row.get('capture_id'), row.get('predicate')) if isinstance(row, dict) else None
        if (not isinstance(row, dict) or row.get('capture_id') not in capture_ids or identity in fact_ids
                or row.get('predicate') not in {'person_presence', 'material_state', 'hand_material_relation',
                                               'object_interaction', 'activity_refinement'}
                or not all(finite(row.get(k)) for k in ('available_at', 'effective_valid_until'))
                or not finite(row.get('receipt', {}).get('committed_at'))
                or row['receipt']['committed_at'] < row['available_at']):
            raise ValueError('original finite interaction publication clocks required')
        fact_ids.add(identity)
    checked_ids = {row['capture_id'] for row in semantic_checks}
    exact_ids = {row['capture_id'] for row in semantic_checks if row['exact_frame']}
    admitted_ids = {row['capture_id'] for row in semantic_checks if row['admitted']}
    la = dict(status='available' if exact_ids else 'unavailable',
              reason=None if exact_ids else 'not_configured' if not la_configured else
                     'no_exact_frame_receipt' if semantic_checks else 'interaction_not_reached',
              check_measurements_status='available' if semantic_checks else 'unavailable',
              configured=la_configured, eligible_capture_count=len(records), checked_capture_count=len(checked_ids),
              not_checked_capture_count=len(records) - len(checked_ids), exact_frame_capture_count=len(exact_ids),
              admitted_capture_count=len(admitted_ids),
              published_refinement_capture_count=len({row['capture_id'] for row in facts
                  if row['predicate'] == 'activity_refinement' and row.get('status') == 'supplied'}),
              exact_frame_yield_fraction=len(exact_ids) / len(records) if la_configured and records else None,
              admitted_yield_fraction=len(admitted_ids) / len(records) if la_configured and records else None,
              duration_s=duration, candidate_eligible_s=0., admitted_s=0., unavailable_s=0., not_measured_s=0.,
              longest_unavailable_gap_s=0.,
              check_counts={status: sum(row['status'] == status for row in semantic_checks)
                            for status in ('fresh_proposal', 'conflict', 'unavailable')})
    focused_windows = sum(window['activity'] == 'focused' for window in windows)
    first_support = {'candidate_holding': [], 'published_object_interaction': []}
    records_by_capture = {row['capture_id']: row for row in records}
    semantic_expiry_cuts = [check['valid_until'] - records_by_capture[check['capture_id']].get('clock', {}).get('uncertainty_s', 0)
                            for check in semantic_checks if check['admitted']]
    interaction = dict(positive_reference_s=0., confuser_reference_s=0., supported_positive_s=0., false_support_s=0.,
                       unknown_s=0., unavailable_candidate_s=0.)
    longest = {part: 0. for part in PARTS}
    for window in windows:
        start, end = window['start_at'], window['end_at']
        # An unmeasured interval between held poses cannot join gaps or service runs.
        runs = {part: 0. for part in PARTS}
        service_runs = {kind: {key: 0. for key in ('candidate_gap', 'qualified_gap', 'candidate_service', 'qualified_service')}
                        for kind in ('protection', 'service')}
        la_gap, support_at = 0., {}
        cuts = sorted({start, end, *(r[k] for r in records for k in ('available_at', 'valid_until') if start < r[k] < end),
                       *(t for t in publication_cuts if start < t < end)})
        cuts = sorted(set(cuts) | {r['receipt']['committed_at'] for r in barriers if start < r['receipt']['committed_at'] < end})
        cuts = sorted(set(cuts) | {r[k] for r in semantic_checks for k in ('checked_at', 'valid_until')
                                  if finite(r.get(k)) and start < r[k] < end}
                      | {t for t in semantic_expiry_cuts if start < t < end}
                      | {r[k] for r in facts for k in ('available_at', 'effective_valid_until') if start < r[k] < end}
                      | {r['receipt']['committed_at'] for r in facts if start < r['receipt']['committed_at'] < end})
        for a, b in zip(cuts, cuts[1:]):
            dt = b - a
            # Later arrival of an older frame cannot supersede newer observations.
            eligible = [r for r in records if r['available_at'] <= a]
            row = max(eligible, key=lambda r: (r['observed_at'], r['available_at'])) if eligible else None
            if row is not None and a >= row['valid_until']:
                row = None
            source_current = row is not None and not any(control['receipt']['committed_at'] <= a
                    and control['source_generation'] > row.get('source_generation', 0) for control in barriers)
            parts = row['parts'] if row else {}
            available = {part: parts.get(part, {}).get('status') == 'candidate' for part in PARTS}
            for part in PARTS:
                runs[part] = 0 if available[part] else runs[part] + dt
                longest[part] = max(longest[part], runs[part])
            protected_parts = ('body', 'head', 'hand_left', 'hand_right', 'protection')
            publication = qualified.get(row['capture_id'], {}) if row else {}
            for kind, target, required in (('protection', complete, protected_parts), ('service', complete_service, PARTS)):
                candidate = all(available[p] for p in required)
                supplied = bool(source_current and publication.get('status') == 'supplied'
                        and max(publication['available_at'], publication['receipt']['committed_at']) <= a < publication['valid_until']
                        and all(publication.get('parts', {}).get(p, {}).get('status') == 'supplied'
                                and a < publication['parts'][p]['effective_valid_until'] for p in required))
                for label, usable in (('candidate', candidate), ('qualified', supplied)):
                    if usable:
                        target[label + '_available_s'] += dt
                    for state, active in (('gap', not usable), ('service', usable)):
                        key = label + '_' + state
                        service_runs[kind][key] = service_runs[kind][key] + dt if active else 0.
                        longest_key = label + '_longest_' + state + '_s'
                        target[longest_key] = max(target[longest_key], service_runs[kind][key])
            for part, values in part_values.items():
                if window['parts'].get(part, {}).get('status') != 'available':
                    values['unavailable_reference_s'] += dt
                else:
                    values['reference_eligible_s'] += dt
                    if not available[part]:
                        values['missing_prediction_s'] += dt
                    else:
                        low, high, center = envelope(parts[part]['shape'])
                        ref_low, ref_high, ref_center = envelope(window['parts'][part]['shape'])
                        values['center'].append((math.dist(center, ref_center), dt))
                        values['boundary'].append((max(abs(x - y) for x, y in zip(low + high, ref_low + ref_high)), dt))
            for name, selected in GROUPS.items():
                group = groups[name]
                if not all(window['parts'].get(p, {}).get('status') == 'available' for p in selected):
                    group['unavailable_reference_s'] += dt
                    continue
                group['eligible_s'] += dt
                if not all(available[p] for p in selected):
                    group['missing_prediction_s'] += dt
                    group['undercovered_s'] += dt
                    continue
                covered = all(contains(parts[p]['shape'], window['parts'][p]['shape'], window['reference_uncertainty_m']) for p in selected)
                group['contained_s' if covered else 'undercovered_s'] += dt
            if available['work'] and window['parts'].get('work', {}).get('status') == 'available':
                predicted = parts['work']['shape']
                reference = window['parts']['work']['shape']
                if predicted['kind'] == reference['kind'] == 'aabb':
                    errors.append((math.dist(predicted['center_m'], reference['center_m']), dt))
            if row:
                latency.append((row['available_at'] - row['observed_at'], dt))
            supported = row is not None and row.get('interaction') is not None and row['interaction'].get('status') == 'supported'
            if supported:
                support_at.setdefault('candidate_holding', a)
            if source_current and any(fact['capture_id'] == row['capture_id']
                    and fact['predicate'] == 'object_interaction' and fact.get('status') == 'supplied' and fact.get('value') is True
                    and max(fact['available_at'], fact['receipt']['committed_at']) <= a < fact['effective_valid_until'] for fact in facts):
                support_at.setdefault('published_object_interaction', a)
            checks = [check for check in semantic_checks if row is not None and check['capture_id'] == row['capture_id']
                      and check['checked_at'] <= a]
            check = max(checks, key=lambda check: check['checked_at']) if checks else None
            la_current = bool(source_current and check and check['admitted']
                    and a + row.get('clock', {}).get('uncertainty_s', 0) < check['valid_until'])
            la['candidate_eligible_s'] += dt if row is not None else 0
            la['admitted_s' if la_current else 'unavailable_s'] += dt
            la['not_measured_s'] += dt if check is None else 0
            la_gap = 0. if la_current else la_gap + dt
            la['longest_unavailable_gap_s'] = max(la['longest_unavailable_gap_s'], la_gap)
            if row is None or row.get('interaction') is None or row['interaction'].get('status') in {'unobservable', 'conflicted'}:
                interaction['unavailable_candidate_s'] += dt
            if window['activity'] == 'focused':
                interaction['positive_reference_s'] += dt
                interaction['supported_positive_s'] += dt if supported else 0
            elif window['activity'] == 'not_engaged':
                interaction['confuser_reference_s'] += dt
                interaction['false_support_s'] += dt if supported else 0
            else:
                interaction['unknown_s'] += dt
        if window['activity'] == 'focused':
            for name, at in support_at.items():
                first_support[name].append(at - start)
    per_part = {part: {'envelope_center': summary(values['center']),
                      'envelope_boundary_max': summary(values['boundary']),
                      **{key: values[key] for key in ('reference_eligible_s', 'missing_prediction_s', 'unavailable_reference_s')}}
                for part, values in part_values.items()}
    timings = pipeline_timings(records, qualified, facts, semantic_checks)
    candidate_leases = [max(0., row['valid_until'] - row['available_at']) for row in records]
    protection_leases, service_leases = [], []
    for capture_id, publication in qualified.items():
        row = records_by_capture[capture_id]
        if publication.get('status') != 'supplied':
            continue
        committed = max(publication['available_at'], publication['receipt']['committed_at'])
        barrier_expiry = min((control['receipt']['committed_at'] for control in barriers
                              if control['source_generation'] > row.get('source_generation', 0)), default=math.inf)
        for required, output in ((('body', 'head', 'hand_left', 'hand_right', 'protection'), protection_leases),
                                 (PARTS, service_leases)):
            if all(publication['parts'].get(part, {}).get('status') == 'supplied' for part in required):
                until = min(row['valid_until'], publication['valid_until'], barrier_expiry,
                            *(publication['parts'][part]['effective_valid_until'] for part in required))
                output.append(max(0., until - committed))
    leases = {'candidate_at_available': record_summary(candidate_leases, len(records)),
              'complete_protection_at_commit': record_summary(protection_leases, len(records)),
              'complete_service_at_commit': record_summary(service_leases, len(records))}
    support = {name: {**record_summary(values, focused_windows, denominator='windows'),
                      'origin': 'independent_focused_window_start', 'support': 'per_observation_without_dwell'}
               for name, values in first_support.items()}
    support['reducer_engagement'] = {**record_summary([], focused_windows, denominator='windows'),
                                     'reason': 'reducer_state_not_recorded'}
    missing = [key for key in REQUIREMENTS if requirements.get(key) is None]
    failures = []
    def fail(name, condition):
        if condition:
            failures.append(name)
    error_summary, latency_summary = summary(errors), summary(latency)
    if not missing:
        fail('work_error', error_summary['p95'] is None or error_summary['p95'] > requirements['max_work_center_error_m'])
        for name, group in groups.items():
            eligible = group['eligible_s']
            fail(name + '_reference', eligible < requirements['min_reference_s'] or eligible <= 0)
            if eligible > 0:
                fail(name + '_containment', group['contained_s'] / eligible < requirements['min_containment_fraction'])
                fail(name + '_undercoverage', group['undercovered_s'] / eligible > requirements['max_undercoverage_fraction'])
        fail('complete_protection', complete['qualified_available_s'] / duration < requirements['min_complete_protection_fraction'])
        fail('latency', latency_summary['p95'] is None or latency_summary['p95'] > requirements['max_capture_to_available_s'])
    if not measurements.get('complete'):
        failures.append('measurement_incomplete')
    blockers = ['producer_feasibility_not_qualified_by_held_pose_report', 'reducer_support_measurements_unavailable']
    for key, reason in (('observation_to_worker_commit', 'worker_commit_measurements_unavailable'),
                        ('observation_to_display', 'display_measurements_unavailable')):
        if timings[key]['unavailable_records'] or not records:
            blockers.append(reason)
    if missing or failures:
        blockers.append('held_pose_assessment_blocked')
    return {'schema': 'home-live-assessment-report-1', 'version': 1,
            'binding': measurements['binding'], 'reference_protocol_ref': protocol,
            'duration_s': duration, 'candidate_records': len(records), 'containment': groups,
            'attempt_counts': {status: sum(row.get('status') == status for row in measurements.get('attempts', []))
                               for status in ('observed', 'failed', 'blocked', 'stopped')},
            'work_center_error_m': error_summary, 'observation_to_available_s': latency_summary,
            'part_errors_m': per_part, 'pipeline_timings_s': timings, 'usable_lease_s': leases,
            'complete_protection': complete, 'complete_service': complete_service, 'longest_candidate_gap_s': longest,
            'time_to_support_s': support, 'optional_la': la,
            'interaction_candidates': interaction, 'missing_requirements': missing, 'failed_requirements': failures,
            'decision': 'block' if missing or failures else 'proceed',
            'decision_scope': 'held_pose_estimator_assessment_only',
            'producer_feasibility': {'decision': 'block', 'decision_scope': 'producer_qualification_only',
                'blockers': ['held_pose_report_does_not_qualify_continuous_supported_conditions',
                             'frozen_producer_geometry_confuser_and_optional_semantic_gates_require_separate_qualification']},
            'overall_readiness': {'decision': 'block', 'decision_scope': 'end_to_end_experience_only',
                                  'blockers': blockers},
            'operational_geometry': False, 'physical_authority': False,
            'limitations': ['candidate_shapes_are_not_qualified_geometry', 'held_poses_do_not_qualify_moving_intervals',
                            'reference_outer_boxes_use_conservative_single_primitive_containment',
                            'per_part_errors_compare_outer_envelopes_not_corresponding_surface_points',
                            'pipeline_timings_measure_recorded_boundary_calls_not_native_compute_only',
                            'interaction_candidates_do_not_establish_temporal_engagement']}
