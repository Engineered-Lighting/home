"""Authored config7 domains. These references never qualify real coverage."""
import copy

from journal import digest
from live_lifecycle_test_helpers import lifecycle_fixture
from live_test_helpers import MANIFEST, PROFILE, reference
from live_worker_test_helpers import describe


def conservative_fixture():
    service, grants, config = lifecycle_fixture()
    policy = config['delivery_policy']
    policy.update(schema='home-live-task-delivery-policy-4', version=4,
                  modeled_emission=dict(assessment_ref=reference('fictional-emission-boundary'), range_m=12,valid_until=1800100000),
                  observation_coverage=dict(assessment_ref=reference('fictional-person-coverage-assessment'),
                      method_ref=reference('fictional-person-coverage-method'),qualification_ref=reference('fictional-person-coverage-qualification'),
                      applicability_ref=reference('fictional-person-coverage-applicability'),bounds=copy.deepcopy(policy['room']['usable_bounds']),valid_until=1800100000))
    description = describe(config)
    manifest = service['manifest']
    manifest['service_clock_policy']=dict(schema='home-live-service-clock-policy-1',version=1,
        qualification_ref=reference('fictional-service-clock-qualification'),valid_until=1800100000,
        uncertainty_s=.001,max_anchor_divergence_s=.05,max_round_trip_s=.5,max_mapping_age_s=3)
    manifest.update(schema='home-live-ingress-manifest-7', version=7,
                    conservative_delivery_interface_digest=description['conservative_delivery_interface_digest'])
    manifest['worker'].update(config_digest=description['config_digest'],delivery_policy=copy.deepcopy(policy),
                              conservative_delivery_schema_pins=description['conservative_delivery_schema_pins'])
    service.update(schema='home-live-ingress-config-7',version=7)
    service['manifest_ref']=dict(id='fictional-conservative-delivery-manifest',digest=digest(manifest))
    MANIFEST.update(service['manifest_ref'])
    config.update(manifest_ref=copy.deepcopy(service['manifest_ref']),live_manifest=copy.deepcopy(manifest))
    from schema_registry import LIVE_CONSERVATIVE_DELIVERY_VERSIONS, LIVE_LIFECYCLE_VERSIONS, schema_digest
    old = set(LIVE_LIFECYCLE_VERSIONS)-{'home-live-person-lifecycle-1','home-live-intent-event-2'}
    for grant in grants:
        grant['payload_types']=[name for name in grant['payload_types'] if name not in old]
        if 'worker_commit' in grant['operations']:
            grant['payload_types']+=list(LIVE_CONSERVATIVE_DELIVERY_VERSIONS)
        elif 'intent' in grant['operations']:
            grant['payload_types'].append('home-live-shadow-decision-6')
        grant['payload_types']=list(dict.fromkeys(grant['payload_types']))
        for binding in grant['live_bindings']:
            binding['manifest_ref']=copy.deepcopy(service['manifest_ref'])
        grant['schema_pins']={name:schema_digest(name,profile=PROFILE) for name in grant['payload_types']}
    return service,grants,config
