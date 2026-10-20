"""Bounded owner-local desk survey guide. No cameras, models or asset readers.

Output is a candidate survey/fit, never supplied geometry or commissioning.
Run: python -B tools/den-assessment/desk-geometry-guide.py --output ABSOLUTE.json
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
import operator  # stdlib before the runtime's operator.py directory is appended.
from pathlib import Path
import re
import secrets
import stat
import sys
import threading
import time
import zipfile

LAB = Path(__file__).resolve().parents[2] / 'stack/services/perception-lab'
MAX_BYTES = 128 * 1024
DISTINCT_PARTS = ('body', 'head', 'hand_left', 'hand_right', 'protection')
DISTINCT_METHOD = 'desk-distinct-parts-envelope-v2'


class SurveyRefusal(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise SurveyRefusal(reason)


def exact(value, fields):
    return isinstance(value, dict) and set(value) == set(fields.split())


def number(value, minimum=-100, maximum=100):
    require(type(value) in (int, float) and math.isfinite(value)
            and minimum <= value <= maximum, 'finite_bounded_measurement_required')
    return float(value)


def vector(value, size):
    require(isinstance(value, list) and len(value) == size, 'measurement_vector_required')
    return [number(item) for item in value]


def text(value):
    require(isinstance(value, str) and 0 < len(value) <= 200, 'named_reference_required')
    return value


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def parse(raw):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES, 'survey_input_bound')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_survey_field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(SurveyRefusal('finite_measurements_required')))


def reference_frame(value):
    require(exact(value, 'frame unit equipment_ref binding floor walls reference_uncertainty_m')
        and value['frame'] == 'z_up_metric_floor0' and value['unit'] == 'm', 'registered_room_reference_required')
    pin = value['equipment_ref']
    require(exact(pin, 'revision digest file_sha256') and type(pin['revision']) is int
        and pin['revision'] >= 0, 'equipment_reference_required')
    for sha in (pin['digest'], pin['file_sha256']):
        require(isinstance(sha, str) and len(sha) == 64
            and all(c in '0123456789abcdef' for c in sha), 'reference_digest_required')
    require(exact(value['binding'], 'content_revision registration_version manifest_digest frame_digest canonical_collision_digest'),
        'exact_pack_binding_required')
    text(value['binding']['content_revision'])
    require(type(value['binding']['registration_version']) is int
        and value['binding']['registration_version'] >= 0, 'registration_revision_required')
    for key in ('manifest_digest', 'frame_digest', 'canonical_collision_digest'):
        sha = value['binding'][key]
        require(isinstance(sha, str) and len(sha) == 64
            and all(c in '0123456789abcdef' for c in sha), 'reference_digest_required')
    floor, walls = value['floor'], value['walls']
    require(exact(floor, 'id normal offset_m') and isinstance(walls, list) and len(walls) == 2,
        'two_named_walls_and_floor_required')
    normal = vector(floor['normal'], 3)
    require(abs(sum(v*v for v in normal)-1) < 1e-6 and normal[2] > .5, 'upward_unit_floor_required')
    number(floor['offset_m']); text(floor['id'])
    for wall in walls:
        require(exact(wall, 'id label normal_xy offset_m'), 'named_wall_plane_required')
        text(wall['id']); text(wall['label']); number(wall['offset_m'])
        axis = vector(wall['normal_xy'], 2)
        require(abs(sum(v*v for v in axis)-1) < 1e-6, 'unit_wall_normal_required')
    a, b = (wall['normal_xy'] for wall in walls)
    require(walls[0]['id'] != walls[1]['id'] and abs(a[0]*b[1]-a[1]*b[0]) > 1e-6,
        'distinct_nonparallel_wall_references_required')
    number(value['reference_uncertainty_m'], 0, 2)
    return deepcopy(value)


def room_point(value, refs):
    require(exact(value, 'wall_distances_m height_above_floor_m uncertainty_m'), 'measured_room_point_required')
    distances = vector(value['wall_distances_m'], 2)
    require(min(distances) >= 0, 'nonnegative_wall_distances_required')
    height = number(value['height_above_floor_m'], 0, 20)
    uncertainty = number(value['uncertainty_m'], 0, 2) + refs['reference_uncertainty_m']
    a, b = refs['walls']; ax, ay = a['normal_xy']; bx, by = b['normal_xy']
    det = ax*by-ay*bx
    u, v = a['offset_m']+distances[0], b['offset_m']+distances[1]
    x, y = (u*by-ay*v)/det, (ax*v-u*bx)/det
    nx, ny, nz = refs['floor']['normal']
    z = (refs['floor']['offset_m']-nx*x-ny*y)/nz + height
    ex, ey = (abs(by)+abs(ay))*uncertainty/abs(det), (abs(bx)+abs(ax))*uncertainty/abs(det)
    error = [ex, ey, uncertainty+(abs(nx)*ex+abs(ny)*ey)/nz]
    point = [number(item) for item in (x, y, z)]
    require(max(error) <= 10, 'ill_conditioned_measurement_refused')
    return point, error


def outer_box(measurements, refs, *, thickness_m=0, require_volume=False):
    require(isinstance(measurements, list) and 2 <= len(measurements) <= 8, 'opposite_outer_bounds_required')
    measured = [room_point(row, refs) for row in measurements]
    points = [row[0] for row in measured]
    dimensions = [max(p[i] for p in points)-min(p[i] for p in points) for i in range(3)]
    require(all(d > 1e-6 for d in dimensions[:2]) and (not require_volume or dimensions[2] > 1e-6),
        'degenerate_measured_bounds')
    low = [min(p[i]-e[i] for p, e in measured) for i in range(3)]
    high = [max(p[i]+e[i] for p, e in measured) for i in range(3)]
    low[2] -= thickness_m
    require(all(high[i] > low[i] for i in range(3)), 'positive_outer_volume_required')
    return {'kind': 'aabb', 'center_m': [(a+b)/2 for a, b in zip(low, high)],
            'half_extents_m': [(b-a)/2 for a, b in zip(low, high)]}


def camera_fit(value, refs, *, original_resolver=None, allow_acquired=False):
    if value is None:
        return {'status': 'unavailable', 'reason': 'visible_3d_correspondences_not_entered'}
    acquired = isinstance(value, dict) and value.get('schema') == 'home-desk-camera-targets-1'
    if acquired:
        require(allow_acquired and exact(value, 'schema version source_frame points')
            and type(value['version']) is int and value['version'] == 1, 'captured_targets_require_distinct_survey')
    require((acquired or exact(value, 'source_frame points')) and value['source_frame'] == 'rectified_left',
        'rectified_left_3d_points_required')
    rows = value['points']
    require(isinstance(rows, list) and 5 <= len(rows) <= 16, 'bounded_fit_and_check_points_required')
    fit_source, fit_target, checks, uncertainty, originals = [], [], [], [], []
    resolved_pixels = set()
    for row in rows:
        keys = 'role camera_xyz_m camera_uncertainty_m room_measurement'
        require(exact(row, keys + (' camera_original_ref rgb_pixel' if acquired else ''))
            and row['role'] in ('fit', 'check'), 'independent_target_role_required')
        source = vector(row['camera_xyz_m'], 3)
        require(source[2] > 0, 'target_in_front_of_camera_required')
        target, error = room_point(row['room_measurement'], refs)
        if acquired:
            ref = row['camera_original_ref']
            require(exact(ref, 'store_id receipt_id digest') and ref['store_id'] == 'desk-camera-originals'
                and isinstance(ref['receipt_id'], str) and re.fullmatch(r'point-[a-f0-9]{64}', ref['receipt_id'])
                and isinstance(ref['digest'], str) and re.fullmatch(r'[a-f0-9]{64}', ref['digest'])
                and ref not in originals, 'unique_camera_point_original_required')
            pixel = row['rgb_pixel']
            require(isinstance(pixel, list) and len(pixel) == 2
                and all(type(n) is int for n in pixel) and 0 <= pixel[0] < 640 and 0 <= pixel[1] < 360,
                'exact_captured_pixel_required')
            require(row['camera_uncertainty_m'] is None, 'independent_camera_error_remains_unknown')
            if original_resolver is not None:
                original = original_resolver(deepcopy(ref))
                require(isinstance(original, dict) and original.get('schema') == 'home-desk-camera-point-original-1'
                    and original.get('version') == 1 and original.get('status') == 'candidate'
                    and original.get('qualified') is False and original.get('operational_geometry') is None
                    and original.get('source_frame') == 'rectified_left'
                    and original.get('camera_xyz_m') == row['camera_xyz_m']
                    and original.get('camera_uncertainty_m') is None and original.get('rgb_pixel') == pixel
                    and original.get('independent_reference') == {'role': row['role'], 'room_measurement': row['room_measurement']},
                    'captured_point_or_locked_reference_mismatch')
                snapshot_ref = original.get('snapshot_ref')
                require(exact(snapshot_ref, 'store_id receipt_id digest')
                    and all(isinstance(snapshot_ref[key], str) for key in snapshot_ref), 'resolved_snapshot_reference_required')
                identity = (snapshot_ref['store_id'], snapshot_ref['receipt_id'], snapshot_ref['digest'], *pixel)
                require(identity not in resolved_pixels, 'fit_check_original_pixel_reused')
                resolved_pixels.add(identity)
            originals.append(deepcopy(ref))
        uncertainty.append({'camera_m': None if acquired else number(row['camera_uncertainty_m'], 0, 2),
                            'room_axes_m': error})
        if row['role'] == 'fit':
            fit_source.append(source); fit_target.append(target)
        else:
            checks.append((source, target))
    require(len(fit_source) >= 4 and checks, 'four_fit_and_independent_check_required')
    if str(LAB) not in sys.path:
        sys.path.insert(0, str(LAB))
    import numpy as np
    from spatial_validation import fit_rigid
    require(np.linalg.matrix_rank(np.asarray(fit_source)-np.mean(fit_source, axis=0), tol=1e-6) >= 2,
        'degenerate_camera_correspondences')
    fit = fit_rigid(fit_source, fit_target)
    matrix = np.asarray(fit['matrix'])
    errors = [float(np.linalg.norm((matrix @ [*source, 1])[:3]-target)) for source, target in checks]
    result = {'status': 'candidate', 'source_frame': 'rectified_left', 'target_frame': refs['frame'],
        'room_from_rectified_left': fit['matrix'], 'fit': fit, 'independent_check_errors_m': errors,
        'point_uncertainties': uncertainty, 'qualified': False,
        'error_bound_qualification': 'fit_residuals_are_not_operational_error_bounds'}
    if acquired:
        result['fit'] = {key: item for key, item in fit.items() if key != 'error_bound_m'}
        result['fit']['residual_candidate_bound_m'] = fit.get('error_bound_m')
        result.update(camera_uncertainty_m=None, original_point_refs=originals,
            original_resolution='resolved_this_guide' if original_resolver is not None else 'fixed_original_resolver_required',
            error_bound_qualification='independent_camera_error_unavailable')
    return result


def _protection_reference(value):
    require(exact(value, 'policy_ref source_file_sha256'), 'protection_policy_import_required')
    ref = value['policy_ref']
    require(exact(ref, 'id digest') and isinstance(ref['id'], str)
        and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', ref['id']),
        'protection_policy_reference_required')
    for sha in (ref['digest'], value['source_file_sha256']):
        require(isinstance(sha, str) and len(sha) == 64
            and all(c in '0123456789abcdef' for c in sha), 'protection_policy_digest_required')
    return deepcopy(ref)


def _prepare_distinct_candidate(value, *, now, camera_original_resolver=None):
    require(exact(value, 'schema version references work_patch parts protection_policy camera survey_method')
        and value['schema'] == 'home-desk-geometry-survey-2' and type(value['version']) is int
        and value['version'] == 2, 'distinct_desk_survey_version_required')
    require(value['survey_method'] in ('tape', 'laser'), 'independent_measured_survey_required')
    refs = reference_frame(value['references'])
    patch = value['work_patch']
    require(exact(patch, 'outer_corners thickness_m'), 'measured_work_patch_required')
    work = outer_box(patch['outer_corners'], refs,
        thickness_m=number(patch['thickness_m'], .001, .25))
    require(exact(value['parts'], ' '.join(DISTINCT_PARTS)), 'separately_measured_parts_required')
    parts = {}
    for name in DISTINCT_PARTS:
        part = value['parts'][name]
        require(exact(part, 'outer_corners coverage_declaration')
            and part['coverage_declaration'] == 'independently_measured_' + name + '_supported_configurations',
            'explicit_independent_part_coverage_required')
        parts[name] = outer_box(part['outer_corners'], refs, require_volume=True)
    protection_ref = _protection_reference(value['protection_policy'])
    result = {'schema': 'home-desk-geometry-candidate-2', 'version': 2,
        'status': 'candidate', 'qualified': False, 'owner_local_only': True,
        'proposed_method': DISTINCT_METHOD, 'frame': refs['frame'], 'unit': 'm',
        'created_at': number(now(), 0, 10**12), 'clock_basis': 'local_preparation_not_observation_time',
        'survey_sha256': digest(value), 'original_survey': deepcopy(value),
        'work_patch': work, 'parts': parts, 'protection_policy_ref': protection_ref,
        'envelope_basis': 'separate_independent_measurements_not_observed_anatomy',
        'camera_registration': camera_fit(value['camera'], refs,
            original_resolver=camera_original_resolver, allow_acquired=True),
        'operational_geometry': None, 'person_identity': None,
        'authority': {'geometry': False, 'engagement': False, 'beam': False, 'physical': False},
        'required_review': ['survey_truth_and_frame_binding', 'independent_per_part_containment_and_applicability',
            'protection_policy_and_covered_parts', 'participant_scope_and_continuity',
            'other_occupants_and_unknown_space', 'camera_projection_clock_and_mount',
            'room_collision_and_fixture_model', 'desk_use_semantics', 'fresh_evidence_leases_and_method_grants']}
    result['digest'] = digest(result)
    return result


def prepare_candidate(value, *, now=time.time, camera_original_resolver=None):
    if isinstance(value, dict) and value.get('schema') == 'home-desk-geometry-survey-2':
        return _prepare_distinct_candidate(value, now=now, camera_original_resolver=camera_original_resolver)
    require(exact(value, 'schema version references work_patch seated_envelope camera survey_method')
        and value['schema'] == 'home-desk-geometry-survey-1' and type(value['version']) is int
        and value['version'] == 1, 'desk_survey_version_required')
    require(value['survey_method'] in ('tape', 'laser'), 'independent_measured_survey_required')
    refs = reference_frame(value['references'])
    patch, envelope = value['work_patch'], value['seated_envelope']
    require(exact(patch, 'outer_corners thickness_m') and exact(envelope, 'outer_corners coverage_declaration')
        and envelope['coverage_declaration'] == 'body_head_both_hands_supported_seated_reach',
        'explicit_seated_enclosure_declaration_required')
    work = outer_box(patch['outer_corners'], refs, thickness_m=number(patch['thickness_m'], .001, .25))
    seated = outer_box(envelope['outer_corners'], refs, require_volume=True)
    result = {'schema': 'home-desk-geometry-candidate-1', 'version': 1,
        'status': 'candidate', 'qualified': False, 'owner_local_only': True,
        'proposed_method': 'desk-static-envelope-v1', 'frame': refs['frame'], 'unit': 'm',
        'created_at': number(now(), 0, 10**12), 'clock_basis': 'local_preparation_not_observation_time',
        'survey_sha256': digest(value), 'original_survey': deepcopy(value),
        'work_patch': work, 'seated_person_envelope': seated,
        'envelope_basis': 'owner_declared_independent_measurements_not_observed_anatomy',
        'camera_registration': camera_fit(value['camera'], refs),
        'operational_geometry': None, 'person_identity': None,
        'authority': {'geometry': False, 'engagement': False, 'beam': False, 'physical': False},
        'required_review': ['survey_truth_and_frame_binding', 'independent_containment_and_applicability',
            'participant_scope_and_continuity', 'other_occupants_and_unknown_space',
            'camera_projection_clock_and_mount', 'room_collision_and_fixture_model',
            'desk_use_semantics', 'fresh_evidence_leases_and_method_grants']}
    result['digest'] = digest(result)
    return result


def save_candidate(output, candidate):
    path = Path(output)
    require(path.is_absolute() and path.suffix == '.json' and path.parent.is_dir()
        and not path.parent.is_symlink(), 'existing_owner_local_output_directory_required')
    raw = encoded(candidate) + b'\n'
    require(len(raw) <= MAX_BYTES, 'candidate_output_bound')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def reference_utc():
    return float(Decimal(time.time_ns()) / Decimal(1_000_000_000))


def sync_reference_directory(path):
    """Fail closed where the owner filesystem cannot acknowledge its namespace."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


class ReferenceActions:
    """Fixed private bounded original actions; no caller-selected file names."""
    def __init__(self, directory):
        from desk_method_assessment import MAX_ACTION_BYTES, MAX_RECORD_BYTES, MAX_ACTIONS
        self.action_limit, self.byte_limit, self.count_limit = MAX_ACTION_BYTES, MAX_RECORD_BYTES, MAX_ACTIONS
        self.directory = Path(directory)
        require(self.directory.is_absolute() and self.directory.parent.is_dir()
            and not self.directory.parent.is_symlink(), 'private_reference_parent_required')
        self.directory.mkdir(mode=0o700, exist_ok=False)
        sync_reference_directory(self.directory.parent)
        self.stream = None; self.failed = False; self.count = self.used = 0
        try:
            descriptor = os.open(self.directory / 'actions.jsonl', os.O_CREAT | os.O_EXCL | os.O_WRONLY
                | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            self.stream = os.fdopen(descriptor, 'wb')
            self.stream.flush(); os.fsync(self.stream.fileno()); sync_reference_directory(self.directory)
        except BaseException:
            if self.stream is not None: self.stream.close()
            raise

    def record(self, action):
        try:
            raw = encoded(action)
            require(not self.failed and self.stream is not None and not self.stream.closed
                and len(raw) <= self.action_limit and self.count < self.count_limit
                and self.used + len(raw) + 1 <= self.byte_limit,
                'reference_action_capacity_or_recording_unavailable')
            require(self.stream.write(raw + b'\n') == len(raw) + 1, 'reference_original_write_unconfirmed')
            self.stream.flush(); os.fsync(self.stream.fileno())
            self.count += 1; self.used += len(raw) + 1
            return {'status': 'retained', 'sha256': hashlib.sha256(raw).hexdigest()}
        except BaseException:
            self.failed = True
            raise

    def close(self, reason):
        if self.stream is None or self.stream.closed: return
        try:
            if not self.failed:
                self.record({'kind': 'guide_closed', 'data': {'reason': reason,
                    'unfinished_interval_is_not_ended': True, 'operational_qualification': 'block'}})
        finally: self.stream.close()

    def export(self, value):
        """One fixed private file; a partial write never receives a success ACK."""
        raw = encoded(value)
        require(not self.failed and len(raw) <= 256 * 1024 and self.used+len(raw)+4096 <= self.byte_limit,
            'bounded_private_export_required')
        descriptor = os.open(self.directory / 'commissioning.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY
            | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                require(stream.write(raw) == len(raw), 'complete_private_export_write_required')
                stream.flush(); os.fsync(stream.fileno())
            sync_reference_directory(self.directory)
        except BaseException:
            self.failed = True
            raise
        return dict(status='retained', sha256=hashlib.sha256(raw).hexdigest())


class Guide(HTTPServer):
    def __init__(self, output, *, seconds=300, camera_targets=None,
                 assessment_candidate=None, assessment_collection_loader=None, assessment_directory=None,
                 assessment_clock=reference_utc, assessment_monotonic=time.monotonic_ns,
                 evaluate_desk_method=False, evaluation_resolver=None, evaluation_qualification_sha256=None,
                 evaluation_boot_id=None, reference_capture_directory=None):
        require(type(seconds) is int and 1 <= seconds <= 300, 'bounded_guide_lifetime_required')
        self.output, self.nonce = Path(output), secrets.token_hex(24)
        require(self.output.is_absolute() and self.output.parent.is_dir()
            and not self.output.exists(), 'fresh_absolute_candidate_output_required')
        self.deadline, self.stopped, self.saved = time.monotonic()+seconds, threading.Event(), False
        require(camera_targets is None or all(callable(getattr(camera_targets, name, None))
                for name in ('snapshot', 'point', 'resolve_point')), 'trusted_camera_target_port_required')
        self.camera_targets = camera_targets
        self.assessment = self.reference_actions = None
        require(type(evaluate_desk_method) is bool, 'explicit_evaluation_mode_required')
        self.reference_capture = None
        self.reference_only = reference_capture_directory is not None
        self.reference_clock, self.reference_monotonic = assessment_clock, assessment_monotonic
        if self.reference_only:
            require(all(value is None for value in (camera_targets, assessment_candidate,
                assessment_collection_loader, assessment_directory, evaluation_resolver,
                evaluation_qualification_sha256, evaluation_boot_id)) and not evaluate_desk_method,
                'reference_capture_requires_separate_mode')
            require(callable(assessment_clock) and callable(assessment_monotonic),
                'fixed_reference_capture_clocks_required')
            self.reference_actions = ReferenceActions(reference_capture_directory)
        self.evaluation_mode = evaluate_desk_method
        self.assessment_candidate = None; self.reference_windows = 0
        if assessment_candidate is not None or assessment_collection_loader is not None or assessment_directory is not None:
            require(assessment_candidate is not None and callable(assessment_collection_loader)
                and assessment_directory is not None and callable(assessment_clock)
                and callable(assessment_monotonic), 'complete_fixed_reference_session_required')
            from desk_method_assessment import HeldDeskAssessment
            self.reference_actions = ReferenceActions(assessment_directory)
            try:
                cls, extra = HeldDeskAssessment, {}
                if evaluate_desk_method:
                    from desk_method_assessment import CausalEvaluatedDeskAssessment
                    cls = CausalEvaluatedDeskAssessment
                    extra = dict(qualification_resolver=evaluation_resolver,
                        expected_qualification_sha256=evaluation_qualification_sha256, reference_boot_id=evaluation_boot_id)
                self.assessment = cls(assessment_candidate, clock=assessment_clock,
                    monotonic=assessment_monotonic, verified_collection_loader=assessment_collection_loader,
                    record=self.reference_actions.record, **extra)
                self.assessment_candidate = deepcopy(assessment_candidate)
            except BaseException:
                self.reference_actions.close('construction_refused'); raise
        require(not evaluate_desk_method or self.assessment is not None, 'evaluation_requires_fixed_reference_session')
        require(evaluate_desk_method or all(v is None for v in (evaluation_resolver,evaluation_qualification_sha256,evaluation_boot_id)),
            'evaluation_ports_require_opt_in')
        try:
            super().__init__(('127.0.0.1', 0), Handler)
        except BaseException:
            if self.reference_actions is not None:
                self.reference_actions.close('construction_refused')
            raise
        self.origin = 'http://127.0.0.1:' + str(self.server_port)
        self.timeout = .2

    def run(self):
        try:
            while not self.stopped.is_set() and time.monotonic() < self.deadline:
                self.handle_request()
        finally:
            try:
                if self.reference_actions is not None:
                    self.reference_actions.close('owner_stop' if self.stopped.is_set() else 'guide_expired')
            finally:
                self.server_close()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup(); self.connection.settimeout(3)

    def log_message(self, *_):
        pass

    def reply(self, status, value, mime='application/json'):
        raw = encoded(value) if mime == 'application/json' else value
        self.send_response(status)
        for name, item in [('Content-Type', mime), ('Content-Length', str(len(raw))),
            ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
            ('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")]:
            self.send_header(name, item)
        self.end_headers(); self.wfile.write(raw)

    def scope_ok(self):
        return (self.client_address[0] == '127.0.0.1'
            and self.headers.get_all('Host') == [self.server.origin.removeprefix('http://')]
            and time.monotonic() < self.server.deadline and not self.server.stopped.is_set())

    def do_GET(self):
        if not self.scope_ok() or self.path != '/':
            return self.reply(404, {'status': 'unavailable'})
        raw = Path(__file__).with_name('desk-geometry-guide.html').read_bytes()
        raw = raw.replace(b'__NONCE__', self.server.nonce.encode()).replace(b'__CAMERA_TARGETS__',
            b'enabled' if self.server.camera_targets is not None else b'offline')
        references = (self.server.assessment_candidate['original_survey']['references']
                      if self.server.assessment_candidate is not None else None)
        raw = raw.replace(b'__ASSESSMENT_MODE__', b'enabled' if self.server.assessment is not None else b'offline')
        raw = raw.replace(b'__EVALUATION_MODE__', b'enabled' if self.server.evaluation_mode else b'offline')
        raw = raw.replace(b'__REFERENCE_ONLY_MODE__', b'enabled' if self.server.reference_only else b'offline')
        raw = raw.replace(b'__ASSESSMENT_REFERENCES__', encoded(references).replace(b'<', b'\\u003c')
                          .replace(b'>', b'\\u003e').replace(b'&', b'\\u0026'))
        self.reply(200, raw, 'text/html; charset=utf-8')

    def do_POST(self):
        try:
            require(self.scope_ok() and self.path in ('/prepare', '/stop', '/camera/snapshot', '/camera/point',
                '/assessment/freeze', '/assessment/start', '/assessment/end', '/assessment/report',
                '/evaluation/freeze','/evaluation/start','/evaluation/end','/evaluation/report','/evaluation/export',
                '/reference/freeze','/reference/start','/reference/end','/reference/results'), 'guide_scope_refused')
            require(self.headers.get_all('Origin') == [self.server.origin]
                and self.headers.get_all('X-Desk-Nonce') == [self.server.nonce], 'same_origin_action_required')
            require(self.headers.get_all('Content-Type') == ['application/json']
                and not self.headers.get_all('Transfer-Encoding'), 'bounded_json_required')
            sizes = self.headers.get_all('Content-Length', [])
            require(len(sizes) == 1 and sizes[0].isdigit() and 0 < int(sizes[0]) <= MAX_BYTES,
                'survey_input_bound')
            raw, size, deadline = bytearray(), int(sizes[0]), time.monotonic()+3
            while len(raw) < size:
                remaining = deadline-time.monotonic()
                require(remaining > 0, 'survey_body_deadline')
                self.connection.settimeout(remaining)
                part = self.rfile.read1(min(8192, size-len(raw)))
                require(bool(part), 'complete_survey_body_required'); raw.extend(part)
            value = parse(bytes(raw))
            if self.path == '/stop':
                require(value == {}, 'exact_stop_action_required')
                self.server.stopped.set()
                return self.reply(200, {'status': 'stopped'})
            if self.path.startswith('/reference/'):
                require(self.server.reference_only, 'reference_capture_not_enabled')
                action = self.path.rsplit('/', 1)[-1]
                if action == 'freeze':
                    require(exact(value, 'references protocol requirements selected_anchor_label')
                        and exact(value['protocol'], 'reference_method supported_configuration_ids max_windows'),
                        'independent_reference_freeze_required')
                    refs = reference_frame(value['references'])
                    if self.server.reference_capture is None:
                        from desk_reference_bootstrap import IndependentReferenceCapture
                        self.server.reference_capture = IndependentReferenceCapture(refs,
                            clock=self.server.reference_clock, monotonic=self.server.reference_monotonic,
                            record=self.server.reference_actions.record)
                    core = self.server.reference_capture
                    require(refs == core.references, 'frozen_room_references_changed')
                    protocol = deepcopy(value['protocol'])
                    protocol['id'] = 'reference-protocol-' + digest(protocol)[:24]
                    result = core.freeze(protocol, value['requirements'],
                        selected_anchor_label=value['selected_anchor_label'])
                else:
                    core = self.server.reference_capture
                    require(core is not None, 'freeze_independent_protocol_first')
                    if action == 'start':
                        require(exact(value, 'window_id configuration_id parts selected_anchor reference_uncertainty_m activity'),
                            'closed_reference_start_required')
                        result = core.start(value['window_id'], value['configuration_id'], value['parts'],
                            value['selected_anchor'], value['reference_uncertainty_m'], value['activity'])
                    elif action == 'end':
                        require(exact(value, 'window_id'), 'explicit_held_end_required')
                        result = core.end(value['window_id'])
                    else:
                        require(value == {}, 'fixed_private_reference_results_only')
                        result = core.read_results()
                require(self.scope_ok(), 'guide_expired_during_reference_action')
                return self.reply(200, result)
            require(not self.server.reference_only, 'reference_capture_has_no_operational_or_candidate_routes')
            if self.path.startswith(('/assessment/','/evaluation/')):
                require(self.server.assessment is not None, 'held_reference_not_enabled')
                evaluated = self.path.startswith('/evaluation/')
                require(evaluated == self.server.evaluation_mode, 'explicit_assessment_variant_required')
                core = self.server.assessment
                action = self.path.rsplit('/',1)[-1]
                if action == 'freeze':
                    require(exact(value, 'protocol requirements') and exact(value['protocol'],
                        'reference_method supported_configuration_ids max_windows'), 'independent_protocol_required')
                    protocol = deepcopy(value['protocol'])
                    protocol['id'] = 'held-protocol-' + digest(protocol)[:24]
                    result = core.freeze(protocol, value['requirements'])
                elif action == 'start':
                    require(exact(value, 'configuration_id references parts reference_uncertainty_m activity'
                            + (' selected_anchor' if evaluated else ''))
                        and value['references'] == self.server.assessment_candidate['original_survey']['references'],
                        'exact_independent_reference_frame_required')
                    references = reference_frame(value['references'])
                    require(exact(value['parts'], 'work body head hand_left hand_right protection'), 'six_reference_parts_required')
                    parts = {}
                    for name, raw_part in value['parts'].items():
                        require(isinstance(raw_part, dict) and raw_part.get('status') in ('available', 'unavailable', 'unknown'),
                            'explicit_reference_availability_required')
                        status = raw_part['status']
                        if status == 'available':
                            require(exact(raw_part, 'status outer_corners thickness_m'), 'raw_independent_measurements_required')
                            thickness = number(raw_part['thickness_m'], .001, .25) if name == 'work' else number(raw_part['thickness_m'], 0, 0)
                            shape = outer_box(raw_part['outer_corners'], references,
                                thickness_m=thickness, require_volume=name != 'work')
                            original = dict(references=deepcopy(references), outer_corners=deepcopy(raw_part['outer_corners']),
                                            thickness_m=thickness)
                        else:
                            require(exact(raw_part, 'status'), 'unavailable_reference_has_no_measurement')
                            shape = original = None
                        parts[name] = dict(status=status, shape=shape, original_measurements=original)
                    window_id = 'held-' + str(self.server.reference_windows + 1)
                    args = [window_id, value['configuration_id'], parts,value['reference_uncertainty_m'],value['activity']]
                    if evaluated: args.append(value['selected_anchor'])
                    result = core.start(*args)
                    self.server.reference_windows += 1
                elif action == 'end':
                    require(exact(value, 'window_id'), 'explicit_held_end_required')
                    result = core.end(value['window_id'])
                elif action == 'report':
                    require(value == {}, 'fixed_private_collection_only')
                    result = core.report()
                else:
                    require(evaluated and value == {}, 'fixed_private_export_only')
                    result = core.export(self.server.reference_actions.export)
                require(self.scope_ok(), 'guide_expired_during_reference_action')
                return self.reply(200, result)
            if self.path in ('/camera/snapshot', '/camera/point'):
                require(self.server.camera_targets is not None and not self.server.saved, 'camera_targets_not_enabled')
                if self.path == '/camera/snapshot':
                    require(value == {}, 'exact_snapshot_action_required')
                    result = self.server.camera_targets.snapshot()
                else:
                    require(exact(value, 'snapshot_id click_id u v reference'), 'exact_frozen_point_action_required')
                    result = self.server.camera_targets.point(value['snapshot_id'], value['click_id'],
                        value['u'], value['v'], value['reference'])
                require(self.scope_ok(), 'guide_expired_during_camera_action')
                return self.reply(200, result)
            require(not self.server.saved, 'candidate_already_saved')
            acquired = (isinstance(value, dict) and isinstance(value.get('camera'), dict)
                        and value['camera'].get('schema') == 'home-desk-camera-targets-1')
            require(not acquired or self.server.camera_targets is not None, 'fixed_camera_original_store_required')
            candidate = prepare_candidate(value, camera_original_resolver=(
                self.server.camera_targets.resolve_point if acquired else None))
            save_candidate(self.server.output, candidate)
            self.server.saved = True
            self.reply(200, {'status': 'candidate_saved', 'digest': candidate['digest'],
                'qualified': False, 'camera_status': candidate['camera_registration']['status'],
                'required_review': candidate['required_review']})
        except (ValueError, OSError, KeyError, TypeError, OverflowError, RecursionError, zipfile.BadZipFile):
            # No coordinates, entered text, file paths or exception contents.
            self.reply(409, {'status': 'refused', 'reason': 'invalid_or_unavailable_candidate_survey'})


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--camera-targets', action='store_true',
        help='Opt in on Victoria: read frozen targets from the existing fixed loopback owner')
    parser.add_argument('--assessment-candidate', help='Fixed owner-local candidate2 JSON; predictions remain hidden')
    parser.add_argument('--assessment-collection', help='Fixed private completed candidate collection JSONL')
    parser.add_argument('--assessment-directory', help='New private directory for immutable reference actions')
    parser.add_argument('--reference-capture-directory',
        help='Opt in to private independent reference capture without a candidate or predictions')
    parser.add_argument('--evaluate-desk-method', action='store_true', help='Opt in to independent aligned method evaluation')
    parser.add_argument('--evaluation-inputs', help='Fixed private independently qualified original bundle')
    parser.add_argument('--evaluation-qualification-sha256', help='Externally supplied static qualification SHA256; never inferred from file')
    parser.add_argument('--evaluation-alignment-inputs', help='Fixed private report-time exposure alignment originals, never selected by browser')
    options = parser.parse_args(arguments)
    try:
        require(not options.reference_capture_directory or not any((options.camera_targets,
            options.assessment_candidate, options.assessment_collection, options.assessment_directory,
            options.evaluate_desk_method, options.evaluation_inputs, options.evaluation_qualification_sha256,
            options.evaluation_alignment_inputs)), 'reference_capture_requires_separate_mode')
        camera_targets = None
        if options.camera_targets:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from desk_camera_targets import DeskCameraTargets, incumbent_reader
            path = Path(options.output)
            camera_targets = DeskCameraTargets(path.with_name(path.stem + '-camera-originals'), incumbent_reader())
        assessment = {}
        selected = (options.assessment_candidate, options.assessment_collection, options.assessment_directory)
        if any(selected):
            require(all(selected), 'all_fixed_assessment_paths_required')
            candidate_path, collection_path = (Path(p) for p in selected[:2])
            require(all(p.is_absolute() and not p.is_symlink() for p in (candidate_path, collection_path))
                and candidate_path.is_file() and candidate_path.stat().st_size <= MAX_BYTES,
                'bounded_private_assessment_candidate_required')
            require(os.name == 'nt' or candidate_path.stat().st_mode & 0o077 == 0, 'private_candidate_required')
            runtime = Path(__file__).resolve().parents[2] / 'tools/den-live-runtime'
            if str(runtime) not in sys.path: sys.path.append(str(runtime))
            from lab_assessment_collect import read_verified_attempts
            assessment = dict(assessment_candidate=parse(candidate_path.read_bytes()),
                assessment_collection_loader=lambda: read_verified_attempts(collection_path),
                assessment_directory=Path(options.assessment_directory))
        if options.evaluate_desk_method:
            require(bool(assessment), 'evaluation_requires_fixed_assessment_paths')
            require(bool(options.evaluation_inputs) == bool(options.evaluation_qualification_sha256), 'paired_original_path_and_external_pin_required')
            def read_evaluation_input(filename):
                if not filename: return None
                p = Path(filename)
                require(p.is_absolute() and not p.is_symlink(), 'absolute_private_qualified_originals_required')
                fd = os.open(p, os.O_RDONLY | getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0))
                with os.fdopen(fd,'rb') as stream:
                    before = os.fstat(stream.fileno())
                    require(stat.S_ISREG(before.st_mode) and before.st_size <= 128*1024
                        and (os.name == 'nt' or before.st_uid == os.getuid() and before.st_mode & 0o077 == 0),
                        'bounded_private_qualified_originals_required')
                    raw = stream.read(128*1024+1)
                    after = os.fstat(stream.fileno())
                    require(len(raw) == before.st_size and (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
                        == (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns), 'independent_original_read_changed')
                value = parse(raw)
                return value
            class FixedEvaluationResolver:
                def qualification(self):
                    value = read_evaluation_input(options.evaluation_inputs)
                    require(value is None or digest(value) == options.evaluation_qualification_sha256, 'independent_qualification_preimage_changed')
                    return value
                def align(self, request):
                    if request is not None and request.get('version') == 2:
                        from desk_method_export import build_bundled_alignment
                        require('value' in bundled_originals, 'same_read_bundled_originals_required')
                        return build_bundled_alignment(self.qualification(), bundled_originals['value'], request)
                    # Fixed private original reader; this does not authenticate
                    # timestamp truth or install an independent method.
                    return read_evaluation_input(options.evaluation_alignment_inputs)
            bundled_originals = {}
            resolver = FixedEvaluationResolver()
            from desk_method_export import validate_qualification
            qualification = validate_qualification(resolver.qualification())
            if qualification is not None and qualification['version'] == 3:
                require(not options.evaluation_alignment_inputs, 'bundled_alignment_is_original_proof_recomputation')
                from lab_assessment_collect import read_verified_bundled_originals
                def load_bundled_originals():
                    value = read_verified_bundled_originals(collection_path)
                    bundled_originals['value'] = deepcopy(value)
                    return value
                assessment['assessment_collection_loader'] = load_bundled_originals
            def boot_id():
                require(os.name == 'posix', 'qualified_reference_boot_port_unavailable')
                return Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
            assessment.update(evaluate_desk_method=True,evaluation_resolver=resolver,
                evaluation_qualification_sha256=options.evaluation_qualification_sha256,evaluation_boot_id=boot_id)
        else:
            require(not options.evaluation_inputs and not options.evaluation_qualification_sha256 and not options.evaluation_alignment_inputs, 'evaluation_inputs_require_opt_in')
        guide = Guide(options.output, seconds=options.seconds, camera_targets=camera_targets,
            reference_capture_directory=options.reference_capture_directory, **assessment)
        print(json.dumps({'status': 'ready', 'url': guide.origin+'/', 'expires_in_s': options.seconds,
            'qualified': False, 'physical_authority': False}), flush=True)
        try:
            guide.run()
        except KeyboardInterrupt:
            guide.stopped.set()
        return 0
    except (ValueError, OSError):
        print(json.dumps({'status': 'refused', 'reason': 'owner_local_guide_unavailable'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
