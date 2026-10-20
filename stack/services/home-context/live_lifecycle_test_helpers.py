"""Fictional config6 commissioning, never a live method qualification."""
import copy

from journal import digest
from live_test_helpers import MANIFEST, PROFILE, reference, person, fact
from live_worker_test_helpers import describe
from test_live_stable_delivery import stable_fixture

LIFECYCLE_METHOD = reference('synthetic-lifecycle-method')
QUALIFICATION = reference('synthetic-lifecycle-qualification')
APPLICABILITY = reference('synthetic-lifecycle-applicability')
COVERAGE = reference('synthetic-lifecycle-coverage')


def lifecycle_fixture():
    service, grants, config = stable_fixture()
    config['delivery_policy'].update(schema='home-live-task-delivery-policy-3', version=3, brightness_transition_s=2)
    tuple_ = dict(method_ref=LIFECYCLE_METHOD, qualification_ref=QUALIFICATION, applicability_ref=APPLICABILITY)
    config['lifecycle_policy'] = dict(schema='home-live-person-lifecycle-policy-1', version=1,
        continuity_methods=[dict(**tuple_, max_gap_s=4)], negative_methods=[tuple_],
        absence_methods=[dict(**tuple_, coverage_assessment_ref=COVERAGE)],
        operator_clearance=dict(policy_ref=reference('synthetic-clearance-policy'),
            applicability_ref=reference('synthetic-clearance-applicability'), procedure_ref=reference('synthetic-clearance-procedure'),
            room_assessment_ref=config['delivery_policy']['room']['assessment_ref'], max_age_s=5))
    manifest = service['manifest']
    for source in manifest['sources'].values():
        source['method_refs'].append(LIFECYCLE_METHOD)
    config['source_pins'] = {name: digest(source) for name, source in manifest['sources'].items()}
    desc = describe(config)
    manifest.update(schema='home-live-ingress-manifest-6', version=6, lifecycle_interface_digest=desc['lifecycle_interface_digest'])
    manifest['worker'].update(config_digest=desc['config_digest'], source_pins=config['source_pins'],
        delivery_policy=copy.deepcopy(config['delivery_policy']), lifecycle_policy=copy.deepcopy(config['lifecycle_policy']),
        lifecycle_schema_pins=desc['lifecycle_schema_pins'])
    service.update(schema='home-live-ingress-config-6', version=6)
    service['manifest_ref'] = dict(id='synthetic-correction-lifecycle-manifest', digest=digest(manifest))
    MANIFEST.update(service['manifest_ref'])
    config.update(manifest_ref=copy.deepcopy(service['manifest_ref']), live_manifest=copy.deepcopy(manifest))
    from schema_registry import LIVE_LIFECYCLE_VERSIONS, LIVE_STABLE_DELIVERY_VERSIONS, schema_digest
    for grant in grants:
        grant['payload_types'] = [name for name in grant['payload_types'] if name not in LIVE_STABLE_DELIVERY_VERSIONS]
        if 'worker_commit' in grant['operations']:
            grant['payload_types'] += list(LIVE_LIFECYCLE_VERSIONS)
            grant['revisions'].append('2.0')
        elif 'intent' in grant['operations']:
            grant['payload_types'] += ['home-live-shadow-decision-5', 'home-live-intent-event-2']
            grant['revisions'].append('2.0')
        elif 'event' in grant['operations']:
            grant['payload_types'].append('home-live-person-lifecycle-1')
        for binding in grant['live_bindings']:
            binding['manifest_ref'] = copy.deepcopy(service['manifest_ref'])
        grant['schema_pins'] = {name: schema_digest(name, profile=PROFILE) for name in grant['payload_types']}
    return service, grants, config


def clearance(event_id, hazards, policy, at):
    from live_scene_test_helpers import intent
    value = {k: copy.deepcopy(v) for k, v in policy['operator_clearance'].items() if k != 'max_age_s'}
    value.update(hazard_refs=[h['hazard_ref'] for h in hazards], inspection_at=at)
    result = intent(event_id, kind='clear_person_hazards', at=at)
    result.update(schema='home-live-intent-event-2', version=2, target=None, value=value)
    return result


def brightness(event_id, scene, at, level):
    from live_scene_test_helpers import intent
    result = intent(event_id, person_ref=scene['person_ref'], kind='set_brightness', at=at)
    result.update(schema='home-live-intent-event-2', version=2, origin='human_preference',
        value=dict(scene_id=scene['scene_id'], episode_id=scene['episode_id'], subject_ref=scene['subject_ref'], brightness_fraction=level))
    return result


def lifecycle(seq, basis_refs, disposition='continuity_qualified', start_at=None):
    f = fact('synthetic-life-basis-'+str(seq), seq=seq)
    return dict(schema='home-live-person-lifecycle-1', version=1, id='synthetic-lifecycle-'+str(seq),
        **{k: copy.deepcopy(f[k]) for k in ['site_id','room_id','session_id','domain_epoch','manifest_ref','source_id','source_generation',
            'observed_at','available_at','valid_until','clock','sensor_roots','observation_roots','observation_root_times','sensor_root_bindings','original_receipt_refs']},
        target_person_ref=person(), target_subject_ref=copy.deepcopy(f['subject_ref']), disposition=disposition,
        method_ref=LIFECYCLE_METHOD, qualification_ref=QUALIFICATION, applicability_ref=APPLICABILITY,
        coverage_assessment_ref=COVERAGE if disposition in {'departure_qualified','absence_qualified'} else None,
        basis_refs=basis_refs, head=dict(key=dict(source_id=f['source_id'], payload_type='home-live-person-lifecycle-1',
            person_ref=person(), subject_ref=copy.deepcopy(f['subject_ref']), predicate='person_lifecycle', method_ref=LIFECYCLE_METHOD),
            position=f['head']['position'], supersedes=None),
        window=dict(start_at=f['observed_at'] if start_at is None else start_at, end_at=f['observed_at']))
