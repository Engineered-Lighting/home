"""Fixed retained path-original adapter for the canonical software-only route.

The installed server supplies the profiles and original-store resolver. No
request selects a resolver, profile, check, path or qualification. This adapter
does not assess a room from desired angles: an independent retained witness
must answer the exact current commit/native query. The incumbent fictional
compiler, authority, recorder and canonical encoders remain the consumers.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import math

from fixture_execution import CHECK_NAMES
from fixture_execution_validation import require, MAX_U64, CAP_FIELDS
from fixture_joint_execution import (JointFixtureCompiler, RETAINED_REQUEST_SCHEMA,
    RETAINED_ASSESSMENT_METHOD, retained_native_reference_digest)
from journal import canonical, digest


def _token(value):
    return type(value) is str and 0 < len(value) <= 160


def _sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _pin(value):
    return type(value) is dict and set(value) == {'id', 'digest'} and _token(value['id']) and _sha(value['digest'])


def _uint(value):
    return type(value) is int and 0 <= value <= MAX_U64


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_path_witness(receipt, query, *, receipt_store_id, receipt_component_id, now_home_us):
    """Verify a body returned by the fixed retained-original store capability."""
    require(_uint(now_home_us) and type(receipt) is dict and set(receipt) == {'ref', 'original'},
            'retained_path_original_required')
    ref, original = receipt['ref'], receipt['original']
    require(type(ref) is dict and set(ref) == {'store_id', 'component_id', 'receipt_id', 'digest'}
            and ref['store_id'] == receipt_store_id and ref['component_id'] == receipt_component_id
            and _token(ref['receipt_id']) and _sha(ref['digest']), 'fixed_retained_path_store_required')
    fields = {'schema', 'version', 'query', 'checks', 'minimum_work_range_m', 'target_uncertainty_m',
              'total_aim_error_bound_rad', 'observed_at_home_us', 'available_at_home_us',
              'valid_until_home_us', 'evidence_source', 'physical_permission', 'physical_truth'}
    require(type(original) is dict and set(original) == fields
            and original['schema'] == 'home-retained-task-path-witness-1'
            and type(original['version']) is int and original['version'] == 1
            and canonical(original['query']) == canonical(query), 'exact_retained_path_query_required')
    require(type(original['checks']) is dict and set(original['checks']) == CHECK_NAMES
            and all(v is True for v in original['checks'].values()), 'independent_retained_path_checks_required')
    require(original['evidence_source'] == 'authored-independent'
            and original['physical_permission'] is False and original['physical_truth'] is False,
            'software_only_path_witness_required')
    require(all(_uint(original[k]) for k in ('observed_at_home_us', 'available_at_home_us', 'valid_until_home_us'))
            and original['observed_at_home_us'] <= original['available_at_home_us'] <= now_home_us
            < original['valid_until_home_us'] <= min(query['original_caps'].values()),
            'retained_path_original_time_or_caps_refused')
    require(all(_number(original[k]) for k in ('minimum_work_range_m', 'target_uncertainty_m', 'total_aim_error_bound_rad'))
            and original['minimum_work_range_m'] > 0 and original['target_uncertainty_m'] >= 0
            and 0 <= original['total_aim_error_bound_rad'] <= math.pi,
            'independent_retained_path_bounds_required')
    require(digest(original) == ref['digest'], 'retained_path_original_digest_changed')
    return deepcopy(receipt)


def validate_archived_path_witness(data):
    """Structural causal replay check; this does not reissue an authority."""
    r, receipt = data['assessment'], data['path_witness']
    require(type(receipt) is dict and set(receipt) == {'ref', 'original'}
            and type(receipt['original']) is dict and type(receipt['original'].get('query')) is dict,
            'recorded_retained_path_original_required')
    q = receipt['original']['query']
    query_fields = {'schema', 'version', 'scope', 'assessor_descriptor', 'room_commit_ref', 'input_cut', 'fixture_id',
                    'retained_aim_ref', 'retained_motion', 'profile_sha256', 'provider_pins', 'method_ref',
                    'qualification_ref', 'lineage', 'native', 'original_caps', 'motion'}
    require(set(q) == query_fields and type(q['scope']) is dict
            and set(q['scope']) == {'site_id', 'room_id', 'session_id', 'domain_epoch'}
            and all(_token(v) for v in q['scope'].values())
            and r['scope'] == dict(home_id=q['scope']['site_id'], room_id=q['scope']['room_id'], fixture_id=data['fixture_id'])
            and q['input_cut'].get('cut_digest') == r['retained_aim_ref']['input_cut_digest'],
            'recorded_retained_path_scope_binding_refused')
    descriptor, ref = q.get('assessor_descriptor'), data['assessor_ref']
    require(type(descriptor) is dict and type(ref) is dict and set(ref) == {'id', 'digest'}
            and ref['id'] == 'home-retained-task-assessor-capability-1' and ref['digest'] == digest(descriptor),
            'recorded_retained_path_assessor_binding_refused')
    fields = {'schema', 'version', 'fixture_profiles', 'receipt_store_id', 'receipt_component_id', 'method_ref',
              'qualification_ref', 'speed_fixture_deg_s', 'motion_timeout_us', 'evidence_source',
              'physical_permission', 'physical_truth'}
    require(set(descriptor) == fields and descriptor['schema'] == ref['id']
            and type(descriptor['version']) is int and descriptor['version'] == 1
            and type(descriptor['fixture_profiles']) is dict and 1 <= len(descriptor['fixture_profiles']) <= 4
            and all(_token(k) and _sha(v) for k,v in descriptor['fixture_profiles'].items())
            and _token(descriptor['receipt_store_id']) and _token(descriptor['receipt_component_id'])
            and _pin(descriptor['method_ref']) and _pin(descriptor['qualification_ref'])
            and type(descriptor['speed_fixture_deg_s']) is list and len(descriptor['speed_fixture_deg_s']) == 2
            and all(_number(v) and v > 0 for v in descriptor['speed_fixture_deg_s'])
            and _uint(descriptor['motion_timeout_us']) and descriptor['motion_timeout_us'] > 0
            and descriptor['evidence_source'] == 'simulated' and descriptor['physical_permission'] is False
            and descriptor['physical_truth'] is False, 'recorded_retained_path_assessor_binding_refused')
    require(q.get('schema') == 'home-retained-task-path-query-1' and type(q.get('version')) is int and q['version'] == 1
            and q.get('fixture_id') == data['fixture_id'] and q.get('room_commit_ref') == data['room_commit_ref']
            and q.get('retained_aim_ref') == r['retained_aim_ref']
            and q.get('profile_sha256') == data['profile_sha256'] == descriptor['fixture_profiles'].get(data['fixture_id'])
            and q.get('method_ref') == descriptor['method_ref'] and q.get('qualification_ref') == descriptor['qualification_ref']
            and q.get('original_caps') == r['original_caps'] and q.get('lineage') == r['lineage']
            and descriptor['speed_fixture_deg_s'] == r['speed_fixture_deg_s']
            and descriptor['motion_timeout_us'] == r['motion_timeout_us'], 'recorded_retained_path_query_binding_refused')
    require(type(q.get('motion')) is dict and set(q['motion']) == {'kind', 'motion_id', 'revision_id', 'predecessor_id',
                'fixture_angles_deg', 'speed_fixture_deg_s', 'motion_timeout_us'}
            and all(q['motion'][k] == r[k] for k in q['motion']), 'recorded_retained_path_motion_binding_refused')
    require(type(q['native']) is dict and set(q['native']) == {'reference_sha256', 'snapshot_sha256', 'capabilities_sha256', 'start_centideg'}
            and all(_sha(q['native'][k]) for k in ('reference_sha256', 'snapshot_sha256', 'capabilities_sha256'))
            and type(q['original_caps']) is dict and set(q['original_caps']) == set(CAP_FIELDS)
            and all(_uint(v) for v in q['original_caps'].values())
            and q['retained_motion']['retained_aim_ref'] == q['retained_aim_ref']
            and [q['retained_motion']['original'][k] for k in ('to_pan_deg', 'to_tilt_deg')] == r['fixture_angles_deg'],
            'recorded_retained_path_native_or_endpoint_binding_refused')
    a, o = r['assessment'], receipt['original']
    require(a['assessment_id'] == 'retained-path-' + receipt['ref']['digest']
            and a['checks'] == o['checks'] and a['current_reference_sha256'] == q['native']['reference_sha256']
            and a['start_centideg'] == q['native']['start_centideg']
            and all(a[k] == o[k] for k in ('minimum_work_range_m', 'target_uncertainty_m', 'total_aim_error_bound_rad'))
            and a['valid_from_home_us'] == o['available_at_home_us'] and a['valid_until_home_us'] == o['valid_until_home_us']
            and r['support_expiry_home_us'] == min(o['valid_until_home_us'], *r['original_caps'].values()),
            'recorded_retained_path_assessment_binding_refused')
    validate_path_witness(receipt, q, receipt_store_id=descriptor['receipt_store_id'],
        receipt_component_id=descriptor['receipt_component_id'], now_home_us=r['issued_home_us'])
    return data


class TrustedRetainedTaskAssessor:
    """Server-constructor capability; its resolver reads already retained originals.

    ``resolve_original(query)`` must return the exact original and receipt from
    the fixed named store/component, or None. It must not create a witness from
    this query. The descriptor and profile preimages are pinned in declaration4
    and retained before READY. There is no JSON-to-callable installation path.
    """
    def __init__(self, *, profiles, receipt_store_id, receipt_component_id, method_ref,
                 qualification_ref, resolve_original, speed_fixture_deg_s, motion_timeout_us):
        require(type(profiles) is dict and 1 <= len(profiles) <= 4
                and all(_token(k) and type(v) is dict and v.get('schema') == 'home-fixture-joint-execution-profile-1'
                        for k, v in profiles.items())
                and _token(receipt_store_id) and _token(receipt_component_id)
                and _pin(method_ref) and _pin(qualification_ref) and callable(resolve_original)
                and type(speed_fixture_deg_s) in (list, tuple) and len(speed_fixture_deg_s) == 2
                and all(_number(v) and v > 0 for v in speed_fixture_deg_s)
                and _uint(motion_timeout_us) and motion_timeout_us > 0, 'fixed_retained_task_assessor_capability_required')
        self._profiles = deepcopy(profiles)
        self._store, self._component = receipt_store_id, receipt_component_id
        self._method, self._qualification = deepcopy(method_ref), deepcopy(qualification_ref)
        self._resolve = resolve_original
        self._installed_resolve = resolve_original
        self._speeds, self._timeout = list(speed_fixture_deg_s), motion_timeout_us
        self._frozen = digest(self.provenance())

    def descriptor(self):
        return dict(schema='home-retained-task-assessor-capability-1', version=1,
            fixture_profiles={k: digest(v) for k, v in self._profiles.items()},
            receipt_store_id=self._store, receipt_component_id=self._component,
            method_ref=deepcopy(self._method), qualification_ref=deepcopy(self._qualification),
            speed_fixture_deg_s=deepcopy(self._speeds), motion_timeout_us=self._timeout,
            evidence_source='simulated', physical_permission=False, physical_truth=False)

    def provenance(self):
        return dict(descriptor=self.descriptor(), profiles=deepcopy(self._profiles))

    def require_current(self):
        require(self._resolve is self._installed_resolve and digest(self.provenance()) == self._frozen,
                'retained_task_assessor_installation_changed')

    def compiler_for(self, fixture_id, *, modules, binding, canonical_config):
        self.require_current()
        profile = self._profiles.get(fixture_id)
        if profile is None:
            return None
        require(profile['motor_profile']['fixture_id'] == fixture_id, 'fixed_task_profile_fixture_required')
        return JointFixtureCompiler(deepcopy(profile), canonical_config=canonical_config,
            provider_pins=modules.provider_pins, binding=binding,
            decode_status=modules.zha.decode_snapshot_v3, decode_capabilities=modules.zha.decode_capabilities_v2)

    def query(self, member):
        """Data-only exact query for an independently retained witness producer."""
        self.require_current()
        r = member.resolver
        require(r.task_assessor is self and r.task_compiler is not None
                and digest(r.task_compiler.profile) == self.descriptor()['fixture_profiles'].get(r.fixture_id),
                'same_fixed_retained_task_assessor_required')
        state, trace, clock, snapshot, correction = r.assessment_basis()
        status = r.modules.zha.decode_snapshot_v3(state.raw_status_v3, binding=r.binding,
            captured_device_us=state.current_ref.captured_device_us)
        previous = r.task_previous
        if previous is not None:
            require(all(previous['retained_aim_ref'][k] == trace['retained_aim_ref'][k]
                        for k in ('motion_trace_sha256', 'fixture_basis_sha256')), 'retained_task_motion_changed')
            kind, motion = 'CONTINUE', previous['motion_id']
            start = deepcopy(previous['start_centideg'])
        else:
            kind, motion = 'MOVE', int(status['motion_id']) + 1
            start = [v['angle_centideg'] for v in status['axis_observations']]
        scene = snapshot['decision']['scene']
        lineage = dict(source_cut_sha256=state.source_cut, protection_sha256=state.protection,
            authority_sha256=state.authority, scene_id=scene['scene_id'], episode_id=scene['episode_id'],
            example_id=scene['example_ref']['id'], correction_ref=deepcopy(correction))
        return dict(schema='home-retained-task-path-query-1', version=1, scope=deepcopy(snapshot['home_scope']),
            assessor_descriptor=self.descriptor(),
            room_commit_ref=deepcopy(state.room_commit_ref), input_cut=deepcopy(snapshot['decision']['input_cut']),
            fixture_id=r.fixture_id, retained_aim_ref=deepcopy(trace['retained_aim_ref']),
            retained_motion=deepcopy(trace), profile_sha256=digest(r.task_compiler.profile),
            provider_pins=deepcopy(r.modules.provider_pins), method_ref=deepcopy(self._method),
            qualification_ref=deepcopy(self._qualification), lineage=lineage,
            native=dict(reference_sha256=retained_native_reference_digest(asdict(state.current_ref)),
                snapshot_sha256=hashlib.sha256(state.raw_status_v3).hexdigest(),
                capabilities_sha256=hashlib.sha256(state.raw_capabilities_v2).hexdigest(),
                start_centideg=start), original_caps={k: int(v) for k, v in state.caps.items()},
            motion=dict(kind=kind, motion_id=motion, revision_id=int(member.dispatcher._meta()['last_revision']) + 1,
                predecessor_id=state.current_ref.revision_id,
                fixture_angles_deg=[trace['original'][k] for k in ('to_pan_deg', 'to_tilt_deg')],
                speed_fixture_deg_s=deepcopy(self._speeds), motion_timeout_us=self._timeout))

    def install(self, member):
        """Install only an independently retained witness for this exact cut."""
        from room_fixture_command_join import RoomRetainedTaskAssessment
        self.require_current()
        r = member.resolver
        with r.source.journal.lock:
            query = self.query(member)
            retained = self._resolve(deepcopy(query))
            state, _, clock, snapshot, _ = r.assessment_basis()
            require(canonical(self.query(member)) == canonical(query), 'task_changed_during_path_original_lookup')
            witness = validate_path_witness(retained, query, receipt_store_id=self._store,
                receipt_component_id=self._component, now_home_us=clock.now_home_us)
            o, ref = witness['original'], witness['ref']
            require(clock.now_home_us + clock.uncertainty_us < o['valid_until_home_us'],
                    'retained_path_uncertain_original_expiry')
            request = deepcopy(query['motion'])
            request.update(schema=RETAINED_REQUEST_SCHEMA, version=2,
                scope=dict(home_id=snapshot['home_scope']['site_id'], room_id=snapshot['home_scope']['room_id'], fixture_id=r.fixture_id),
                pins=deepcopy(r.task_compiler.p['pins']), lineage=deepcopy(query['lineage']),
                context=dict(boot_generation=state.current_ref.boot_generation, session_id=state.current_ref.session_id,
                    ownership_generation=state.current_ref.ownership_generation),
                retained_aim_ref=deepcopy(query['retained_aim_ref']), issued_home_us=clock.now_home_us,
                support_expiry_home_us=min(o['valid_until_home_us'], *query['original_caps'].values()),
                original_caps=deepcopy(query['original_caps']))
            target = r.task_compiler.compile_retained_target_joint(request['fixture_angles_deg'],
                request['speed_fixture_deg_s'], now_home_us=clock.now_home_us)
            request['assessment'] = dict(status='fictional', assessment_id='retained-path-' + ref['digest'],
                method=RETAINED_ASSESSMENT_METHOD, profile_sha256=query['profile_sha256'], pins=deepcopy(request['pins']),
                lineage=deepcopy(request['lineage']), fixture_angles_deg=deepcopy(request['fixture_angles_deg']),
                retained_aim_ref=deepcopy(request['retained_aim_ref']), target_centideg=target['target_centideg'],
                start_centideg=deepcopy(query['native']['start_centideg']), current_reference_sha256=query['native']['reference_sha256'],
                motion_id=request['motion_id'], checks=deepcopy(o['checks']), valid_from_home_us=o['available_at_home_us'],
                valid_until_home_us=o['valid_until_home_us'], original_caps=deepcopy(request['original_caps']),
                minimum_work_range_m=o['minimum_work_range_m'], target_uncertainty_m=o['target_uncertainty_m'],
                total_aim_error_bound_rad=o['total_aim_error_bound_rad'])
            # Existing compiler validates the original STATUS/CAP and independent
            # bounds before publication; no reservation or native effect occurs.
            r.task_compiler.compile_retained_stationary_joint(request, now_home_us=clock.now_home_us,
                current_ref=state.current_ref, raw_status_v3=state.raw_status_v3,
                raw_capabilities_v2=state.raw_capabilities_v2, previous=r.task_previous)
            r.install_task_assessment(RoomRetainedTaskAssessment(request, path_witness=witness,
                assessor_ref=dict(id='home-retained-task-assessor-capability-1', digest=digest(self.descriptor()))))
            return dict(status='installed', witness_ref=deepcopy(ref), assessment_sha256=digest(request), physical_permission=False)
