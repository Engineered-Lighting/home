"""Public configuration admission; authored fixtures only, no runtime I/O."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'tools/den-live-runtime'))
from live_conservative_test_helpers import conservative_fixture
from live_ingress_validation import validate_config
from schema_registry import SchemaRegistry
from room_configuration import upgrade_configuration


class RoomConfigurationTests(unittest.TestCase):
    def test_explicit_assessed_task_commissioning_pins_fallback_and_inspection_to_the_same_room(self):
        original,grants,worker=conservative_fixture()
        before=copy.deepcopy(worker)
        room=json.loads(subprocess.check_output(['node','-e',"process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"],cwd=ROOT,text=True))
        room['registration']=copy.deepcopy(worker['delivery_policy']['registration'])
        for fixture in room['fixtures']:
            task=copy.deepcopy(worker['delivery_policy'])
            task['fixture'].update(id=fixture['fixture_id'],fixture_pose_ref=fixture['pose_ref'])
            task['room']['assessment_ref']['id']='independent-authored-room-review'
            fixture['task_policy']=task
        result,selected=upgrade_configuration(original,worker,room)
        self.assertEqual(selected['delivery_policy'],room['fixtures'][0]['task_policy'])
        self.assertEqual(selected['lifecycle_policy']['operator_clearance']['room_assessment_ref'],room['fixtures'][0]['task_policy']['room']['assessment_ref'])
        for key in ('policy_ref','procedure_ref','applicability_ref'):
            self.assertEqual(selected['lifecycle_policy']['operator_clearance'][key],worker['lifecycle_policy']['operator_clearance'][key])
        for key in ('delivery_policy','lifecycle_policy'):
            self.assertEqual(result['manifest']['worker'][key],selected[key])
        validate_config(result,SchemaRegistry([],profile='home-live-shadow-1'))
        self.assertEqual(worker,before)
        self.assertEqual(result['manifest']['sources'],original['manifest']['sources'])

    def test_room_configuration_requires_explicit_successor_and_preserves_input_pins(self):
        original,grants,worker=conservative_fixture()
        before=copy.deepcopy(original)
        run=subprocess.run(['node','-e',"process.stdout.write(JSON.stringify(require('./tools/fixtures/home-live-room.cjs').policy()))"],
                           cwd=ROOT,check=True,capture_output=True,text=True,timeout=10)
        room=json.loads(run.stdout)
        room['registration']=copy.deepcopy(worker['delivery_policy']['registration'])
        result,selected=upgrade_configuration(original,worker,room)
        registry=SchemaRegistry([],profile='home-live-shadow-1')
        validate_config(result,registry)
        self.assertEqual(result['schema'],'home-live-ingress-config-9')
        self.assertEqual(result['manifest']['sources'],original['manifest']['sources'])
        self.assertEqual(selected['room_policy'],room)
        self.assertEqual(original,before)
        forged=copy.deepcopy(result)
        forged['schema']='home-live-ingress-config-7'
        forged['version']=7
        with self.assertRaises(Exception):
            validate_config(forged,registry)


if __name__=='__main__':unittest.main()
