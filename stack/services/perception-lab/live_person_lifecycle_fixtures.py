"""Authored factory commissioning only, never installed or real qualification."""
from copy import deepcopy

from journal import digest
from live_lifecycle_test_helpers import lifecycle_fixture, LIFECYCLE_METHOD, QUALIFICATION, APPLICABILITY, COVERAGE
from live_test_helpers import reference
from live_worker_test_helpers import describe
from schema_registry import schema_digest, LIVE_PROFILE


def commission(live_config, grant, *, capture_method, spatial_method, interaction_method, model_refs, qualified_methods=False):
    service, _, worker = lifecycle_fixture()
    source_id, = live_config['manifest']['sources']
    source = deepcopy(live_config['manifest']['sources'][source_id])
    source['method_refs'].append(deepcopy(LIFECYCLE_METHOD))
    source['association_method_refs'].append(deepcopy(LIFECYCLE_METHOD))
    source['receipt_store_ids'].append('synthetic-lifecycle-receipts')
    source['receipt_component_ids'].append('lifecycle')
    worker['engagement_policy'].update(support_methods=[deepcopy(interaction_method)],
        refinement_methods=[deepcopy(interaction_method)], negative_methods=[])
    worker['scene_policy']['catalog']['background']['methods'] = [deepcopy(interaction_method)]
    worker['delivery_policy']['registration'] = deepcopy(source['registration'])
    worker['delivery_policy']['room']['geometry_digest'] = source['registration']['geometry_digest']
    worker['delivery_policy']['protection']['policy_ref'] = deepcopy(source['protection_policy_refs'][0])
    # This fixture activates only explicitly fictional continuity. Actual
    # end-use/absence methods remain absent unless a test commissions them.
    worker['lifecycle_policy']['negative_methods'] = []
    worker['lifecycle_policy']['absence_methods'] = []
    if qualified_methods:
        negative = dict(method_ref=reference('synthetic-qualified-end-use'), qualification_ref=QUALIFICATION, applicability_ref=APPLICABILITY)
        absence = dict(method_ref=reference('synthetic-qualified-absence'), qualification_ref=QUALIFICATION,
                       applicability_ref=APPLICABILITY, coverage_assessment_ref=COVERAGE)
        worker['lifecycle_policy']['negative_methods'] = [negative]
        worker['lifecycle_policy']['absence_methods'] = [absence]
        source['method_refs'] += [deepcopy(negative['method_ref']), deepcopy(absence['method_ref'])]
    worker['source_pins'] = {source_id: digest(source)}
    description = describe(worker)
    manifest = service['manifest']
    manifest['sources'] = {source_id: source}
    manifest['identity'] = deepcopy(live_config['manifest']['identity'])
    manifest['worker'].update(config_digest=description['config_digest'], source_pins=worker['source_pins'],
        engagement_policy=deepcopy(worker['engagement_policy']), scene_policy=deepcopy(worker['scene_policy']),
        delivery_policy=deepcopy(worker['delivery_policy']), lifecycle_policy=deepcopy(worker['lifecycle_policy']),
        lifecycle_schema_pins=description['lifecycle_schema_pins'])
    service['manifest_ref']['digest'] = digest(manifest)
    grant = deepcopy(grant)
    grant['payload_types'].append('home-live-person-lifecycle-1')
    grant['schema_pins']['home-live-person-lifecycle-1'] = schema_digest('home-live-person-lifecycle-1', profile=LIVE_PROFILE)
    grant['live_bindings'][0]['manifest_ref'] = deepcopy(service['manifest_ref'])
    continuity = worker['lifecycle_policy']['continuity_methods'][0]
    policy = dict(schema='home-local-person-lifecycle-policy-1', version=1, id='authored-lifecycle-factory',
        capture_method_ref=deepcopy(capture_method), spatial_method_ref=deepcopy(spatial_method),
        model_refs=deepcopy(model_refs), registration=deepcopy(source['registration']),
        receipt_store_id='synthetic-lifecycle-receipts', receipt_component_id='lifecycle',
        continuity={**deepcopy(continuity), 'anchor_joint_ids': [5, 6, 11, 12], 'min_anchor_count': 2,
            'max_anchor_error_m': .1, 'max_step_m': .5, 'max_speed_m_s': 2},
        negative_methods=deepcopy(worker['lifecycle_policy']['negative_methods']),
        absence_methods=deepcopy(worker['lifecycle_policy']['absence_methods']))
    return service, grant, policy
