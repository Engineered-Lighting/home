"""Public production-factory admission for the additive fixed interaction stage."""
from copy import deepcopy
from pathlib import Path
import unittest

import live_capture_fixtures as capture
import live_spatial_fixtures as spatial
from journal import digest
from live_acquisition import create_production_acquisition
from test_live_capture_policy2 import policy2_fixture


class InteractionGraphConfigurationTests(unittest.TestCase):
    def setUp(self):
        config, capture_policy, capture_method, _, grant = policy2_fixture()
        refs = {'detector': capture.pin('synthetic-detector'), 'pose': capture.pin('synthetic-pose')}
        (self.live_config, self.capture_policy, self.capture_method, _,
         self.spatial_policy, spatial_method) = spatial.commission(config, capture_policy, capture_method, grant, refs)
        self.interaction_policy = {'schema': 'synthetic-interaction-policy', 'version': 1,
            'capture_method_ref': deepcopy(self.capture_method), 'spatial_method_ref': deepcopy(spatial_method),
            'model_refs': deepcopy(refs)}
        self.config = {'schema': 'home-production-acquisition-3', 'version': 3,
            'session_ref': deepcopy(capture.SCOPE), 'source_id': capture.SOURCE,
            'revision': capture.REVISION, 'local_grant_id': 'synthetic-local-acquisition-grant',
            'model_refs': refs, 'limits': {'interval_s': .5, 'max_cycles': 4,
                'max_runtime_s': 30, 'stage_timeout_s': 2, 'memory_limit_mb': 512},
            'person_threshold': .5, 'max_people': 2,
            'occupancy_entity': 'binary_sensor.synthetic_person_occupancy',
            'camera_derived_entities': ['binary_sensor.synthetic_person_occupancy'],
            'spatial_method_ref': spatial_method,
            'interaction_method_ref': {'id': 'synthetic-interaction-method', 'digest': digest(self.interaction_policy)}}

    def construct(self, **options):
        ports = dict(live_config=self.live_config, capture_policy=self.capture_policy,
            capture_method=self.capture_method, spatial_policy=self.spatial_policy,
            interaction_policy=self.interaction_policy, journal_path=Path('synthetic-must-not-open.sqlite'),
            journal_grants=[], publication_token='', detector=None, pose=None,
            local_authority=None, household_enabled=None, occupancy=None)
        ports.update(options)
        # All cases stop at pure admission, before files, sockets or camera I/O.
        return create_production_acquisition(self.config, **ports)

    def test_complete_v3_configuration_reaches_existing_port_admission(self):
        with self.assertRaisesRegex(ValueError, 'local grants, commissioned control clock'):
            self.construct()

    def test_v3_requires_both_pinned_producers(self):
        for name in ('spatial_policy', 'interaction_policy'):
            with self.subTest(missing=name), self.assertRaisesRegex(ValueError, 'fixed pinned'):
                self.construct(**{name: None})

    def test_v3_rejects_changed_interaction_pin(self):
        self.config['interaction_method_ref']['digest'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'fixed pinned interaction'):
            self.construct()

    def test_v3_rejects_registered_but_differently_bound_interaction_policy_before_io(self):
        replacements = {'capture_method_ref': capture.pin('synthetic-other-capture-method'),
            'spatial_method_ref': capture.pin('synthetic-other-spatial-method'),
            'model_refs': {'detector': capture.pin('synthetic-other-detector'),
                           'pose': capture.pin('synthetic-other-pose')}}
        for field, value in replacements.items():
            policy = deepcopy(self.interaction_policy)
            policy[field] = value
            # This is a correctly pinned but incompatible policy, rather than
            # changed content under an old pin. Factory admission must stop it.
            self.config['interaction_method_ref']['digest'] = digest(policy)
            source = self.live_config['manifest']['sources'][capture.SOURCE]
            if field == 'model_refs':
                source['model_refs'].extend(deepcopy(list(value.values())))
            else:
                source['method_refs'].append(deepcopy(value))
                if field == 'spatial_method_ref':
                    source['association_method_refs'].append(deepcopy(value))
            source['method_refs'].append(deepcopy(self.config['interaction_method_ref']))
            self.live_config['manifest_ref']['digest'] = digest(self.live_config['manifest'])
            with self.subTest(binding=field), self.assertRaisesRegex(ValueError, 'fixed pinned interaction'):
                self.construct(interaction_policy=policy)

    def test_v3_rejects_generic_callbacks_for_either_fixed_producer(self):
        for name in ('spatial', 'interaction'):
            with self.subTest(stage=name), self.assertRaisesRegex(ValueError, 'fixed pinned'):
                self.construct(stages={name: lambda *_: None})

    def test_old_versions_reject_new_interaction_authority(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.config['schema'], self.config['version'] = 'home-production-acquisition-' + str(version), version
                self.config.pop('interaction_method_ref', None)
                if version == 1:
                    self.config.pop('spatial_method_ref', None)
                else:
                    self.config['spatial_method_ref'] = {'id': 'synthetic-spatial-method', 'digest': digest(self.spatial_policy)}
                with self.assertRaisesRegex(ValueError, 'interaction publication requires production acquisition version three'):
                    self.construct(spatial_policy=None if version == 1 else self.spatial_policy)


if __name__ == '__main__': unittest.main()
