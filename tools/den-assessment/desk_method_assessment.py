"""Independent held boxes -> unsigned review of proposed static desk bounds.

No source, model, journal, person association or qualification is created. The
fixed Guide supplies clocks, a durable recorder and the already verified private
collection loader. Actual COCO17 camera points remain separate from six static
proposed room bounds. Entry clocks never become sensor exposure or clock-error
qualifications. Only aggregate results leave this owner-local module.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import threading

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1] / 'stack/services/perception-lab'
PARTS = ('work', 'body', 'head', 'hand_left', 'hand_right', 'protection')
REQUIREMENTS = ('max_work_center_error_m', 'min_containment_fraction', 'max_undercoverage_fraction',
                'min_complete_protection_fraction', 'min_reference_s', 'max_capture_to_available_s')
# The opt-in Guide recorder imports these exact private-file limits. Its HTTP
# request bound is independent and remains unchanged.
MAX_ACTION_BYTES = 1024**2
MAX_RECORD_BYTES = 8 * 1024**2
MAX_ACTIONS = 256
MAX_COLLECTION_BINDING_BYTES = 128 * 1024
MAX_REPORT_BYTES = 64 * 1024
GUIDE_CLOSE_RESERVE_BYTES = 4096
AUTHORITY = dict(geometry=False, association=False, engagement=False, beam=False, physical=False)
OPERATIONAL_MISSING = ('independent_camera_projection_and_registration_error',
    'qualified_reference_RGB_and_depth_clock_alignment', 'independent_visible_anchor_estimator_error',
    'independent_complete_hidden_configuration_applicability',
    'qualified_exact_capture_person_work_association', 'independent_protection_policy_coverage')
_GUIDE = None
_CONTAINS = None


class DeskAssessmentRefusal(ValueError):
    pass


def need(ok, reason):
    if not ok: raise DeskAssessmentRefusal(reason)


def encoded(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise DeskAssessmentRefusal('finite_bounded_reference_JSON_required') from None


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def exact(value, keys, reason):
    need(type(value) is dict and set(value) == set(keys), reason)


def number(value, low=0, high=1e12):
    need(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
         'finite_independent_value_required')
    return value


def identity(value):
    need(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value) is not None,
         'opaque_reference_identity_required')


def sha(value):
    need(type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None, 'reference_digest_required')


def _math():
    """Reuse fixed existing Guide transforms and conservative scorer math.

    There is no caller-selected import. The Guide imports this extension only
    when its opt-in session is constructed, so the pure definitions can also be
    loaded here without running its CLI/server/camera entry.
    """
    global _GUIDE, _CONTAINS
    if _GUIDE is None:
        spec = importlib.util.spec_from_file_location('_held_desk_reference_math', HERE / 'desk-geometry-guide.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _GUIDE = module
    if _CONTAINS is None:
        if str(LAB) not in sys.path: sys.path.insert(0, str(LAB))
        from live_assessment_scoring import contains
        _CONTAINS = contains
    return _GUIDE, _CONTAINS


def _box(value):
    exact(value, ('kind', 'center_m', 'half_extents_m'), 'independent_AABB_required')
    need(value['kind'] == 'aabb', 'independent_AABB_required')
    for key in ('center_m', 'half_extents_m'):
        need(type(value[key]) is list and len(value[key]) == 3, 'independent_AABB_vector_required')
        for v in value[key]: number(v, -100 if key == 'center_m' else 0, 100)
    need(all(v > 0 for v in value['half_extents_m']), 'positive_independent_AABB_required')


def _candidate(value):
    need(type(value) is dict and value.get('schema') == 'home-desk-geometry-candidate-2'
         and type(value.get('version')) is int and value['version'] == 2
         and value.get('qualified') is False and value.get('status') == 'candidate'
         and value.get('operational_geometry') is None and value.get('person_identity') is None
         and value.get('frame') == 'z_up_metric_floor0' and value.get('unit') == 'm'
         and value.get('authority') == dict(geometry=False, engagement=False, beam=False, physical=False),
         'existing_unqualified_candidate2_required')
    sha(value.get('digest'))
    need(value['digest'] == digest({k: v for k, v in value.items() if k != 'digest'})
         and value.get('survey_sha256') == digest(value.get('original_survey')),
         'candidate_original_digest_mismatch')
    need(len(encoded(value)) <= 128 * 1024, 'candidate_reference_byte_bound')
    guide, _ = _math()
    original = value['original_survey']
    need(original.get('schema') == 'home-desk-geometry-survey-2' and original.get('version') == 2,
         'candidate_original_survey2_required')
    guide.reference_frame(original['references'])
    exact(value['parts'], PARTS[1:], 'five_proposed_part_bounds_required')
    proposed = dict(work=value['work_patch'], **value['parts'])
    for name in PARTS:
        raw = original['work_patch'] if name == 'work' else original['parts'][name]
        _box(proposed[name])
        calculated = guide.outer_box(raw['outer_corners'], original['references'],
            thickness_m=raw.get('thickness_m', 0), require_volume=name != 'work')
        need(calculated == proposed[name], 'proposed_bounds_differ_from_original_measurements')
    return proposed


class HeldDeskAssessment:
    def __init__(self, candidate, *, clock, monotonic, verified_collection_loader, record):
        need(all(callable(x) for x in (clock, monotonic, verified_collection_loader, record)),
             'fixed_reference_ports_required')
        self.candidate = deepcopy(candidate)
        self.proposed = deepcopy(_candidate(self.candidate))
        self.candidate_ref = dict(id='desk-candidate-' + self.candidate['digest'], digest=self.candidate['digest'])
        self.clock, self.monotonic = clock, monotonic
        self.loader, self.record = verified_collection_loader, record
        self.failed, self.sequence, self.recorded_bytes = False, 0, 0
        self.frozen, self.windows, self.active = None, [], None
        self.last_stamp, self.report_value = None, None
        self.lock = threading.RLock()

    def _ready(self):
        need(not self.failed, 'reference_recording_incomplete')
        need(self.report_value is None, 'reference_report_already_final')

    def _stamp(self):
        utc, mono = self.clock(), self.monotonic()
        try:
            number(utc)
            need(type(mono) is int and 0 <= mono <= 2**63 - 1, 'integer_monotonic_ns_required')
            need(self.last_stamp is None or utc >= self.last_stamp['utc_s']
                 and mono >= int(self.last_stamp['monotonic_ns']), 'reference_clock_rollback')
        except Exception:
            self.failed = True
            raise
        return dict(utc_s=utc, monotonic_ns=str(mono))

    def _action(self, kind, body, *, seq=None):
        return dict(schema='home-desk-held-action-1', version=1, seq=self.sequence + 1 if seq is None else seq,
                      kind=kind, candidate_ref=deepcopy(self.candidate_ref), body=deepcopy(body))

    def _retain(self, kind, body):
        action = self._action(kind, body)
        raw = encoded(action)
        need(len(raw) <= MAX_ACTION_BYTES and self.sequence < MAX_ACTIONS
             and self.recorded_bytes + len(raw) + 1 <= MAX_RECORD_BYTES, 'reference_action_byte_bound')
        fingerprint = hashlib.sha256(raw).hexdigest()
        try:
            acknowledgement = self.record(deepcopy(action))
            need(acknowledgement == dict(status='retained', sha256=fingerprint), 'durable_reference_ACK_required')
        except BaseException:
            self.failed = True
            raise DeskAssessmentRefusal('reference_recording_incomplete') from None
        self.sequence += 1
        self.recorded_bytes += len(raw) + 1  # Same single newline as the fixed Guide writer.
        return dict(id='held-action-' + str(self.sequence), digest=fingerprint)

    def _reference_document(self, binding, windows):
        return dict(schema='home-desk-held-references-1', version=1,
            id='held-references-' + digest([self.candidate_ref, self.frozen['receipt']['freeze_ref'], windows]),
            candidate_ref=deepcopy(self.candidate_ref), protocol=deepcopy(self.frozen['protocol']),
            requirements=deepcopy(self.frozen['requirements']), windows=deepcopy(windows),
            collection_binding=deepcopy(binding),
            clock=dict(role='server_entry_not_sensor_exposure', qualification=None, error_bound_s=None),
            authority=deepcopy(AUTHORITY))

    def _reserve_start(self, request, stamp):
        """Refuse new owner work while the prior ended references still fit.

        The exact accumulated original windows are serialized. Bounded future
        End/receipt fields use the longest sequence/clock identities plus 256
        bytes per window for finite-number representation. The future verified
        binding and aggregate have the same closed bounds enforced at report.
        No recorder capacity is increased and no future ACK is manufactured.
        """
        ref = dict(id='held-action-' + str(MAX_ACTIONS), digest='f' * 64)
        latest = dict(utc_s=10**12, monotonic_ns=str(2**63 - 1))
        start_receipt = dict(status='started', window_id=request['id'], started_at=stamp['utc_s'], reference_ref=ref)
        end_receipt = dict(status='ended', window_id=request['id'], started_at=stamp['utc_s'],
                           ended_at=latest['utc_s'], duration_s=300, reference_ref=ref)
        future = dict(id=request['id'], request=request, started=stamp, ended=latest,
                      start_receipt=start_receipt, end_receipt=end_receipt)
        windows = self.windows + [future]
        # None contributes four bytes in the envelope, making the added complete
        # binding/report byte allowances conservative rather than subtractive.
        report_action = self._action('report', dict(reference_document=self._reference_document(None, windows),
            report=None), seq=MAX_ACTIONS)
        report_bound = (len(encoded(report_action)) + MAX_COLLECTION_BINDING_BYTES + MAX_REPORT_BYTES
                        + len(windows) * 256)
        start_bound = len(encoded(self._action('start', dict(**request, started=stamp)))) + 1
        end_bound = len(encoded(self._action('end', dict(window_id=request['id'], started=stamp,
            ended=latest, duration_s=300, source_clock_qualification=None), seq=MAX_ACTIONS))) + 257
        need(report_bound <= MAX_ACTION_BYTES and end_bound <= MAX_ACTION_BYTES
             and self.sequence + 4 <= MAX_ACTIONS
             and self.recorded_bytes + start_bound + end_bound + report_bound + 1
                 + GUIDE_CLOSE_RESERVE_BYTES <= MAX_RECORD_BYTES, 'reserved_reference_report_capacity')

    def freeze(self, protocol, requirements):
        with self.lock:
            need(not self.failed, 'reference_recording_incomplete')
            exact(protocol, ('id', 'reference_method', 'supported_configuration_ids', 'max_windows'), 'closed_reference_protocol_required')
            identity(protocol['id'])
            ids = protocol['supported_configuration_ids']
            need(protocol['reference_method'] in ('tape', 'laser') and type(ids) is list
                 and 1 <= len(ids) <= 32 and len(set(ids)) == len(ids), 'bounded_independent_configurations_required')
            for item in ids: identity(item)
            need(type(protocol['max_windows']) is int and 1 <= protocol['max_windows'] <= 32, 'reference_window_bound')
            exact(requirements, REQUIREMENTS, 'closed_independent_requirements_required')
            for key, value in requirements.items():
                if value is not None: number(value, 0, 1 if 'fraction' in key else 1e6)
            if self.frozen is not None:
                need(protocol == self.frozen['protocol']['body'] and requirements == self.frozen['requirements']['body'],
                     'independent_freeze_identity_changed')
                return deepcopy(self.frozen['receipt'])
            self._ready()
            stamp = self._stamp()
            body = dict(candidate_original=deepcopy(self.candidate),
                protocol=dict(body=deepcopy(protocol), sha256=digest(protocol)),
                requirements=dict(body=deepcopy(requirements), sha256=digest(requirements)),
                frozen_at=stamp, predictions_inspected=False)
            receipt = dict(status='frozen', freeze_ref=self._retain('freeze', body),
                protocol_ref=dict(id=protocol['id'], digest=digest(protocol)),
                requirements_ref=dict(id='held-requirements-' + digest(requirements), digest=digest(requirements)))
            self.frozen = {**body, 'receipt': deepcopy(receipt)}
            self.last_stamp = stamp
            return receipt

    def _references(self, values):
        exact(values, PARTS, 'six_explicit_independent_reference_parts_required')
        guide, _ = _math()
        for name, item in values.items():
            exact(item, ('status', 'shape', 'original_measurements'), 'closed_reference_part_required')
            need(item['status'] in ('available', 'unavailable', 'unknown'), 'explicit_reference_part_status_required')
            if item['status'] != 'available':
                need(item['shape'] is None and item['original_measurements'] is None, 'missing_reference_cannot_supply_shape')
                continue
            _box(item['shape'])
            raw = item['original_measurements']
            exact(raw, ('references', 'outer_corners', 'thickness_m'), 'original_independent_measurements_required')
            need(raw['references'] == self.candidate['original_survey']['references'], 'independent_equipment_or_frame_changed')
            number(raw['thickness_m'], 0, .25)
            need(name == 'work' or raw['thickness_m'] == 0, 'part_measurement_thickness_not_applicable')
            calculated = guide.outer_box(raw['outer_corners'], raw['references'],
                thickness_m=raw['thickness_m'], require_volume=name != 'work')
            need(calculated == item['shape'], 'independent_derived_box_changed')
        return deepcopy(values)

    def start(self, window_id, configuration_id, reference_parts, reference_uncertainty_m, activity):
        with self.lock:
            self._ready()
            need(self.frozen is not None, 'freeze_independent_protocol_first')
            identity(window_id); identity(configuration_id)
            need(configuration_id in self.frozen['protocol']['body']['supported_configuration_ids'],
                 'configuration_outside_frozen_protocol')
            number(reference_uncertainty_m, 0, 2)
            need(activity in ('focused', 'not_engaged', 'unknown'), 'independent_activity_annotation_required')
            parts = self._references(reference_parts)
            request = dict(id=window_id, configuration_id=configuration_id, reference_parts=parts,
                reference_uncertainty_m=reference_uncertainty_m, activity=activity)
            old = next((w for w in self.windows if w['id'] == window_id), None)
            if old is not None:
                need(old['request'] == request, 'held_window_identity_changed')
                return deepcopy(old['start_receipt'])
            need(self.active is None, 'held_reference_intervals_cannot_overlap')
            need(len(self.windows) < self.frozen['protocol']['body']['max_windows'], 'reference_window_budget_exhausted')
            stamp = self._stamp()
            self._reserve_start(request, stamp)
            reference = self._retain('start', dict(**request, started=stamp))
            receipt = dict(status='started', window_id=window_id, started_at=stamp['utc_s'], reference_ref=reference)
            self.windows.append(dict(id=window_id, request=request, started=stamp, ended=None,
                                     start_receipt=deepcopy(receipt), end_receipt=None))
            self.active, self.last_stamp = window_id, stamp
            return receipt

    def end(self, window_id):
        with self.lock:
            self._ready()
            identity(window_id)
            window = next((w for w in self.windows if w['id'] == window_id), None)
            need(window is not None, 'held_reference_window_unavailable')
            if window['ended'] is not None: return deepcopy(window['end_receipt'])
            need(self.active == window_id, 'exact_active_reference_window_required')
            stamp = self._stamp()
            elapsed = (int(stamp['monotonic_ns']) - int(window['started']['monotonic_ns'])) / 1e9
            need(0 < elapsed <= 300, 'bounded_positive_held_interval_required')
            ref = self._retain('end', dict(window_id=window_id, started=deepcopy(window['started']), ended=stamp,
                                           duration_s=elapsed, source_clock_qualification=None))
            receipt = dict(status='ended', window_id=window_id, started_at=window['started']['utc_s'],
                           ended_at=stamp['utc_s'], duration_s=elapsed, reference_ref=ref)
            window.update(ended=stamp, end_receipt=deepcopy(receipt))
            self.active, self.last_stamp = None, stamp
            return receipt

    def _collection(self):
        value = deepcopy(self.loader())
        exact(value, ('binding', 'attempts', 'summary', 'collection_sha256'), 'fixed_verified_collection_result_required')
        binding, attempts, summary = value['binding'], value['attempts'], value['summary']
        need(type(binding) is dict and binding.get('schema') == 'home-lab-assessment-binding-2'
             and binding.get('version') == 2 and type(binding.get('binding')) is dict,
             'verified_original_binding2_required')
        need(binding.get('binding_digest') == digest(binding['binding']), 'verified_collection_binding_changed')
        need(len(encoded(binding)) <= MAX_COLLECTION_BINDING_BYTES, 'verified_collection_binding_byte_bound')
        sha(value['collection_sha256'])
        need(type(summary) is dict and type(summary.get('complete')) is bool
             and summary.get('archive_sha256') == value['collection_sha256'], 'verified_collection_summary_changed')
        need(type(attempts) is list and len(attempts) <= 24, 'bounded_verified_attempts_required')
        ids = set()
        for a in attempts:
            need(type(a) is dict and a.get('schema') == 'home-lab-assessment-attempt-2' and a.get('version') == 2
                 and a.get('binding_digest') == binding['binding_digest'] and a.get('candidate_only') is True
                 and a.get('coordinate_frame') == 'rectified_left' and a.get('unit') == 'm'
                 and a.get('error_bound_m') is None and a.get('operational_geometry') is None
                 and all(a.get(k) is False for k in ('geometry_authority', 'identity_authority', 'protection_authority', 'beam_authority')),
                 'original_camera_candidates_are_not_part_predictions')
            event_id = a.get('source_event_id')
            need(type(event_id) is int and event_id > 0 and event_id not in ids, 'unique_original_attempt_required')
            ids.add(event_id)
            instances = a.get('instances')
            need(type(instances) is list and len(instances) <= 2, 'bounded_original_pose_instances_required')
            for instance in instances:
                joints = instance.get('joints')
                need(type(joints) is list and len(joints) == 17
                     and {j.get('joint_id') for j in joints} == set(range(17)), 'exact_original_COCO17_slots_required')
                for j in joints:
                    need(j.get('status') in ('candidate', 'unavailable'), 'original_candidate_joint_status_required')
                    point = j.get('camera_xyz_m')
                    need((j['status'] != 'candidate' and point is None) or type(point) is list and len(point) == 3
                         and all(type(n) in (int, float) and math.isfinite(n) for n in point), 'finite_nominal_camera_point_required')
        return value

    def report(self):
        with self.lock:
            need(not self.failed, 'reference_recording_incomplete')
            if self.report_value is not None: return deepcopy(self.report_value)
            self._ready()
            need(self.frozen is not None and self.windows, 'ended_independent_references_required')
            need(self.active is None and all(w['ended'] is not None for w in self.windows), 'held_reference_interval_not_ended')
            collection = self._collection()  # First permitted prediction read.
            _, contains = _math()
            bounds = {}
            for name in PARTS:
                row = dict(eligible_reference_windows=0, contained_reference_windows=0, undercovered_reference_windows=0,
                    unavailable_reference_windows=0, unknown_reference_windows=0, eligible_reference_s=0.,
                    contained_reference_s=0., undercovered_reference_s=0., unavailable_reference_s=0., unknown_reference_s=0.)
                if name == 'work':
                    row.update(max_reference_center_distance_m=None,
                        center_distance_basis='static_proposed_box_to_independent_box_not_estimator_error')
                for window in self.windows:
                    duration = window['end_receipt']['duration_s']
                    ref = window['request']['reference_parts'][name]
                    if ref['status'] != 'available':
                        row[ref['status'] + '_reference_windows'] += 1
                        row[ref['status'] + '_reference_s'] += duration
                        continue
                    row['eligible_reference_windows'] += 1; row['eligible_reference_s'] += duration
                    if name == 'work':
                        distance = math.dist(self.proposed[name]['center_m'], ref['shape']['center_m'])
                        old = row['max_reference_center_distance_m']
                        row['max_reference_center_distance_m'] = distance if old is None else max(old, distance)
                    covered = contains(self.proposed[name], ref['shape'], window['request']['reference_uncertainty_m'])
                    prefix = 'contained' if covered else 'undercovered'
                    row[prefix + '_reference_windows'] += 1; row[prefix + '_reference_s'] += duration
                denominator = row['eligible_reference_s']
                row['contained_fraction'] = row['contained_reference_s'] / denominator if denominator else None
                row['undercovered_fraction'] = row['undercovered_reference_s'] / denominator if denominator else None
                bounds[name] = row
            observations = dict(attempts=len(collection['attempts']), refused_attempts=0, no_pose_attempts=0,
                joint_slots={str(i): dict(candidate=0, unavailable=0, missing_prediction_attempts=0) for i in range(17)},
                part_prediction_availability='unavailable_camera_points_are_not_six_part_shapes',
                qualified_complete_protection=None, qualified_error_bound_m=None,
                temporal_alignment='unassessed_not_assigned_to_held_reference_intervals')
            for a in collection['attempts']:
                observations['refused_attempts'] += a.get('status') == 'refused'
                observations['no_pose_attempts'] += not a['instances']
                found = set()
                for instance in a['instances']:
                    for j in instance['joints']:
                        observations['joint_slots'][str(j['joint_id'])][j['status']] += 1
                        if j['status'] == 'candidate': found.add(j['joint_id'])
                for i in range(17):
                    observations['joint_slots'][str(i)]['missing_prediction_attempts'] += i not in found
            req = self.frozen['requirements']['body']
            missing = [key for key, v in req.items() if v is None]
            failed = []
            for name, row in bounds.items():
                if row['eligible_reference_s']:
                    if name == 'work' and req['max_work_center_error_m'] is not None and row['max_reference_center_distance_m'] > req['max_work_center_error_m']:
                        failed.append('work_proposed_center_error')
                    if req['min_containment_fraction'] is not None and row['contained_fraction'] < req['min_containment_fraction']:
                        failed.append(name + '_proposed_bound_containment')
                    if req['max_undercoverage_fraction'] is not None and row['undercovered_fraction'] > req['max_undercoverage_fraction']:
                        failed.append(name + '_proposed_bound_undercoverage')
                    if req['min_reference_s'] is not None and row['eligible_reference_s'] < req['min_reference_s']:
                        failed.append(name + '_independent_reference_duration')
            observed_configs = {w['request']['configuration_id'] for w in self.windows}
            missing_configs = [c for c in self.frozen['protocol']['body']['supported_configuration_ids'] if c not in observed_configs]
            incomplete_refs = [p for p, r in bounds.items() if r['unavailable_reference_windows'] or r['unknown_reference_windows']]
            decision = ('block' if missing or failed or missing_configs or not collection['summary']['complete'] else
                        'narrow' if incomplete_refs else 'proceed')
            references = self._reference_document(collection['binding'], self.windows)
            ref = dict(id=references['id'], digest=digest(references))
            result = dict(schema='home-desk-method-review-report-1', version=1, status='review_report',
                reference_document_ref=ref, collection_ref=dict(sha256=collection['collection_sha256'],
                    binding_digest=collection['binding']['binding_digest']), proposal_decision=decision,
                proposal_scope='unsigned_static_bounds_review_only_no_product_scope_change',
                proposed_bounds=bounds, candidate_observations=observations,
                missing_requirements=missing, failed_requirements=failed, unmeasured_configurations=missing_configs,
                incomplete_reference_parts=incomplete_refs, collection_complete=collection['summary']['complete'],
                unevaluated_requirements=['min_complete_protection_fraction', 'max_capture_to_available_s'],
                clock=deepcopy(references['clock']), operational_qualification=dict(decision='block', missing=list(OPERATIONAL_MISSING)),
                qualified=False, operational_geometry=None, authority=deepcopy(AUTHORITY),
                limitations=['held_entry_duration_is_not_qualified_source_service', 'six_static_proposed_bounds_are_not_observed_anatomy',
                    'camera_points_are_not_independent_corroboration_or_part_shapes', 'review_does_not_issue_assessment2'])
            need(len(encoded(result)) <= MAX_REPORT_BYTES, 'reference_aggregate_report_byte_bound')
            self._retain('report', dict(reference_document=references, report=result))
            self.report_value = deepcopy(result)
            return result


class EvaluatedDeskAssessment(HeldDeskAssessment):
    """Opt-in evaluated successor; the historical held-review mode is unchanged.

    The fixed resolver is a server composition port. Its pin is supplied before
    predictions are read, and authenticating its independent measurement claims
    remains the installer's responsibility. No browser action chooses that port.
    """
    def __init__(self, candidate, *, clock, monotonic, verified_collection_loader, record,
                 qualification_resolver, expected_originals_sha256, reference_boot_id):
        super().__init__(candidate, clock=clock, monotonic=monotonic,
            verified_collection_loader=verified_collection_loader, record=record)
        need(callable(qualification_resolver) and callable(reference_boot_id), 'fixed_evaluation_ports_required')
        if expected_originals_sha256 is not None: sha(expected_originals_sha256)
        self.qualification_resolver, self.expected_originals_sha256 = qualification_resolver, expected_originals_sha256
        self.reference_boot_id = reference_boot_id
        self.independent_originals = self.commissioning = self.export_receipt = None

    def _action(self, kind, body, *, seq=None):
        value = super()._action(kind, body, seq=seq)
        value.update(schema='home-desk-held-action-2', version=2)
        return value

    def _stamp(self):
        value = super()._stamp()
        try:
            boot = self.reference_boot_id()
            need(isinstance(boot, str) and 1 <= len(boot) <= 128, 'reference_boot_identity_unavailable')
            if self.last_stamp is not None:
                need(boot == self.last_stamp['boot_id'], 'reference_boot_changed')
        except BaseException:
            self.failed = True
            raise
        value['boot_id'] = boot
        return value

    def _retain(self, kind, body):
        if kind != 'freeze': return super()._retain(kind, body)
        # This occurs before HeldDeskAssessment promotes its frozen state.
        try:
            originals = deepcopy(self.qualification_resolver())
            need(originals is None or type(originals) is dict and len(encoded(originals)) <= 128 * 1024,
                 'bounded_independent_original_bundle_required')
            from desk_method_export import SELECTOR
            augmented = dict(**body, selector=deepcopy(SELECTOR), independent_originals=originals,
                expected_originals_sha256=self.expected_originals_sha256,
                resolver_truth_authentication='external_installer_responsibility')
            receipt = super()._retain(kind, augmented)
        except BaseException:
            self.failed = True
            raise
        self.independent_originals = originals
        return receipt

    def _reference_document(self, binding, windows):
        value = super()._reference_document(binding, windows)
        value.update(schema='home-desk-held-references-2', version=2,
            independent_originals_ref=None if self.independent_originals is None else dict(
                sha256=digest(self.independent_originals), expected_sha256=self.expected_originals_sha256),
            clock=dict(role='server_entry_with_separately_resolved_alignment', qualification=None))
        return value

    def _reserve_start(self, request, stamp):
        super()._reserve_start(request, stamp)
        # Same limits as report: reserve the complete fixed input and nullable
        # export before asking the owner to hold another reference interval.
        future = dict(id=request['id'], request=request, started=stamp,
            ended=dict(utc_s=10**12,monotonic_ns=str(2**63-1),boot_id='x'*128),
            start_receipt=dict(id='x'*128,digest='f'*64),end_receipt=dict(duration_s=300,reference_ref='f'*128))
        action = self._action('report', dict(reference_document=self._reference_document(None,self.windows+[future]),
            independent_originals=self.independent_originals, report=None, commissioning=None), seq=MAX_ACTIONS)
        bound = len(encoded(action)) + MAX_COLLECTION_BINDING_BYTES + MAX_REPORT_BYTES + 256 * 1024 + 1024 * (len(self.windows)+1)
        need(bound <= MAX_ACTION_BYTES and self.sequence+5 <= MAX_ACTIONS
            and self.recorded_bytes+bound+256*1024+GUIDE_CLOSE_RESERVE_BYTES+4096 <= MAX_RECORD_BYTES,
            'reserved_evaluated_report_and_export_capacity')

    def start(self, window_id, configuration_id, reference_parts, reference_uncertainty_m, activity, selected_anchor):
        with self.lock:
            self._ready(); need(self.frozen is not None, 'freeze_independent_protocol_first')
            identity(window_id); identity(configuration_id)
            need(configuration_id in self.frozen['protocol']['body']['supported_configuration_ids'], 'configuration_outside_frozen_protocol')
            number(reference_uncertainty_m,0,2)
            need(activity in ('focused','not_engaged','unknown'), 'independent_activity_annotation_required')
            exact(selected_anchor,('status','original_measurement'), 'raw_selected_anchor_reference_required')
            status=selected_anchor['status'];need(status in ('available','unavailable','unknown'),'selected_anchor_reference_status_required')
            if status=='available':
                guide,_=_math()
                point,axes=guide.room_point(selected_anchor['original_measurement'],self.candidate['original_survey']['references'])
            else:
                need(selected_anchor['original_measurement'] is None,'missing_anchor_has_no_measurement');point=axes=None
            selected=dict(**deepcopy(selected_anchor),point_m=point,error_axes_m=axes)
            request=dict(id=window_id,configuration_id=configuration_id,reference_parts=self._references(reference_parts),
                reference_uncertainty_m=reference_uncertainty_m,activity=activity,selected_anchor=selected)
            old=next((w for w in self.windows if w['id']==window_id),None)
            if old is not None:
                need(old['request']==request,'held_window_identity_changed');return deepcopy(old['start_receipt'])
            need(self.active is None,'held_reference_intervals_cannot_overlap')
            need(len(self.windows)<self.frozen['protocol']['body']['max_windows'],'reference_window_budget_exhausted')
            stamp=self._stamp();self._reserve_start(request,stamp)
            ref=self._retain('start',dict(**request,started=stamp))
            receipt=dict(status='started',window_id=window_id,started_at=stamp['utc_s'],reference_ref=ref)
            self.windows.append(dict(id=window_id,request=request,started=stamp,ended=None,start_receipt=deepcopy(receipt),end_receipt=None))
            self.active,self.last_stamp=window_id,stamp
            return receipt

    def report(self):
        with self.lock:
            need(not self.failed,'reference_recording_incomplete')
            if self.report_value is not None:return deepcopy(self.report_value)
            self._ready();need(self.frozen is not None and self.windows,'ended_independent_references_required')
            need(self.active is None and all(w['ended'] is not None for w in self.windows),'held_reference_interval_not_ended')
            from desk_method_export import validate_collection,evaluate_method
            collection=validate_collection(deepcopy(self.loader()))
            need(len(encoded(collection['binding']))<=MAX_COLLECTION_BINDING_BYTES,'verified_collection_binding_byte_bound')
            references=self._reference_document(collection['binding'],self.windows)
            references['reported']=self._stamp()
            value=evaluate_method(self.candidate,references,collection,originals=self.independent_originals,
                expected_originals_sha256=self.expected_originals_sha256,now=references['reported']['utc_s'])
            result=value['report']
            need(len(encoded(result))<=MAX_REPORT_BYTES,'reference_aggregate_report_byte_bound')
            need(value['commissioning'] is None or len(encoded(value['commissioning']))<=256*1024,'commissioning_export_byte_bound')
            self._retain('report',dict(reference_document=references,independent_originals=self.independent_originals,
                report=result,commissioning=value['commissioning']))
            self.commissioning=deepcopy(value['commissioning']);self.report_value=deepcopy(result)
            return result

    def export(self, writer):
        with self.lock:
            need(not self.failed and self.report_value is not None and self.commissioning is not None,
                'qualified_compatible_export_unavailable')
            if self.export_receipt is not None:return deepcopy(self.export_receipt)
            need(callable(writer),'fixed_private_export_writer_required')
            try:
                ack=writer(deepcopy(self.commissioning))
                need(ack==dict(status='retained',sha256=digest(self.commissioning)),'durable_export_ACK_required')
                ref=self._retain('export',dict(commissioning_sha256=ack['sha256'],private_file='commissioning.json'))
            except BaseException:
                self.failed=True;raise DeskAssessmentRefusal('commissioning_export_incomplete') from None
            self.export_receipt=dict(status='export_saved',sha256=ack['sha256'],reference_ref=ref,
                operational_geometry=None,authority=deepcopy(AUTHORITY))
            return deepcopy(self.export_receipt)


class CausalEvaluatedDeskAssessment(EvaluatedDeskAssessment):
    """Live held successor: static qualification first, exact leaves at report.

    EvaluatedDeskAssessment remains a retrospective control. Only this class is
    exposed by the new Guide mode. The same fixed resolver owns both methods;
    its alignment method receives no numeric model predictions.
    """
    def __init__(self,candidate,*,clock,monotonic,verified_collection_loader,record,
                 qualification_resolver,expected_qualification_sha256,reference_boot_id):
        need(callable(getattr(qualification_resolver,'qualification',None))
            and callable(getattr(qualification_resolver,'align',None)),'fixed_two_phase_original_resolver_required')
        if expected_qualification_sha256 is not None:sha(expected_qualification_sha256)
        super().__init__(candidate,clock=clock,monotonic=monotonic,verified_collection_loader=verified_collection_loader,
            record=record,qualification_resolver=qualification_resolver.qualification,expected_originals_sha256=None,
            reference_boot_id=reference_boot_id)
        self.resolver=qualification_resolver;self.expected_qualification_sha256=expected_qualification_sha256

    def _action(self,kind,body,*,seq=None):
        value=super()._action(kind,body,seq=seq);value.update(schema='home-desk-held-action-3',version=3);return value

    def _retain(self,kind,body):
        if kind!='freeze':return HeldDeskAssessment._retain(self,kind,body)
        from desk_method_export import validate_qualification,SELECTOR
        try:
            q=validate_qualification(deepcopy(self.resolver.qualification()))
            if q is not None:
                binding=q['measurement_binding'] if q['version'] in (2,3) else q['coverage']
                need((binding is None or binding['available_at']<=body['frozen_at']['utc_s'])
                    and q['context']['anchor_original_ref']['available_at']<=body['frozen_at']['utc_s'],
                    'qualification_contains_not_yet_available_original')
            augmented=dict(**body,selector=deepcopy(SELECTOR),qualification=q,
                expected_qualification_sha256=self.expected_qualification_sha256,
                exposure_alignment_resolved=False,resolver_truth_authentication='external_installer_responsibility')
            ref=HeldDeskAssessment._retain(self,kind,augmented)
        except BaseException:self.failed=True;raise
        self.independent_originals=q
        return ref

    def _reference_document(self,binding,windows):
        value=HeldDeskAssessment._reference_document(self,binding,windows)
        value.update(schema='home-desk-held-references-3',version=3,qualification_ref=None if self.independent_originals is None else dict(
            id=self.independent_originals['id'],digest=digest(self.independent_originals)),
            clock=dict(role='server_entry_with_separately_resolved_alignment',qualification=None))
        return value

    def _reserve_start(self,request,stamp):
        HeldDeskAssessment._reserve_start(self,request,stamp)
        ref=dict(id='held-action-'+str(MAX_ACTIONS),digest='f'*64)
        latest=dict(utc_s=10**12,monotonic_ns=str(2**63-1),boot_id='x'*128)
        future=dict(id=request['id'],request=request,started=stamp,ended=latest,
            start_receipt=dict(status='started',window_id=request['id'],started_at=stamp['utc_s'],reference_ref=ref),
            end_receipt=dict(status='ended',window_id=request['id'],started_at=stamp['utc_s'],ended_at=latest['utc_s'],duration_s=300,reference_ref=ref))
        windows=self.windows+[future]
        report_action=self._action('report',dict(reference_document=self._reference_document(None,windows),
            qualification=self.independent_originals,alignment_originals=None,report=None,commissioning=None),seq=MAX_ACTIONS)
        report_bound=len(encoded(report_action))+MAX_COLLECTION_BINDING_BYTES+MAX_REPORT_BYTES+64*1024+256*1024+len(windows)*1024
        alignment_bound=128*1024+4096  # <=64 KiB request + <=64 KiB exact leaves.
        start_bound=len(encoded(self._action('start',dict(**request,started=stamp))))+1
        end_bound=len(encoded(self._action('end',dict(window_id=request['id'],started=stamp,ended=latest,
            duration_s=300,source_clock_qualification=None),seq=MAX_ACTIONS)))+1024
        need(report_bound<=MAX_ACTION_BYTES and end_bound<=MAX_ACTION_BYTES and self.sequence+6<=MAX_ACTIONS
            and self.recorded_bytes+start_bound+end_bound+alignment_bound+report_bound+256*1024
                +4096+GUIDE_CLOSE_RESERVE_BYTES<=MAX_RECORD_BYTES,
            'reserved_causal_alignment_capacity')

    def report(self):
        with self.lock:
            need(not self.failed,'reference_recording_incomplete')
            if self.report_value is not None:return deepcopy(self.report_value)
            self._ready();need(self.frozen is not None and self.windows,'ended_independent_references_required')
            need(self.active is None and all(w['ended'] is not None for w in self.windows),'held_reference_interval_not_ended')
            from desk_method_export import validate_collection,validate_qualification,alignment_request,validate_alignment,evaluate_resolved_method
            try:
                q=validate_qualification(deepcopy(self.resolver.qualification()))
                need(q==self.independent_originals,'frozen_method_originals_changed')
                loaded=deepcopy(self.loader())
                bundled_collection=loaded if q is not None and q['version']==3 else None
                collection=validate_collection(loaded['collection'] if bundled_collection is not None else loaded)
                need(len(encoded(collection['binding']))<=MAX_COLLECTION_BINDING_BYTES,'verified_collection_binding_byte_bound')
                request=alignment_request(q,collection) if q is not None else None
                resolved=deepcopy(self.resolver.align(deepcopy(request))) if q is not None else None
                need(validate_qualification(deepcopy(self.resolver.qualification()))==q,'frozen_method_originals_changed')
                stamp=self._stamp();resolved=validate_alignment(q,request,resolved,now=stamp['utc_s'])
                # Exact leaves are durably ACKed before they influence a score.
                resolution_ref=self._retain('alignment',dict(qualification_ref=request['qualification_ref'] if request else None,
                    request=request,resolved=resolved,received=stamp))
                references=self._reference_document(collection['binding'],self.windows)
                references.update(reported=stamp,alignment_ref=resolution_ref)
                value=evaluate_resolved_method(self.candidate,references,collection,qualification=q,alignment=resolved,
                    expected_qualification_sha256=self.expected_qualification_sha256,now=stamp['utc_s'],
                    bundled_collection=bundled_collection)
                result=value['report'];need(len(encoded(result))<=MAX_REPORT_BYTES,'reference_aggregate_report_byte_bound')
                need(value['commissioning'] is None or len(encoded(value['commissioning']))<=256*1024,'commissioning_export_byte_bound')
                self._retain('report',dict(reference_document=references,qualification=q,alignment_originals=resolved,
                    report=result,commissioning=value['commissioning']))
            except BaseException:
                self.failed=True;raise
            self.commissioning=deepcopy(value['commissioning']);self.report_value=deepcopy(result)
            return result
