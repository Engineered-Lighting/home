"""Actual room-controller authored output; no Journal authority or native I/O."""
from copy import deepcopy
import unittest

from journal import digest
from room_command_projection import (RETAINED_TASK_PROFILE1, RETAINED_TASK_PROFILE_REF,
    RoomProjectionRefusal, project_committed_snapshot)
from room_command_session import CanonicalCommandSession
from room_fixture_command_join import RoomRetainedTaskAssessment
import test_room_command_projection as old
from test_room_delivery import room_case
from test_room_subset_worker import subset_policy
import test_fixture_joint_execution as joint
from test_fixture_retained_angle_execution import retained_request


def subset_snapshot(case):
    policy=subset_policy(case['policy'])
    output=deepcopy(case['room_output']); output['policy_ref']=deepcopy(policy['policy_ref'])
    for index,fixture in enumerate(output['fixtures']):
        for row in fixture['domains']:
            if index!=0 or row['domain']!='spotlight':
                row.update(status='unavailable',reason='domain_assessment_unavailable',sample=None,desired=None,
                    goal_id=None,scene_id=None,evidence_refs=[],assessment_refs=[],valid_until=case['t'])
    ref=dict(configuration_digest='a'*64,manifest_digest='b'*64,worker_version='home-live-context-worker-12.0.0',
        room_output_schema='home-live-room-output-1',commit_id='accepted-authored-room',commit_body_sha256='c'*64,
        decision_sha256='d'*64,room_output_revision_id=output['revision_id'],room_output_sha256=digest(output))
    snapshot=dict(schema='home-accepted-room-command-source-1',version=1,room_commit_ref=ref,
        home_scope=deepcopy(case['identity']),sample_at=case['t'],checked_at=case['t']+.001,
        physical_permission=False,native_achieved_truth=False,
        decision=dict(t=case['t'],input_cut=dict(cut_digest=output['input_cut_digest'],
            source_generations=deepcopy(output['source_generations'])),room_output=output),
        commit=dict(checkpoint=dict(state=dict(room_execution=deepcopy(case['room_snapshot'])))))
    return snapshot,policy


class RoomRetainedTaskProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual=room_case(task=True)  # One fixed local Node controller; no cameras/models.

    def test_actual_retained_endpoint_and_nullable_inventory_preserve_raw_policy(self):
        snapshot,policy=subset_snapshot(self.actual)
        result=project_committed_snapshot(snapshot,room_policy=policy,provider=old.projection_provider(),
            task_profile=deepcopy(RETAINED_TASK_PROFILE1))
        self.assertEqual((result['schema'],result['version']),('home-room-command-projection-2',2))
        self.assertEqual(result['room_policy_sha256'],digest(policy))
        self.assertEqual(len(result['fixtures']),4)
        self.assertTrue(all(len(f['domains'])==8 for f in result['fixtures']))
        self.assertTrue(all(f['pose_ref'] is None for f in result['fixtures'][1:]))
        self.assertTrue(all(d['intent'] is None and d['original']['sample'] is None
                            for f in result['fixtures'] for d in f['domains'][:7]))
        selected=next(f for f in result['fixtures'] if f['fixture_id']==snapshot['decision']['room_output']['task']['fixture_id'])
        task=selected['task']; trace=task['retained_motion']; original=trace['original']
        assignment=snapshot['decision']['room_output']['task']['allocation']['assignments'][0]
        self.assertEqual([original['to_pan_deg'],original['to_tilt_deg']],[assignment['pan_deg'],assignment['tilt_deg']])
        self.assertEqual(trace['retained_aim_ref']['room_commit_ref'],snapshot['room_commit_ref'])
        self.assertEqual(trace['retained_aim_ref']['fixture_basis_sha256'],digest(trace['fixture_basis']))
        self.assertEqual(task['light_request']['status'],'unmet')
        self.assertEqual(task['light_request']['sample'],selected['domains'][7]['original']['sample'])
        self.assertFalse(task['command_ready']);self.assertFalse(task['native_motion_equivalence'])

    def test_default_reader_and_changed_profile_or_forged_unassessed_off_refuse(self):
        snapshot,policy=subset_snapshot(self.actual)
        with self.assertRaisesRegex(RoomProjectionRefusal,'policy2_command_projection'):
            project_committed_snapshot(snapshot,room_policy=policy,provider=old.projection_provider())
        altered=deepcopy(RETAINED_TASK_PROFILE1);altered['aim_only']=False
        with self.assertRaisesRegex(RoomProjectionRefusal,'fixed_retained_task_profile'):
            project_committed_snapshot(snapshot,room_policy=policy,provider=old.projection_provider(),task_profile=altered)
        snapshot['decision']['room_output']['fixtures'][1]['domains'][0].update(status='off',sample=dict(brightness_fraction=0,cct_kelvin=3000))
        with self.assertRaisesRegex(RoomProjectionRefusal,'unassessed_room_domain'):
            project_committed_snapshot(snapshot,room_policy=policy,provider=old.projection_provider(),task_profile=RETAINED_TASK_PROFILE1)
        s,p=old.authored_projection_case()
        self.assertEqual(project_committed_snapshot(s,room_policy=p,provider=old.projection_provider())['version'],1)

    def test_task_can_trigger_binding_without_fabricated_ambient_and_expiry_is_exclusive(self):
        snapshot,_=subset_snapshot(self.actual)
        self.assertFalse(CanonicalCommandSession._supported(snapshot))
        self.assertTrue(CanonicalCommandSession._supported(snapshot,task_profile=RETAINED_TASK_PROFILE1))
        snapshot['checked_at']=snapshot['decision']['room_output']['task']['display_lease']['valid_until']
        self.assertFalse(CanonicalCommandSession._supported(snapshot,task_profile=RETAINED_TASK_PROFILE1))
        self.assertEqual(RETAINED_TASK_PROFILE_REF['digest'],digest(RETAINED_TASK_PROFILE1))

    def test_fictional_original_is_closed_copied_and_detects_later_inner_mutation(self):
        request=retained_request(joint.joint_fixture()); original=RoomRetainedTaskAssessment(request)
        request['fixture_angles_deg'][0]=99
        self.assertEqual(original.original()['fixture_angles_deg'],[10,20])
        original.request['assessment']['minimum_work_range_m']=999
        with self.assertRaisesRegex(ValueError,'original_changed'):original.original()
        with self.assertRaisesRegex(ValueError,'request2_original'):
            RoomRetainedTaskAssessment(dict(schema='home-fixture-retained-angle-request-2',version=2))


if __name__=='__main__':unittest.main()
