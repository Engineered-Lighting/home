"""Independent admission of finite modeled room output. No I/O or physical grant.

Original publications and the trusted preceding checkpoint are explicit inputs.
This code does not import or call the JavaScript room controller.
"""
from __future__ import annotations

import copy
import math
import re

from journal import JournalError, bounded_json, canonical, digest, finite
from live_ingress_validation import _registration
from live_delivery import (validate_conservative_delivery_worker_policy,
                           _validate_conservative_domains, _validate_conservative_surface,
                           validate_delivery_policy)
from live_scene import validate_scene_worker_grant

VERSION = 'home-live-room-allocation-1.0.0'
DOMAINS = ('radial1', 'radial2', 'radial3', 'radial4', 'radial5', 'radial6', 'downward')
SCOPE = ('site_id', 'room_id', 'session_id', 'domain_epoch')
ROOM_SCHEMAS = frozenset({'home-live-shadow-decision-8', 'home-live-worker-checkpoint-8',
                          'home-live-worker-commit-8', 'home-live-input-cut-8',
                          'home-live-room-output-1', 'home-live-room-snapshot-1'})
ROOM_OUTPUTS = frozenset({'home-live-engagement-state-1', 'home-live-shadow-decision-8'})
EPS = 1e-10


def _exact(value, keys, message='closed room contract required'):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(message)


def _id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value) is not None


def _pin(value):
    return (isinstance(value, dict) and set(value) == {'id', 'digest'} and _id(value['id'])
            and isinstance(value['digest'], str) and re.fullmatch('[a-f0-9]{64}', value['digest']) is not None)


def _number(value, low=0, high=math.inf, positive=False):
    return finite(value) and low <= value <= high and (not positive or value > low)


def _vector(value):
    return isinstance(value, list) and len(value) == 3 and all(finite(x) for x in value)


def validate_room_policy(policy, sources=None, scene_policy=None, *, allow_assessed_subset=False):
    if isinstance(policy, dict) and (policy.get('schema') == 'home-live-room-policy-2' or policy.get('version') == 2):
        if allow_assessed_subset is not True:
            raise ValueError('assessed subset requires explicit successor')
        return _validate_room_subset_policy(policy, sources, scene_policy)
    _exact(policy, {'schema', 'version', 'policy_ref', 'registration', 'inventory_ref', 'valid_until',
                    'ambient_timing', 'exposure', 'fixtures'})
    if (policy['schema'] != 'home-live-room-policy-1' or type(policy['version']) is not int or policy['version'] != 1
            or not _pin(policy['policy_ref']) or not _pin(policy['inventory_ref']) or not _number(policy['valid_until'], positive=True)):
        raise ValueError('explicit assessed room policy1 required')
    try:
        _registration(policy['registration'])
    except JournalError as error:
        raise ValueError('exact room registration required') from error
    registration = policy['registration']
    if (len(set(registration['coverage_refs'])) != len(registration['coverage_refs'])
            or len({canonical(x) for x in registration['transform_chain_refs']}) != len(registration['transform_chain_refs'])):
        raise ValueError('unique room registration pins required')
    timing = policy['ambient_timing']
    _exact(timing, {'fade_in_s', 'vacancy_delay_s', 'fade_out_s', 'source_loss_hold_s'})
    if any(not _number(x, high=30) for x in timing.values()) or timing['source_loss_hold_s'] != 0:
        raise ValueError('bounded ambient timing without stale source renewal required')
    exposure = policy['exposure']
    _exact(exposure, {'assessment_ref', 'reference_ref', 'unit', 'max_total', 'valid_until'})
    if (not _pin(exposure['assessment_ref']) or not _pin(exposure['reference_ref'])
            or exposure['unit'] != 'normalized_modeled_exposure' or not _number(exposure['max_total'])
            or not _number(exposure['valid_until'], positive=True)):
        raise ValueError('one common assessed exposure reference required')
    fixtures = policy['fixtures']
    if not isinstance(fixtures, list) or len(fixtures) != 4:
        raise ValueError('exact four room fixtures required')
    tasks = []
    for fixture in fixtures:
        _exact(fixture, {'fixture_id', 'mount_id', 'profile_ref', 'pose_ref', 'valid_until', 'max_power_w',
                         'spot_power_w_at_full', 'spot_exposure_upper_at_full', 'task_policy', 'ambient'})
        if (not _id(fixture['fixture_id']) or not _id(fixture['mount_id']) or not _pin(fixture['profile_ref'])
                or not _pin(fixture['pose_ref']) or not _number(fixture['valid_until'], positive=True)
                or not _number(fixture['max_power_w'], positive=True)
                or not _number(fixture['spot_power_w_at_full']) or not _number(fixture['spot_exposure_upper_at_full'])):
            raise ValueError('finite assessed room fixture required')
        ambient = fixture['ambient']
        if not isinstance(ambient, list) or len(ambient) != 7:
            raise ValueError('seven ordered ambient domains required')
        for index, row in enumerate(ambient):
            _exact(row, {'domain', 'origin_m', 'direction_unit', 'half_angle_rad', 'range_m', 'assessment_ref',
                         'valid_until', 'level_scale', 'max_fraction', 'cct_min_k', 'cct_max_k',
                         'power_w_at_full', 'exposure_upper_at_full'})
            if (row['domain'] != DOMAINS[index] or not _vector(row['origin_m']) or not _vector(row['direction_unit'])
                    or abs(math.hypot(*row['direction_unit']) - 1) > 1e-8
                    or not _number(row['half_angle_rad'], high=math.pi / 2, positive=True) or row['half_angle_rad'] == math.pi / 2
                    or not _number(row['range_m'], high=100, positive=True) or not _pin(row['assessment_ref'])
                    or not _number(row['valid_until'], positive=True) or not _number(row['level_scale'], high=1)
                    or not _number(row['max_fraction'], high=1) or not _number(row['cct_min_k'], low=1500, high=6500)
                    or not _number(row['cct_max_k'], low=row['cct_min_k'], high=6500)
                    or not _number(row['power_w_at_full']) or not _number(row['exposure_upper_at_full'])):
                raise ValueError('finite assessed ambient emission required')
        task = fixture['task_policy']
        if task is not None:
            if (task.get('schema') != 'home-live-task-delivery-policy-4' or type(task.get('version')) is not int
                    or task['version'] != 4 or task.get('fixture', {}).get('id') != fixture['fixture_id']
                    or task.get('fixture', {}).get('fixture_pose_ref') != fixture['pose_ref']
                    or task.get('registration') != registration):
                raise ValueError('exact pinned conservative room candidate required')
            try:
                _validate_conservative_domains(task)
                _validate_conservative_surface(task)
                base = copy.deepcopy(task)
                base.update(schema='home-live-task-delivery-policy-2', version=2)
                for key in ('modeled_emission', 'observation_coverage', 'brightness_transition_s'):
                    del base[key]
                scene = scene_policy or {'catalog': {'examples': [{'requirements_ref': base['requirements_ref'], 'nominal': {'fixture_family_ref': base['fixture']['family_ref']}}]}}
                validate_delivery_policy(base, sources or {'authored': {'registration': registration}}, scene, allow_stable=True)
            except JournalError as error:
                raise ValueError('invalid conservative room candidate') from error
            tasks.append(task)
    if len({f['fixture_id'] for f in fixtures}) != 4 or len({f['mount_id'] for f in fixtures}) != 4:
        raise ValueError('distinct fixture mount identities required')
    if len(tasks) not in (0, 4) or any(task['room'] != tasks[0]['room'] for task in tasks):
        raise ValueError('zero or four same-room conservative task candidates required')
    bounded_json(policy, 524288)


def _validate_room_subset_policy(policy, sources, scene_policy):
    _exact(policy, {'schema', 'version', 'policy_ref', 'registration', 'inventory_ref', 'valid_until',
                    'ambient_timing', 'exposure', 'fixtures'})
    if (policy['schema'] != 'home-live-room-policy-2' or type(policy['version']) is not int or policy['version'] != 2
            or not _pin(policy['policy_ref']) or not _pin(policy['inventory_ref']) or not _number(policy['valid_until'], positive=True)):
        raise ValueError('closed assessed subset policy2 required')
    try:
        _registration(policy['registration'])
    except JournalError as error:
        raise ValueError('exact room registration required') from error
    r = policy['registration']
    if len(set(r['coverage_refs'])) != len(r['coverage_refs']) or len({canonical(x) for x in r['transform_chain_refs']}) != len(r['transform_chain_refs']):
        raise ValueError('unique room registration pins required')
    timing, exposure = policy['ambient_timing'], policy['exposure']
    _exact(timing, {'fade_in_s', 'vacancy_delay_s', 'fade_out_s', 'source_loss_hold_s'})
    if any(not _number(x, high=30) for x in timing.values()) or timing['source_loss_hold_s'] != 0:
        raise ValueError('bounded ambient timing without stale source renewal required')
    _exact(exposure, {'assessment_ref', 'reference_ref', 'unit', 'max_total', 'valid_until'})
    if (not _pin(exposure['assessment_ref']) or not _pin(exposure['reference_ref'])
            or exposure['unit'] != 'normalized_modeled_exposure' or not _number(exposure['max_total'])
            or not _number(exposure['valid_until'], positive=True)):
        raise ValueError('one common assessed exposure reference required')
    fixtures, tasks = policy['fixtures'], []
    if not isinstance(fixtures, list) or len(fixtures) != 4:
        raise ValueError('exact four room fixtures required')
    for fixture in fixtures:
        _exact(fixture, {'fixture_id', 'mount_id', 'profile_ref', 'assessment'})
        if not _id(fixture['fixture_id']) or not _id(fixture['mount_id']) or not _pin(fixture['profile_ref']):
            raise ValueError('exact subset inventory identity required')
        a = fixture['assessment']
        if a is None:
            continue
        _exact(a, {'pose_ref', 'valid_until', 'max_power_w', 'task', 'ambient'})
        if not _pin(a['pose_ref']) or not _number(a['valid_until'], positive=True) or not _number(a['max_power_w'], positive=True):
            raise ValueError('complete subset fixture assessment required')
        if a['task'] is not None:
            task_row = a['task']
            _exact(task_row, {'delivery_policy', 'power_w_at_full', 'exposure_upper_at_full'})
            if not _number(task_row['power_w_at_full']) or not _number(task_row['exposure_upper_at_full']):
                raise ValueError('assessed task emission required')
            task = task_row['delivery_policy']
            if (not isinstance(task, dict) or task.get('schema') != 'home-live-task-delivery-policy-4'
                    or type(task.get('version')) is not int or task['version'] != 4
                    or task.get('fixture', {}).get('id') != fixture['fixture_id']
                    or task.get('fixture', {}).get('fixture_pose_ref') != a['pose_ref'] or task.get('registration') != r):
                raise ValueError('exact pinned conservative room candidate required')
            try:
                _validate_conservative_domains(task)
                _validate_conservative_surface(task)
                base = copy.deepcopy(task)
                base.update(schema='home-live-task-delivery-policy-2', version=2)
                for key in ('modeled_emission', 'observation_coverage', 'brightness_transition_s'):
                    del base[key]
                scene = scene_policy or {'catalog': {'examples': [{'requirements_ref': base['requirements_ref'], 'nominal': {'fixture_family_ref': base['fixture']['family_ref']}}]}}
                validate_delivery_policy(base, sources or {'authored': {'registration': r}}, scene, allow_stable=True)
            except JournalError as error:
                raise ValueError('invalid conservative room candidate') from error
            tasks.append(task)
        if not isinstance(a['ambient'], list) or len(a['ambient']) != 7:
            raise ValueError('seven ordered subset ambient slots required')
        count = 0
        for index, slot in enumerate(a['ambient']):
            _exact(slot, {'domain', 'assessment'})
            if slot['domain'] != DOMAINS[index]:
                raise ValueError('ordered subset ambient identity required')
            row = slot['assessment']
            if row is None:
                continue
            count += 1
            _exact(row, {'origin_m', 'direction_unit', 'half_angle_rad', 'range_m', 'assessment_ref', 'valid_until',
                         'level_scale', 'max_fraction', 'cct_min_k', 'cct_max_k', 'power_w_at_full', 'exposure_upper_at_full'})
            if (not _vector(row['origin_m']) or not _vector(row['direction_unit']) or abs(math.hypot(*row['direction_unit']) - 1) > 1e-8
                    or not _number(row['half_angle_rad'], high=math.pi / 2, positive=True) or row['half_angle_rad'] == math.pi / 2
                    or not _number(row['range_m'], high=100, positive=True) or not _pin(row['assessment_ref'])
                    or not _number(row['valid_until'], positive=True) or not _number(row['level_scale'], high=1)
                    or not _number(row['max_fraction'], high=1) or not _number(row['cct_min_k'], low=1500, high=6500)
                    or not _number(row['cct_max_k'], low=row['cct_min_k'], high=6500)
                    or not _number(row['power_w_at_full']) or not _number(row['exposure_upper_at_full'])):
                raise ValueError('finite assessed ambient emission required')
        if a['task'] is None and count == 0:
            raise ValueError('empty fixture assessment must be unavailable')
    if len({f['fixture_id'] for f in fixtures}) != 4 or len({f['mount_id'] for f in fixtures}) != 4:
        raise ValueError('distinct fixture mount identities required')
    if tasks and any(task['room'] != tasks[0]['room'] for task in tasks):
        raise ValueError('same-room conservative task candidates required')
    bounded_json(policy, 524288)


def room_policy_view(policy, sources=None, scene_policy=None, *, allow_assessed_subset=False):
    """Private execution view; absent assessments keep no numeric coefficients."""
    validate_room_policy(policy, sources, scene_policy, allow_assessed_subset=allow_assessed_subset)
    if policy['version'] == 1:
        return copy.deepcopy(policy)
    result = copy.deepcopy(policy)
    rows = []
    for fixture in policy['fixtures']:
        a = fixture['assessment']
        task = a['task'] if a is not None else None
        rows.append(dict(fixture_id=fixture['fixture_id'], mount_id=fixture['mount_id'], profile_ref=copy.deepcopy(fixture['profile_ref']),
            assessment_available=a is not None, pose_ref=copy.deepcopy(a['pose_ref']) if a is not None else None,
            valid_until=a['valid_until'] if a is not None else None, max_power_w=a['max_power_w'] if a is not None else None,
            task_policy=copy.deepcopy(task['delivery_policy']) if task is not None else None,
            spot_power_w_at_full=task['power_w_at_full'] if task is not None else None,
            spot_exposure_upper_at_full=task['exposure_upper_at_full'] if task is not None else None,
            ambient=[dict(domain=domain, assessment_available=a is not None and a['ambient'][i]['assessment'] is not None,
                **(copy.deepcopy(a['ambient'][i]['assessment']) if a is not None and a['ambient'][i]['assessment'] is not None else {}))
                for i, domain in enumerate(DOMAINS)]))
    result['fixtures'] = rows
    return result


def validate_room_worker_policy(manifest, registry, *, allow_assessed_subset=False):
    worker = manifest['worker']
    if (not getattr(registry, 'room_interface_digest', None)
            or manifest.get('room_interface_digest') != registry.room_interface_digest
            or worker.get('room_schema_pins') != registry.room_schema_pins):
        raise ValueError('exact additive room interface and schema pins required')
    view = room_policy_view(worker['room_policy'], manifest['sources'], worker['scene_policy'], allow_assessed_subset=allow_assessed_subset)
    tasks = [fixture['task_policy'] for fixture in view['fixtures'] if fixture['task_policy'] is not None]
    if tasks and (worker['delivery_policy']['room'] != tasks[0]['room']
                  or worker['lifecycle_policy'].get('operator_clearance') is None
                  or worker['lifecycle_policy']['operator_clearance']['room_assessment_ref'] != tasks[0]['room']['assessment_ref']):
        raise ValueError('room candidates require exact fallback room and clearance assessment')
    base = copy.deepcopy(manifest)
    del base['room_interface_digest']
    del base['worker']['room_schema_pins']
    del base['worker']['room_policy']
    validate_conservative_delivery_worker_policy(base, registry)


def validate_room_grant(grant, config):
    if grant.payload_types & (ROOM_SCHEMAS - ROOM_OUTPUTS) and 'worker_commit' not in grant.operations:
        raise ValueError('private room state requires worker capability')
    if 'home-live-shadow-decision-8' in grant.payload_types and 'home-live-geometry-1' not in grant.payload_types:
        raise ValueError('complete room decision requires separate geometry authority')


def validate_room_worker_grant(grant, config):
    validate_scene_worker_grant(grant, config)
    if not ROOM_SCHEMAS | {'home-live-intent-event-2', 'home-live-person-lifecycle-1'} <= grant.payload_types:
        raise ValueError('room worker requires complete explicit successor closure')


def _close(actual, expected, message):
    if finite(actual) and finite(expected):
        if abs(actual - expected) > EPS:
            raise ValueError(message)
    elif canonical(actual) != canonical(expected):
        raise ValueError(message)


def _presence_horizon(background, publications, identity, t, source_generations):
    refs = background['evidence_refs']
    if not isinstance(refs, list) or not refs or len(refs) > 128 or len({canonical(x) for x in refs}) != len(refs):
        raise ValueError('exact original ambient presence proofs required')
    horizons = []
    for reference in refs:
        matches = [row for row in publications if row.get('publication_ref') == reference]
        if len(matches) != 1:
            raise ValueError('ambient proof original publication required')
        row = matches[0]
        payload = row['payload']
        _exact(reference, {'stream_epoch', 'cursor', 'producer_id', 'id', 'payload_digest', 'component_id'})
        if (reference['id'] != payload.get('id') or reference['component_id'] is not None
                or reference['payload_digest'] != digest(payload)
                or payload.get('schema') != 'home-live-fact-1' or payload.get('version') != 1
                or any(payload.get(key) != identity[key] for key in SCOPE)
                or payload.get('status') != 'supplied' or payload.get('kind') != 'person_presence'
                or payload.get('value') != 'present' or payload.get('association', {}).get('person_status') != 'qualified'
                or not isinstance(payload.get('person_ref'), dict) or not _pin(payload.get('method_ref'))
                or payload.get('source_generation') != source_generations.get(payload.get('source_id'))
                or not _number(payload.get('observed_at')) or payload['observed_at'] > t
                or not _number(row.get('available_at')) or row['available_at'] > t
                or row['available_at'] < payload.get('available_at', math.inf)
                or not _number(row.get('valid_until')) or row['valid_until'] <= t
                or row['valid_until'] > payload.get('valid_until', -math.inf)):
            raise ValueError('fresh scoped original ambient presence required')
        horizons.append(row['valid_until'])
    return min(horizons)


def _subset_vacancy_anchor(policy, prior, t):
    """Fixed policy/applicability order; never select a proposed/positive row."""
    rows = [prior['ambient'][i*7+j] for i, fixture in enumerate(policy['fixtures'])
            for j, domain in enumerate(fixture['ambient']) if domain.get('assessment_available') is not False
            and t < min(policy['valid_until'], policy['exposure']['valid_until'], fixture['valid_until'], domain['valid_until'])]
    anchor = rows[0] if rows else None
    carried = [row for row in rows if row['goal_id'] is not None and row['absence_since'] is not None and row['last_valid_until'] > t]
    if carried and (anchor is None or anchor['absence_since'] is None or anchor['last_valid_until'] <= t
                    or any(row['absence_since'] != anchor['absence_since'] or row['basis_refs'] != anchor['basis_refs'] for row in carried)):
        raise ValueError('room vacancy checkpoint divergence')
    return anchor


def _vacancy_proof(prior, publications, identity, t, source_generations, lifecycle_policy, changed, *, subset_policy=None):
    if prior is None or changed:
        return None
    def fresh(row):
        p = row.get('payload', {})
        return (all(p.get(key) == identity[key] for key in SCOPE)
                and p.get('source_generation') == source_generations.get(p.get('source_id'))
                and _number(p.get('observed_at')) and p['observed_at'] <= t
                and _number(row.get('available_at')) and p.get('available_at', math.inf) <= row['available_at'] <= t
                and _number(row.get('valid_until')) and t < row['valid_until'] <= p.get('valid_until', -math.inf))
    if any(fresh(row) and row['payload'].get('status') == 'supplied'
           and (row['payload'].get('kind') == 'person_presence' and row['payload'].get('value') == 'present'
                or row['payload'].get('schema') == 'home-live-geometry-1' and row['payload'].get('person_ref'))
           for row in publications):
        return None
    methods = (lifecycle_policy or {}).get('absence_methods', [])
    qualified = []
    for row in publications:
        p, ref = row.get('payload', {}), row.get('publication_ref', {})
        if (fresh(row) and p.get('schema') == 'home-live-person-lifecycle-1' and p.get('version') == 1
                and p.get('disposition') in {'absence_qualified', 'departure_qualified'}
                and _number(p.get('clock', {}).get('uncertainty_s')) and p.get('basis_refs')
                and ref.get('id') == p.get('id') and ref.get('payload_digest') == digest(p) and ref.get('component_id') is None
                and any(all(p.get(key) == method.get(key) for key in ('method_ref', 'qualification_ref', 'applicability_ref', 'coverage_assessment_ref')) for method in methods)):
            qualified.append(row)
    shared = (prior.get('fallback_snapshot') or {}).get('state')
    if shared is None and prior.get('tasks'):
        shared = prior['tasks'][0]['snapshot']['state']
    shared = shared or {}
    anchor = _subset_vacancy_anchor(subset_policy, prior, t) if subset_policy is not None else prior['ambient'][0]
    continuing = anchor is not None and anchor['absence_since'] is not None and (subset_policy is None or anchor['last_valid_until'] > t)
    if continuing:
        proofs = [next((row for row in qualified if row['publication_ref'] == ref), None) for ref in anchor['basis_refs']]
    else:
        proofs = [next((row for row in qualified if row['payload'].get('target_person_ref') == person
                        and row['payload']['observed_at'] - row['payload']['clock']['uncertainty_s']
                        > shared.get('person_observed_through', {}).get(digest(person), math.inf)), None)
                  for person in shared.get('known_persons', [])]
    if not proofs or any(row is None for row in proofs):
        return None
    unique = {row['payload']['id']: row for row in proofs}
    proofs = list(unique.values())
    return dict(refs=[row['publication_ref'] for row in proofs], valid_until=min(row['valid_until'] for row in proofs),
                since=anchor['absence_since'] if continuing else t)


def validate_room_output(*, policy, identity, t, seq, input_cut_digest, authority_generation,
                         source_generations, background, room_output, room_snapshot,
                         prior_room_snapshot, publications, max_output_age_s=3, lifecycle_policy=None, allow_assessed_subset=False):
    """Reject forged samples using original proof horizons and prior committed ramps.

    The caller first validates the existing scene/background and selected task
    through their unchanged independent boundaries. Proposed room state is not
    used as its own transition origin.
    """
    validate_room_policy(policy, allow_assessed_subset=allow_assessed_subset)
    policy_digest = digest(policy)
    subset = policy['version'] == 2
    policy = room_policy_view(policy, allow_assessed_subset=allow_assessed_subset)
    _exact(room_output, {'schema', 'version', 'revision_id', 'policy_ref', 'input_cut_digest', 'sample_at',
                         'authority_generation', 'source_generations', 'fixtures', 'task', 'task_candidates'})
    _exact(room_snapshot, {'schema', 'version', 'identity', 'policy_digest', 't', 'seq', 'authority_generation',
                           'source_generations', 'ambient', 'tasks', 'fallback_snapshot', 'last_selected_fixture_id'})
    if (not _number(t) or type(seq) is not int or seq < 0 or not _number(max_output_age_s, high=3, positive=True)
            or room_output['schema'] != 'home-live-room-output-1' or room_output['version'] != 1
            or room_output['policy_ref'] != policy['policy_ref'] or room_output['input_cut_digest'] != input_cut_digest
            or room_output['sample_at'] != t or room_output['authority_generation'] != authority_generation
            or room_output['source_generations'] != source_generations
            or room_snapshot['schema'] != 'home-live-room-snapshot-1' or room_snapshot['version'] != 1
            or room_snapshot['identity'] != identity or room_snapshot['policy_digest'] != policy_digest
            or room_snapshot['t'] != t or room_snapshot['seq'] != seq
            or room_snapshot['authority_generation'] != authority_generation or room_snapshot['source_generations'] != source_generations):
        raise ValueError('exact causal room output and checkpoint required')
    if not isinstance(room_snapshot['ambient'], list) or len(room_snapshot['ambient']) != 28:
        raise ValueError('complete ambient transition checkpoint required')
    prior = prior_room_snapshot
    if prior is not None and (prior.get('identity') != identity or prior.get('policy_digest') != policy_digest
                              or prior.get('t') is not None and (prior['t'] >= t or prior['seq'] >= seq)):
        raise ValueError('trusted causal preceding room checkpoint required')
    changed = (prior is None or prior.get('authority_generation') != authority_generation
               or prior.get('source_generations') != source_generations)
    requested = background.get('status') == 'requested' and background.get('kind') == 'illuminate_presence' and background.get('valid_until', 0) > t
    horizon = _presence_horizon(background, publications, identity, t, source_generations) if requested else t
    assessed_ambient = [row for fixture in policy['fixtures'] for row in fixture['ambient'] if row.get('assessment_available') is not False]
    fit = requested and bool(assessed_ambient) and all(background['nominal']['brightness_fraction'] * row['level_scale'] <= row['max_fraction']
                            and row['cct_min_k'] <= background['nominal']['cct_kelvin'] <= row['cct_max_k']
                            for row in assessed_ambient)
    vacancy = _vacancy_proof(prior, publications, identity, t, source_generations, lifecycle_policy, changed,
                             subset_policy=policy if subset else None) if not requested else None
    refusal = None
    exposure = 0
    powers = {}
    uppers = {}
    for fixture in policy['fixtures']:
        power = 0
        for domain in fixture['ambient']:
            key = (fixture['fixture_id'], domain['domain'])
            old = next((x for x in prior.get('ambient', []) if (x['fixture_id'], x['domain']) == key), None) if prior else None
            if domain.get('assessment_available') is False:
                if old and (old['goal_id'] is not None or any(old[k] != 0 for k in ('from_fraction', 'to_fraction', 'last_fraction'))
                            or old['last_valid_until'] > t):
                    raise ValueError('unassessed domain cannot carry an active transition')
                continue
            target = background['nominal']['brightness_fraction'] * domain['level_scale'] if fit else 0
            upper = max(target, old['last_fraction'] if old and not changed and old['last_valid_until'] > t else 0) if fit or vacancy else 0
            uppers[key] = upper
            power += upper * domain['power_w_at_full']
            exposure += upper * domain['exposure_upper_at_full']
        if fixture.get('assessment_available') is not False and power > fixture['max_power_w'] + EPS:
            refusal = 'ambient_fixture_power_exceeded'
        powers[fixture['fixture_id']] = power
    if exposure > policy['exposure']['max_total'] + EPS:
        refusal = 'ambient_aggregate_exposure_exceeded'
    if not isinstance(room_output['fixtures'], list) or len(room_output['fixtures']) != 4:
        raise ValueError('complete four fixture room output required')
    for index, fixture in enumerate(policy['fixtures']):
        actual = room_output['fixtures'][index]
        _exact(actual, {'fixture_id', 'mount_id', 'profile_ref', 'domains'})
        if any(actual[key] != fixture[key] for key in ('fixture_id', 'mount_id', 'profile_ref')) or not isinstance(actual['domains'], list) or len(actual['domains']) != 8:
            raise ValueError('exact commissioned room fixture identity required')
        for offset, domain in enumerate(fixture['ambient']):
            row = actual['domains'][offset]
            _exact(row, {'domain', 'status', 'reason', 'goal_id', 'scene_id', 'desired', 'sample', 'valid_until', 'evidence_refs', 'assessment_refs'})
            if row['domain'] != domain['domain'] or row['scene_id'] is not None:
                raise ValueError('exact ambient domain identity required')
            old = next((x for x in prior.get('ambient', []) if x['fixture_id'] == fixture['fixture_id'] and x['domain'] == domain['domain']), None) if prior else None
            vacant = bool(vacancy and old and old['goal_id'] is not None and old['last_valid_until'] > t)
            support_until = vacancy['valid_until'] if vacant else min(background.get('valid_until', t), horizon)
            assessed = domain.get('assessment_available') is not False
            until = min(t + max_output_age_s, support_until, policy['valid_until'],
                        policy['exposure']['valid_until'], fixture['valid_until'], domain['valid_until']) if assessed and (fit or vacant) else t
            enabled = assessed and (fit or vacant) and not refusal and until > t
            checkpoint = room_snapshot['ambient'][index * 7 + offset]
            _exact(checkpoint, {'fixture_id', 'domain', 'goal_id', 'cct_kelvin', 'from_fraction', 'to_fraction', 'started_at', 'duration_s',
                                'last_sample_at', 'last_fraction', 'last_valid_until', 'absence_since', 'basis_refs'})
            if checkpoint['fixture_id'] != fixture['fixture_id'] or checkpoint['domain'] != domain['domain']:
                raise ValueError('exact ambient transition identity required')
            if enabled:
                target = 0 if vacant else background['nominal']['brightness_fraction'] * domain['level_scale']
                goal_id = old['goal_id'] if vacant else background['goal_id']
                cct = old['cct_kelvin'] if vacant else background['nominal']['cct_kelvin']
                refs = vacancy['refs'] if vacant else background['evidence_refs']
                continuous = old and not changed and old['last_valid_until'] > t
                retained = old['absence_since'] is not None if vacant else continuous and old['absence_since'] is None and old['goal_id'] == goal_id and old['to_fraction'] == target
                start = old['started_at'] if retained else vacancy['since'] + policy['ambient_timing']['vacancy_delay_s'] if vacant else t
                duration = old['duration_s'] if retained else policy['ambient_timing']['fade_out_s' if vacant else 'fade_in_s']
                origin = old['from_fraction'] if retained else old['last_fraction'] if continuous else 0
                progress = 1 if duration == 0 else max(0, min(1, (t - start) / duration))
                level = origin + (target - origin) * progress
                if (row['status'] != 'modeled' or row['reason'] != ('qualified_modeled_vacancy' if vacant else 'qualified_modeled_presence') or row['goal_id'] != goal_id
                        or row['evidence_refs'] != refs
                        or row['assessment_refs'] != [domain['assessment_ref'], policy['exposure']['assessment_ref']]
                        or row['desired'] != {'brightness_fraction': target, 'cct_kelvin': cct}):
                    raise ValueError('exact independently supported ambient target required')
                _exact(row['sample'], {'brightness_fraction', 'cct_kelvin'})
                _close(row['sample']['brightness_fraction'], level, 'ambient sampled level does not match trusted transition')
                _close(row['sample']['cct_kelvin'], cct, 'ambient sampled CCT mismatch')
                _close(row['valid_until'], until, 'ambient sample lease exceeds original support')
                expected = dict(fixture_id=fixture['fixture_id'], domain=domain['domain'], goal_id=goal_id, cct_kelvin=cct,
                                from_fraction=origin, to_fraction=target, started_at=start, duration_s=duration,
                                last_sample_at=t, last_fraction=level, last_valid_until=until, absence_since=vacancy['since'] if vacant else None,
                                basis_refs=refs)
            else:
                if (row['status'] != 'unavailable' or row['sample'] is not None or row['desired'] is not None
                        or row['goal_id'] is not None or row['evidence_refs'] or row['assessment_refs'] or row['valid_until'] != t):
                    raise ValueError('unqualified ambient must withdraw without a sample')
                if not assessed and row['reason'] != 'domain_assessment_unavailable':
                    raise ValueError('unassessed ambient requires explicit unavailable reason')
                expected = dict(fixture_id=fixture['fixture_id'], domain=domain['domain'], goal_id=None, cct_kelvin=None, from_fraction=0,
                                to_fraction=0, started_at=0, duration_s=0, last_sample_at=t, last_fraction=0,
                                last_valid_until=t, absence_since=None, basis_refs=[])
            for key, value in expected.items():
                _close(checkpoint[key], value, 'ambient checkpoint does not match independently sampled transition')
    task = room_output['task']
    _exact(task, {'fixture_id', 'allocation', 'execution', 'display_lease'}, 'exact selected spotlight task linkage required')
    commissioned = [fixture for fixture in policy['fixtures'] if fixture['task_policy'] is not None]
    if (not isinstance(room_snapshot['tasks'], list) or len(room_snapshot['tasks']) != len(commissioned)
            or [row.get('fixture_id') for row in room_snapshot['tasks']] != [fixture['fixture_id'] for fixture in commissioned]
            or room_snapshot['last_selected_fixture_id'] != task['fixture_id']
            or not isinstance(room_output['task_candidates'], list)
            or len(room_output['task_candidates']) != len(commissioned)
            or [row.get('fixture_id') for row in room_output['task_candidates']] != [fixture['fixture_id'] for fixture in commissioned]):
        raise ValueError('exact bounded task candidate and selected checkpoint linkage required')
    if task['fixture_id'] is None:
        if (task['allocation'].get('status') != 'unavailable' or task['execution'].get('status') != 'unavailable'
                or task['display_lease'].get('status') != 'withdrawn'):
            raise ValueError('unselected spotlight must have withdrawn task authority')
        for fixture in room_output['fixtures']:
            spotlight = fixture['domains'][7]
            _exact(spotlight, {'domain', 'status', 'reason', 'goal_id', 'scene_id', 'desired', 'sample', 'valid_until', 'evidence_refs', 'assessment_refs'})
            if (spotlight['domain'] != 'spotlight' or spotlight['status'] not in {'unassigned', 'unavailable'}
                    or spotlight['sample'] is not None or spotlight['desired'] is not None
                    or spotlight['goal_id'] is not None or spotlight['scene_id'] is not None
                    or spotlight['valid_until'] != t or spotlight['evidence_refs'] or spotlight['assessment_refs']):
                raise ValueError('unselected spotlight cannot carry a modeled sample')
            if subset and next(f for f in policy['fixtures'] if f['fixture_id'] == fixture['fixture_id'])['task_policy'] is None and (spotlight['reason'] != 'domain_assessment_unavailable' or spotlight['status'] != 'unavailable'):
                raise ValueError('unassessed spotlight requires explicit unavailable reason')
    else:
        matches = [fixture for fixture in commissioned if fixture['fixture_id'] == task['fixture_id']]
        if len(matches) != 1:
            raise ValueError('selected spotlight requires commissioned task admission')
        fixture = matches[0]
        allocation, execution, lease = (task[key] for key in ('allocation', 'execution', 'display_lease'))
        if (allocation.get('status') != 'allocated' or execution.get('status') != 'virtual'
                or lease.get('status') != 'current' or lease.get('sample_at') != t
                or not _number(lease.get('valid_until')) or not t < lease['valid_until'] <= min(t + max_output_age_s, policy['valid_until'], policy['exposure']['valid_until'], fixture['valid_until'])
                or len(allocation.get('assignments', [])) != 1 or len(execution.get('readback', [])) != 1
                or len(allocation.get('candidates', [])) != 1):
            raise ValueError('exact selected spotlight sampled authority required')
        assignment, readback = allocation['assignments'][0], execution['readback'][0]
        selected = next(row['snapshot']['state'] for row in room_snapshot['tasks'] if row['fixture_id'] == fixture['fixture_id'])
        if (assignment.get('fixture_id') != fixture['fixture_id'] or readback.get('fixture_id') != fixture['fixture_id']
                or not selected.get('plan') or selected.get('last_readback') != readback
                or selected.get('last_valid_until') != lease['valid_until']
                or allocation['candidates'][0] != next(row for row in room_output['task_candidates'] if row['fixture_id'] == fixture['fixture_id'])):
            raise ValueError('selected spotlight readback must match its achieved candidate checkpoint')
        upper = max(assignment['nominal']['brightness_fraction'], readback['brightness_fraction'],
                    (selected.get('brightness_transition') or {}).get('from_fraction', 0))
        ambient_exposure = 0 if refusal else exposure
        ambient_power = 0 if refusal else powers[fixture['fixture_id']]
        if ambient_exposure + upper * fixture['spot_exposure_upper_at_full'] > policy['exposure']['max_total'] + EPS:
            raise ValueError('concurrent room aggregate exposure bound exceeded')
        if ambient_power + upper * fixture['spot_power_w_at_full'] > fixture['max_power_w'] + EPS:
            raise ValueError('concurrent room fixture power bound exceeded')
        for output in room_output['fixtures']:
            spotlight = output['domains'][7]
            _exact(spotlight, {'domain', 'status', 'reason', 'goal_id', 'scene_id', 'desired', 'sample', 'valid_until', 'evidence_refs', 'assessment_refs'})
            if output['fixture_id'] == fixture['fixture_id']:
                expected = dict(domain='spotlight', status='modeled', reason='qualified_modeled_task',
                                goal_id=assignment['goal_id'], scene_id=selected['plan']['scene_id'],
                                desired={key: assignment['nominal'][key] for key in ('brightness_fraction', 'cct_kelvin')},
                                sample={key: readback[key] for key in ('brightness_fraction', 'cct_kelvin')},
                                valid_until=lease['valid_until'], evidence_refs=lease['geometry_refs'],
                                assessment_refs=execution['assessment_refs'])
                if spotlight != expected:
                    raise ValueError('spotlight domain must equal independently admitted selected task readback')
            elif (spotlight['domain'] != 'spotlight' or spotlight['status'] not in {'unassigned', 'unavailable'}
                  or spotlight['sample'] is not None or spotlight['desired'] is not None or spotlight['goal_id'] is not None
                  or spotlight['scene_id'] is not None or spotlight['valid_until'] != t
                  or spotlight['evidence_refs'] or spotlight['assessment_refs']):
                raise ValueError('unselected spotlight cannot carry a modeled sample')
            if subset and next(f for f in policy['fixtures'] if f['fixture_id'] == output['fixture_id'])['task_policy'] is None and (spotlight['reason'] != 'domain_assessment_unavailable' or spotlight['status'] != 'unavailable'):
                raise ValueError('unassessed spotlight requires explicit unavailable reason')
    body = {key: value for key, value in room_output.items() if key != 'revision_id'}
    if room_output['revision_id'] != 'room:' + digest([identity, seq, body])[:48]:
        raise ValueError('content bound room revision required')
    bounded_json(room_output, 262144)
