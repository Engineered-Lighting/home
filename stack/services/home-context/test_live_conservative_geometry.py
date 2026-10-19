"""Independent authored geometry expectations, not real room qualification."""
import copy
import json
import math
import unittest
from pathlib import Path

from live_delivery import _coverage, _protected, _validate_conservative_domains, _validate_conservative_surface, validate_delivery_policy
from live_delivery_test_helpers import delivery_policy

VECTORS = json.loads((Path(__file__).resolve().parents[3] / 'tools' / 'fixtures' / 'home-live-conservative-geometry.json').read_text())


class ConservativeGeometryTests(unittest.TestCase):
    def policy(self):
        result = delivery_policy()
        result.update(schema='home-live-task-delivery-policy-4', version=4, brightness_transition_s=2)
        result['modeled_emission']=dict(assessment_ref=dict(id='authored-emission-boundary',digest='a'*64),range_m=12,valid_until=1800100000)
        ref=lambda name:dict(id=name,digest='a'*64)
        result['observation_coverage']=dict(assessment_ref=ref('observation-assessment'),method_ref=ref('observation-method'),qualification_ref=ref('observation-qualified'),applicability_ref=ref('observation-applicable'),bounds=copy.deepcopy(result['room']['usable_bounds']),valid_until=1800100000)
        return result

    def test_shared_commissioning_acceptance_oracle(self):
        from journal import JournalError
        for row in VECTORS['commissioning']:
            with self.subTest(case=row['id']):
                policy=self.policy()
                if 'path' in row:
                    target=policy
                    for key in row['path'][:-1]:target=target[key]
                    target[row['path'][-1]]=copy.deepcopy(row['value'])
                operation=row.get('operation')
                if operation=='fixture-id-length':policy['fixture']['id']='a'*row['length']
                if operation=='coverage-count':policy['registration']['coverage_refs']=['coverage:'+str(n) for n in range(row['count'])]
                if operation=='transform-count':policy['registration']['transform_chain_refs']=[dict(id='transform:'+str(n),digest='a'*64) for n in range(row['count'])]
                if operation=='duplicate-coverage':policy['registration']['coverage_refs'].append(policy['registration']['coverage_refs'][0])
                if operation=='extra-primitive-field':policy['room']['usable_bounds']['uncommissioned']=True
                try:
                    _validate_conservative_domains(policy);_validate_conservative_surface(policy)
                    base=copy.deepcopy(policy);base.update(schema='home-live-task-delivery-policy-2',version=2)
                    for key in ('modeled_emission','observation_coverage','brightness_transition_s'):del base[key]
                    scene=dict(catalog=dict(examples=[dict(requirements_ref=base['requirements_ref'],nominal=dict(fixture_family_ref=base['fixture']['family_ref']))]))
                    validate_delivery_policy(base,{'authored':dict(registration=base['registration'])},scene,allow_stable=True)
                    accepted=True
                except (ValueError,JournalError):accepted=False
                self.assertEqual(accepted,row['accepted'])

    def test_independent_axis_separation_vectors(self):
        for case in VECTORS['coverage']:
            with self.subTest(case=case['id']):
                policy = self.policy()
                policy['fixture']['origin_m'] = copy.deepcopy(case['origin_m'])
                vector = [x-y for x,y in zip(case['work']['center_m'],case['origin_m'])]
                pan, tilt = math.degrees(math.atan2(vector[1], vector[0])), math.degrees(math.atan2(math.hypot(vector[0],vector[1]),-vector[2]))
                actual = _coverage(case['work'], [case['obstacle']], policy, pan, tilt)
                self.assertEqual(actual['clear_fraction'] if case['expected']=='clear' else actual['blocked_fraction'], 1)

    def test_protection_is_not_truncated_to_useful_work_range(self):
        policy = self.policy()
        policy['fixture']['origin_m'] = [0,-2,1]
        # Nine metres along the +Y beam: outside useful work range (8m),
        # still inside the emitted direction; no zero-light range is qualified.
        box = dict(kind='aabb', center_m=[0,7,1], half_extents_m=[.1,.1,.1])
        self.assertFalse(_protected([box], policy, 90,90,.4))

    def test_degenerate_protection_direction_is_a_typed_conservative_hit(self):
        policy = self.policy()
        policy['fixture']['origin_m'] = [0,-2,2.5]
        box = dict(kind='aabb', center_m=[3e-11,-2,2.5], half_extents_m=[1e-12,1e-12,1e-12])
        self.assertFalse(_protected([box], policy,90,45,.4))

    def test_nonzero_motion_expands_protection_over_lease(self):
        policy = self.policy()
        head = dict(kind='aabb', center_m=[1.5,0,1.8], half_extents_m=[.12,.12,.15])
        self.assertTrue(_protected([head],policy,90,45,.4))
        # Independently selected 0.3m/s + 0.1m/s2 over 3s expands by 1.35m.
        inflated = copy.deepcopy(head)
        inflated['half_extents_m']=[x + .3*3 + .5*.1*3*3 for x in head['half_extents_m']]
        self.assertFalse(_protected([inflated],policy,90,45,.4))


if __name__ == '__main__':
    unittest.main()
