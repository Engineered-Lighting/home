"""Detached commands from exact accepted room samples; no native/physical I/O.

Only RoomCommandProjector obtains operational originals, through the installed
CommittedRoomSource. The pure projection function is also useful for authored
numerical tests; JSON passed to it never creates authority. The downstream
consumer must recheck the exact commit, native state, full24 power, grant, CAS
and leases before dispatch. This module owns no IDs, scheduler or calibration.
"""
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from journal import digest, finite
from room_committed_source import CommittedRoomSource
from room_delivery import DOMAINS, validate_room_policy, room_policy_view
from room_canonical_provider import WINDOWS_PROVIDER, MIXER_SOURCE_SHA256, PIN_KEYS, selected_provider


SCHEMA = 'home-room-command-projection-1'
RETAINED_TASK_PROFILE1 = dict(schema='home-canonical-retained-task-profile-1', version=1,
    input_profile='producer8-config13-stage12-room-output1', room_policy_version=2,
    aim_only=True, domain_mask=128, field_mask=[0, 0, 0, 0, 0, 0, 0, 4],
    requires_original_dark_spotlight=True, semantic_CCT_mapping=None,
    evidence_source='simulated', physical_permission=False, physical_truth=False)
RETAINED_TASK_PROFILE_REF = dict(id=RETAINED_TASK_PROFILE1['schema'], digest=digest(RETAINED_TASK_PROFILE1))


def retained_task_profile(value):
    if value is None:
        return False
    need(type(value) is dict and value == RETAINED_TASK_PROFILE1, 'fixed_retained_task_profile_required')
    return True
# Historical Windows alias; admitted projections record their actual profile.
PROVIDER_PINS = WINDOWS_PROVIDER.provider_pins


class RoomProjectionRefusal(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise RoomProjectionRefusal(reason)


@dataclass(frozen=True)
class CanonicalProjectionProvider:
    """Fixed trusted installation port, not a caller-supplied permission.

    ambient_mix is the pinned c12_runtime_outputs.ambient_mix implementation,
    installed by the consumer after verifying the immutable provider closure.
    No path, import name, URL, codec or process is selected by a room request.
    """
    manifest_sha256: str
    source_sha256: str
    executable_sha256: str
    configuration_sha256: str
    mixer_source_sha256: str
    ambient_mix: object


def _provider(provider):
    need(type(provider) is CanonicalProjectionProvider, 'fixed_canonical_provider_required')
    pins = {key: getattr(provider,key) for key in PIN_KEYS}
    try: profile = selected_provider()
    except ValueError: raise RoomProjectionRefusal('unsupported_fixed_canonical_provider_platform') from None
    need(profile.matches(pins)
         and provider.mixer_source_sha256 == MIXER_SOURCE_SHA256
         and callable(provider.ambient_mix), 'canonical_projection_provider_changed')
    return pins


def _refuse_subset_policy(room_policy):
    # Policy2's nullable assessments require a separately reviewed command view.
    # The historical pure authored fixtures may omit schema/version; no old
    # policy or operational validator is broadened by this explicit refusal.
    need(not (type(room_policy) is dict
        and (room_policy.get('schema') == 'home-live-room-policy-2'
             or room_policy.get('version') == 2)),
        'room_policy2_command_projection_unavailable')


def quantize_ambient_sample(sample):
    """Nearest integer, positive ties up; never silently clamp unsupported CCT.

    Errors describe representation only, not measured emitter CCT/brightness.
    Exact original sample values remain present in the returned record.
    """
    need(type(sample) is dict and set(sample) == {'brightness_fraction', 'cct_kelvin'},
         'closed_committed_sample_required')
    fraction, kelvin = sample['brightness_fraction'], sample['cct_kelvin']
    need(finite(fraction) and 0 <= fraction <= 1 and finite(kelvin) and 1500 <= kelvin <= 6500,
         'committed_sample_bounds')
    f, k = Decimal(str(fraction)), Decimal(str(kelvin))
    level = int((f * 254).to_integral_value(rounding=ROUND_HALF_UP))
    mired = int((Decimal(1000000) / k).to_integral_value(rounding=ROUND_HALF_UP))
    need(154 <= mired <= 556, 'ambient_CCT_mapping_out_of_range')
    return dict(level=level, mired=mired, quantization=dict(
        rule='nearest_integer_positive_ties_up', source_sample=deepcopy(sample),
        brightness_fraction_error=float(Decimal(level) / 254 - f),
        mired_error=float(Decimal(mired) - Decimal(1000000) / k),
        represented_kelvin=float(Decimal(1000000) / mired),
        calibrated_photometry=False))


def _domain(row, now, policy_until):
    need(type(row) is dict and row.get('domain') in (*DOMAINS, 'spotlight')
         and row.get('status') in {'modeled', 'off', 'unassigned', 'unavailable'}
         and finite(row.get('valid_until')) and row['valid_until'] >= 0,
         'invalid_committed_domain')
    original = {key: deepcopy(row[key]) for key in
                ('domain', 'status', 'reason', 'sample', 'valid_until', 'evidence_refs', 'assessment_refs')}
    usable = row['status'] in {'modeled', 'off'} and row['sample'] is not None
    until = min(row['valid_until'], policy_until)
    reason = row['reason']
    if usable and not now < until:
        usable, reason = False, 'committed_sample_expired'
    if row['status'] == 'off' and row['sample'] is not None:
        need(row['sample'].get('brightness_fraction') == 0, 'off_sample_must_be_zero')
    return dict(original=original, status='sampled' if usable else 'unavailable', reason=reason,
                support_until=until, intent=None)


def _task(snapshot, policy, fixture, domain, *, task_profile=None):
    """Retained endpoint is a trace, never a new per-sample MOVE command."""
    task = snapshot['decision']['room_output']['task']
    result = dict(status='unavailable', reason='fixture_not_selected', retained_motion=None,
                  sampled_virtual_readback=None, native_motion_equivalence=False,
                  command_ready=False)
    if task.get('fixture_id') != fixture['fixture_id']:
        return result
    if fixture['task_policy'] is None:
        raise RoomProjectionRefusal('selected_task_without_commissioned_fixture')
    execution, allocation, lease = (task[key] for key in ('execution', 'allocation', 'display_lease'))
    now, t = snapshot['checked_at'], snapshot['sample_at']
    if (domain['status'] != 'sampled' or execution.get('status') != 'virtual'
            or allocation.get('status') != 'allocated' or lease.get('status') != 'current'):
        result['reason'] = 'committed_task_unavailable'
        return result
    untils = {name: value['valid_until'] for name, value in
              (('domain', domain['original']), ('allocation', allocation), ('execution', execution), ('display', lease))}
    need(all(finite(x) and x >= 0 for x in untils.values()), 'invalid_task_original_lease')
    until = min(*untils.values(), domain['support_until'])
    if not now < until:
        result['reason'] = 'committed_task_expired'
        return result
    room_state = snapshot['commit']['checkpoint']['state']['room_execution']
    states = [row['snapshot']['state'] for row in room_state['tasks']
              if row['fixture_id'] == fixture['fixture_id']]
    need(len(states) == 1 and type(states[0].get('plan')) is dict, 'exact_retained_task_plan_required')
    state, plan = states[0], states[0]['plan']
    need(len(allocation['assignments']) == len(execution['readback']) == 1,
         'one_committed_task_assignment_required')
    assignment, readback = allocation['assignments'][0], execution['readback'][0]
    need(execution['sample_at'] == lease['sample_at'] == t
         and execution['samples'] == [{'t': t, 'readback': [readback]}]
         and state['last_readback'] == readback and state['last_valid_until'] == lease['valid_until']
         and assignment['fixture_id'] == readback['fixture_id'] == fixture['fixture_id']
         and assignment['fixture_pose_ref'] == readback['fixture_pose_ref'] == fixture['pose_ref']
         and assignment['pan_deg'] == plan['to_pan_deg']
         and assignment['tilt_deg'] == plan['to_tilt_deg']
         and assignment['goal_id'] == plan['goal_id']
         and domain['original']['sample'] == {key: readback[key] for key in ('brightness_fraction', 'cct_kelvin')},
         'retained_task_sample_linkage_changed')
    f = fixture['task_policy']['fixture']
    need(f['id'] == fixture['fixture_id'] and f['fixture_pose_ref'] == fixture['pose_ref'],
         'retained_task_fixture_basis_changed')
    need(all(finite(plan[key]) for key in ('started_at', 'to_pan_deg', 'to_tilt_deg')),
         'invalid_retained_angles')
    # The original motion, not the nominal-dependent plan.key, is stable across
    # brightness corrections. No native motion/revision ID is allocated here.
    motion = {key: deepcopy(plan[key]) for key in
              ('started_at', 'from_pan_deg', 'from_tilt_deg', 'to_pan_deg', 'to_tilt_deg', 'motion')}
    motion.update(fixture_id=fixture['fixture_id'], fixture_pose_ref=deepcopy(fixture['pose_ref']))
    result = dict(status='retained_aim_trace', reason='angle_compiler_successor_required',
        retained_motion=dict(motion_trace_sha256=digest(motion), plan_key=plan['key'],
            original=motion, assignment=deepcopy(assignment),
            fixture_basis=dict(origin_m=deepcopy(f['origin_m']), local_to_room=deepcopy(f['local_to_room']),
                fixture_pose_ref=deepcopy(f['fixture_pose_ref']),
                convention='pan_deg_tilt_from_downward_minus_Z_deg'),
            execution_assessment_refs=deepcopy(execution['assessment_refs']),
            geometry_refs=deepcopy(lease['geometry_refs']), protection_refs=deepcopy(lease['protection_refs']),
            original_exclusive_leases=untils, support_until=until),
        sampled_virtual_readback=deepcopy(readback), native_motion_equivalence=False, command_ready=False)
    if task_profile is not None:
        retained_task_profile(task_profile)
        retained = result['retained_motion']
        retained['retained_aim_ref'] = dict(room_commit_ref=deepcopy(snapshot['room_commit_ref']),
            input_cut_digest=snapshot['decision']['input_cut']['cut_digest'],
            motion_trace_sha256=retained['motion_trace_sha256'], fixture_basis_sha256=digest(retained['fixture_basis']))
        result.update(status='retained_aim_candidate', reason='original_dark_native_and_fictional_path_assessment_required',
            aim_only=True, light_request=dict(status='unmet', reason='spotlight_semantic_CCT_mapping_unavailable',
                sample=deepcopy(domain['original']['sample'])))
    return result


def project_committed_snapshot(snapshot, *, room_policy, provider, task_profile=None):
    """Pure detached projection. Its argument alone is never journal authority."""
    task_mode = retained_task_profile(task_profile)
    raw_policy = room_policy
    if task_mode:
        need(type(raw_policy) is dict and raw_policy.get('schema') == 'home-live-room-policy-2'
             and type(raw_policy.get('version')) is int and raw_policy['version'] == 2,
             'retained_task_room_policy2_required')
        room_policy = room_policy_view(raw_policy, allow_assessed_subset=True)
    else:
        _refuse_subset_policy(room_policy)
    admitted_provider_pins = _provider(provider)
    need(snapshot.get('schema') == 'home-accepted-room-command-source-1'
         and type(snapshot.get('version')) is int and snapshot['version'] == 1
         and snapshot.get('physical_permission') is False
         and snapshot.get('native_achieved_truth') is False, 'accepted_room_source_shape_required')
    now, t = snapshot['checked_at'], snapshot['sample_at']
    need(finite(now) and finite(t) and 0 <= t <= now, 'committed_projection_clock')
    output = snapshot['decision']['room_output']
    if task_mode:
        need(snapshot['room_commit_ref']['worker_version'] == 'home-live-context-worker-12.0.0',
             'retained_task_worker12_required')
    need(output['policy_ref'] == room_policy['policy_ref'] and output['sample_at'] == t
         and snapshot['decision']['t'] == t and output['input_cut_digest'] == snapshot['decision']['input_cut']['cut_digest']
         and output['source_generations'] == snapshot['decision']['input_cut']['source_generations'],
         'exact_committed_input_cut_required')
    configured = room_policy['fixtures']
    need(len(configured) == len(output['fixtures']) == 4
         and len({f['fixture_id'] for f in configured}) == 4
         and [f['fixture_id'] for f in configured] == [f['fixture_id'] for f in output['fixtures']],
         'four_fixed_fixture_projection_required')
    fixtures = []
    for f, original in zip(configured, output['fixtures']):
        need(all(original[key] == f[key] for key in ('fixture_id', 'mount_id', 'profile_ref'))
             and len(original['domains']) == 8
             and tuple(d['domain'] for d in original['domains']) == (*DOMAINS, 'spotlight'),
             'exact_fixture_and_eight_domains_required')
        assessed = f.get('assessment_available') is not False
        policy_until = min(room_policy['valid_until'], room_policy['exposure']['valid_until'],
                           f['valid_until'] if assessed else room_policy['valid_until'])
        domains = []
        for i, row in enumerate(original['domains']):
            available = assessed and (i == 7 or f['ambient'][i].get('assessment_available') is not False)
            if not available:
                need(row['status'] == 'unavailable' and row['sample'] is None,
                     'unassessed_room_domain_cannot_supply_command')
            until = min(policy_until, f['ambient'][i]['valid_until']) if i < 7 and available else policy_until
            domains.append(_domain(row, now, until))
        changes, logical = [], [None] * 21
        for zone, d in enumerate(domains[:7]):
            if d['status'] != 'sampled':
                continue
            try:
                q = quantize_ambient_sample(d['original']['sample'])
                mixed = provider.ambient_mix(q['level'], q['mired'])
                need(type(mixed) is list and len(mixed) == 3
                     and all(type(v) is int and 0 <= v <= 4096 for v in mixed)
                     and sum(mixed) <= 4096, 'canonical_mixer_return_shape')
            except RoomProjectionRefusal as exc:
                d.update(status='refused', reason=str(exc))
                continue
            d['intent'] = dict(zone=zone, level=q['level'], mired=q['mired'],
                logical_wnc=deepcopy(mixed), quantization=q['quantization'])
            logical[zone * 3:zone * 3 + 3] = mixed
            changes.append(dict(zone=zone, level=q['level'], mired=q['mired']))
        spot = domains[7]
        # Even a sampled-dark virtual task does not establish a supported
        # spotlight emitter/CCT map, nor an explicit native withdrawal command.
        if spot['status'] == 'sampled':
            spot.update(status='refused', reason='spotlight_semantic_CCT_mapping_unavailable')
        selected_until = [d['support_until'] for d in domains[:7] if d['intent'] is not None]
        fixtures.append(dict(fixture_id=f['fixture_id'], mount_id=f['mount_id'],
            profile_ref=deepcopy(f['profile_ref']), pose_ref=deepcopy(f['pose_ref']), domains=domains,
            ambient=dict(status='complete' if len(changes) == 7 else 'partial' if changes else 'unavailable',
                changes=changes, domain_mask=sum(1 << c['zone'] for c in changes),
                logical_channels=logical, support_until=min(selected_until) if selected_until else None,
                full24_power_checked=False, preserve_unselected_from_current_native=True),
            task=_task(snapshot, room_policy, f, _domain(original['domains'][7], now, policy_until), task_profile=task_profile)))
    return dict(schema='home-room-command-projection-2' if task_mode else SCHEMA, version=2 if task_mode else 1,
        room_commit_ref=deepcopy(snapshot['room_commit_ref']),
        home_scope=deepcopy(snapshot['home_scope']), sample_at=t, checked_at=now,
        input_cut=deepcopy(snapshot['decision']['input_cut']),
        room_policy_sha256=digest(raw_policy), registration=deepcopy(room_policy['registration']),
        authority_generation=output['authority_generation'], source_generations=deepcopy(output['source_generations']),
        provider_pins=admitted_provider_pins, mixer_source_sha256=MIXER_SOURCE_SHA256, fixtures=fixtures,
        authority_created=False, physical_permission=False, native_achieved_truth=False,
        downstream_current_commit_and_native_validation_required=True)


class RoomCommandProjector:
    def __init__(self, source, *, room_policy, provider, task_profile=None):
        need(type(source) is CommittedRoomSource, 'actual_committed_room_source_required')
        mode = retained_task_profile(task_profile)
        if not mode:
            _refuse_subset_policy(room_policy)
        _provider(provider)
        validate_room_policy(room_policy, allow_assessed_subset=mode)
        need(not mode or room_policy['version'] == 2, 'retained_task_room_policy2_required')
        self.source, self.policy, self.provider = source, deepcopy(room_policy), provider
        self.task_profile = deepcopy(task_profile)
        self.policy_sha256 = digest(self.policy)
        self._installed()

    def _installed(self):
        with self.source.journal.lock:
            worker = self.source._installation()
            if retained_task_profile(self.task_profile):
                config = self.source.journal.dynamic.config
                need(config.get('schema') == 'home-live-ingress-config-13'
                     and type(config.get('version')) is int and config['version'] == 13
                     and config['manifest'].get('schema') == 'home-live-ingress-manifest-13'
                     and type(config['manifest'].get('version')) is int and config['manifest']['version'] == 13
                     and worker.worker_version == 'home-live-context-worker-12.0.0',
                     'retained_task_config13_worker12_required')
            need(digest(self.policy) == self.policy_sha256
                 and digest(worker.policy['room_policy']) == self.policy_sha256,
                 'installed_room_projection_policy_changed')

    def project(self):
        self._installed()
        first = self.source.current()
        # Never assemble four independent source reads into one fictional cut.
        result = project_committed_snapshot(first, room_policy=self.policy, provider=self.provider, task_profile=self.task_profile)
        self._installed()
        final = self.source.current()
        need(final['room_commit_ref'] == first['room_commit_ref'], 'room_commit_changed_during_projection')
        need(final['checked_at'] >= first['checked_at'], 'room_projection_clock_rollback')
        # The mixer may consume time. Do not call it again after the final
        # clock check; remove expired selections from the already computed cut.
        result['checked_at'] = final['checked_at']
        for f in result['fixtures']:
            for d in f['domains']:
                if d['original']['sample'] is not None and not final['checked_at'] < d['support_until']:
                    d.update(status='unavailable', reason='committed_sample_expired', intent=None)
            changes = []
            logical = [None] * 21
            for zone, d in enumerate(f['domains'][:7]):
                if d['intent'] is not None:
                    i = d['intent']
                    changes.append(dict(zone=zone, level=i['level'], mired=i['mired']))
                    logical[zone * 3:zone * 3 + 3] = i['logical_wnc']
            f['ambient'].update(status='complete' if len(changes) == 7 else 'partial' if changes else 'unavailable',
                changes=changes, domain_mask=sum(1 << c['zone'] for c in changes), logical_channels=logical,
                support_until=min(d['support_until'] for d in f['domains'][:7] if d['intent'] is not None) if changes else None)
            motion = f['task']['retained_motion']
            if motion is not None and not final['checked_at'] < motion['support_until']:
                f['task'].update(status='unavailable', reason='committed_task_expired', retained_motion=None,
                                 sampled_virtual_readback=None)
        return result
