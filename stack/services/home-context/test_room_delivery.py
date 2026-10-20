"""Independent admission of authored modeled room samples; no household I/O."""
import copy
import json
import subprocess
import unittest
from pathlib import Path

from room_delivery import validate_room_output, validate_room_worker_policy
from journal import digest

ROOT = Path(__file__).resolve().parents[3]


def room_case(vacancy=False, task=False):
    program = """
const F=require('./tools/fixtures/home-live-room.cjs');
const C=require('./app/src/home-live-room-composition.js');
const A=require('./app/src/home-live-room-allocation.js');
const vacant=process.argv[1]==='vacancy',task=process.argv[1]==='task',policy=task?F.taskRoom():F.policy(),lifecycle_policy=F.lifecycle();lifecycle_policy.absence_methods=[F.vacancyMethod()];
const d=A.createRoomAllocation({identity:F.identity,policy,max_output_age_s:3,lifecycle_policy,...(vacant?{fallback_policy:F.taskPolicy()}:{})});
const step=i=>d.step({...i,composition:C.compose({identity:F.identity,policy,...i})});
if(task){const first=step(F.emptyInput(98));step(F.clearanceInput(first.person_hazards));for(const t of [100,101,102,103])step(F.taskInput(t));}else step(F.input(100));if(vacant){step(F.input(100.5));step(F.vacancyInput(101));}const prior=d.snapshot(),input=task?F.taskInput(104):vacant?F.vacancyInput(103):F.input(101),result=step(input);
const publications=[...input.facts,...input.lifecycles,...input.geometries].map(payload=>({payload,publication_ref:input.publication_refs[payload.id],available_at:payload.available_at,valid_until:payload.valid_until}));
process.stdout.write(JSON.stringify({policy,identity:F.identity,t:input.t,seq:input.seq,input_cut_digest:input.input_cut_digest,authority_generation:0,source_generations:input.source_generations,background:input.background,room_output:result.room_output,room_snapshot:d.snapshot(),prior_room_snapshot:prior,publications,max_output_age_s:3,lifecycle_policy}));
"""
    return json.loads(subprocess.run(['node', '-e', program, 'task' if task else 'vacancy' if vacancy else 'presence'], cwd=ROOT, check=True, capture_output=True, text=True).stdout)


class RoomDeliveryTests(unittest.TestCase):
    def test_original_presence_admits_literal_midpoint_and_rejects_forged_sample(self):
        case = room_case()
        self.assertEqual(case['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'], .1)
        validate_room_output(**case)
        forged = copy.deepcopy(case)
        forged['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'] = .3
        with self.assertRaisesRegex(ValueError, 'ambient.*sample'):
            validate_room_output(**forged)

    def test_ambient_only_commit_cannot_smuggle_a_spotlight_without_task_authority(self):
        forged = room_case()
        spotlight = forged['room_output']['fixtures'][0]['domains'][7]
        spotlight.update(status='modeled', reason='forged', sample={'brightness_fraction': .4, 'cct_kelvin': 3200})
        body = {key: value for key, value in forged['room_output'].items() if key != 'revision_id'}
        forged['room_output']['revision_id'] = 'room:' + digest([forged['identity'], forged['seq'], body])[:48]
        with self.assertRaisesRegex(ValueError, 'spotlight'):
            validate_room_output(**forged)

    def test_qualified_vacancy_fade_uses_original_lifecycle_proof(self):
        case = room_case(vacancy=True)
        self.assertEqual(case['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'], .025)
        validate_room_output(**case)

    def test_selected_task_readback_is_admitted_with_all_concurrent_ambient_domains(self):
        case = room_case(task=True)
        self.assertEqual(case['room_output']['task']['fixture_id'], 'fixture-1')
        self.assertEqual(case['room_output']['fixtures'][0]['domains'][7]['sample']['brightness_fraction'], .4)
        self.assertEqual(case['room_output']['fixtures'][0]['domains'][0]['sample']['brightness_fraction'], .2)
        validate_room_output(**case)

    def test_concurrent_exposure_and_power_are_recomputed_independently(self):
        exposure = room_case(task=True)
        exposure['policy']['exposure']['max_total'] = .008
        for key in ('room_snapshot', 'prior_room_snapshot'):
            exposure[key]['policy_digest'] = digest(exposure['policy'])
        with self.assertRaisesRegex(ValueError, 'aggregate exposure'):
            validate_room_output(**exposure)
        power = room_case(task=True)
        power['policy']['fixtures'][0]['max_power_w'] = 3
        for key in ('room_snapshot', 'prior_room_snapshot'):
            power[key]['policy_digest'] = digest(power['policy'])
        with self.assertRaisesRegex(ValueError, 'fixture power'):
            validate_room_output(**power)

    def test_commissioned_candidates_cannot_diverge_from_fallback_room_and_clearance(self):
        import sys
        sys.path.insert(0, str(ROOT / 'tools/den-live-runtime'))
        from live_conservative_test_helpers import conservative_fixture
        from room_configuration import upgrade_configuration
        from schema_registry import SchemaRegistry
        service, _, worker = conservative_fixture()
        policy = room_case()['policy']
        policy['registration'] = copy.deepcopy(worker['delivery_policy']['registration'])
        for fixture in policy['fixtures']:
            task = copy.deepcopy(worker['delivery_policy'])
            task['fixture'].update(id=fixture['fixture_id'], fixture_pose_ref=copy.deepcopy(fixture['pose_ref']))
            fixture['task_policy'] = task
        configured, _ = upgrade_configuration(service, worker, policy)
        registry = SchemaRegistry([], profile='home-live-shadow-1')
        validate_room_worker_policy(configured['manifest'], registry)
        foreign = copy.deepcopy(configured['manifest'])
        assessment = dict(id='unrelated-room-assessment', digest='c'*64)
        foreign['worker']['delivery_policy']['room']['assessment_ref'] = copy.deepcopy(assessment)
        foreign['worker']['lifecycle_policy']['operator_clearance']['room_assessment_ref'] = copy.deepcopy(assessment)
        with self.assertRaisesRegex(ValueError, 'fallback room and clearance'):
            validate_room_worker_policy(foreign, registry)
        foreign = copy.deepcopy(configured['manifest'])
        foreign['worker']['lifecycle_policy']['operator_clearance']['room_assessment_ref'] = assessment
        with self.assertRaisesRegex(ValueError, 'fallback room and clearance'):
            validate_room_worker_policy(foreign, registry)


if __name__ == '__main__':
    unittest.main()
