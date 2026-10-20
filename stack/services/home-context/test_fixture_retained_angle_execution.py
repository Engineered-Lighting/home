"""Authored independent path originals; no native process or physical claim."""
from copy import deepcopy
from dataclasses import replace
import unittest

from fixture_execution import FixtureExecutionRefusal
from fixture_joint_execution import (CURRENT_FIELDS, RETAINED_REQUEST_SCHEMA, RETAINED_ASSESSMENT_METHOD,
    retained_native_reference_digest)
from fixture_profile import digest
from fixture_profile import FixtureProfileRefusal
import test_fixture_joint_execution as old


def retained_request(f, *, kind='MOVE', now_home_us=1000000, previous=None):
    r = old.joint_request(f, kind=kind, now_home_us=now_home_us)
    r.pop('target_room_m')
    r.update(schema=RETAINED_REQUEST_SCHEMA, version=2, fixture_angles_deg=[10, 20],
        retained_aim_ref=dict(room_commit_ref=dict(configuration_digest='a'*64, manifest_digest='b'*64,
            worker_version='home-live-context-worker-12.0.0', room_output_schema='home-live-room-output-1',
            commit_id='accepted-1', commit_body_sha256='c'*64, decision_sha256='d'*64,
            room_output_revision_id='room-1', room_output_sha256='e'*64),
            input_cut_digest='f'*64, motion_trace_sha256='1'*64, fixture_basis_sha256='2'*64))
    a = r['assessment']; a.pop('target_room_m')
    a.update(method=RETAINED_ASSESSMENT_METHOD, fixture_angles_deg=deepcopy(r['fixture_angles_deg']),
        retained_aim_ref=deepcopy(r['retained_aim_ref']), minimum_work_range_m=.8,
        current_reference_sha256=retained_native_reference_digest({k:getattr(f['ref'],k) for k in CURRENT_FIELDS}))
    if previous is not None:
        a['start_centideg'] = deepcopy(previous['start_centideg'])
    return r


def compile_retained(f, r=None, *, previous=None, now_home_us=1000000, **kwargs):
    ports = dict(current_ref=f['ref'], raw_status_v3=f['raw_status'], raw_capabilities_v2=f['raw_capabilities'])
    ports.update(kwargs)
    return f['compiler'].compile_retained_stationary_joint(r or retained_request(f),
        now_home_us=now_home_us, previous=previous, **ports)


class RetainedAngleExecutionTests(unittest.TestCase):
    def test_original_angles_quantize_to_joint_once_without_room_target(self):
        f=old.joint_fixture(); r=retained_request(f); result=compile_retained(f,r)
        self.assertEqual((result['schema'],result['version']),('home-fixture-joint-compiled-draft-2',2))
        self.assertEqual(result['fixture_angles_deg'],[10,20])
        self.assertEqual(result['target_centideg'],[1000,2000])
        self.assertEqual(f['compiler']._raw_validation(result['target_centideg'],result['speed_deg_s'])[0],[-1000,500])
        self.assertNotIn('target_room_m',result); self.assertNotIn('raw_target_centideg',result)
        self.assertEqual(result['retained_aim_ref'],r['retained_aim_ref'])
        self.assertFalse(result['physical_eligible']); self.assertFalse(result['authority_created'])

    def test_old_point_and_new_angle_requests_and_drafts_do_not_alias(self):
        f=old.joint_fixture(); original=old.compile_fixture(f)
        with self.assertRaisesRegex(FixtureProfileRefusal,'invalid_fields: joint_request'): old.compile_fixture(f,retained_request(f))
        with self.assertRaisesRegex(FixtureProfileRefusal,'invalid_fields: joint_request'): compile_retained(f,old.joint_request(f))
        with self.assertRaisesRegex(FixtureProfileRefusal,'invalid_fields: previous_joint_draft'): compile_retained(f,previous=original)
        changed=retained_request(f);changed['target_room_m']=[0,0,1]
        with self.assertRaisesRegex(FixtureProfileRefusal,'invalid_fields: joint_request'): compile_retained(f,changed)
        self.assertEqual(old.compile_fixture(f)['schema'],'home-fixture-joint-compiled-draft-1')

    def test_derived_current_clock_advances_without_renewing_any_original(self):
        f=old.joint_fixture();r=retained_request(f)
        first=compile_retained(f,r)
        advanced=replace(f['ref'],device_now_lower_us=110000,device_now_upper_us=110000)
        second=compile_retained(f,r,current_ref=advanced,now_home_us=1010000)
        self.assertEqual(first['assessment_sha256'],second['assessment_sha256'])
        self.assertEqual(first['support_expiry_home_us'],second['support_expiry_home_us'])
        self.assertEqual(second['support_expiry_home_us'],r['support_expiry_home_us'])
        self.assertEqual(second['motion_start_deadline_device_us'],first['motion_start_deadline_device_us'])
        for reference in (replace(f['ref'],device_now_upper_us=400000,device_now_lower_us=400000),
                          replace(f['ref'],captured_device_us=99999),replace(f['ref'],state_generation=9)):
            with self.subTest(reference=reference),self.assertRaises(FixtureExecutionRefusal):
                compile_retained(f,r,current_ref=reference)
        with self.assertRaises(FixtureExecutionRefusal): compile_retained(f,r,raw_status_v3=b'x'*998)
        with self.assertRaises(FixtureExecutionRefusal): compile_retained(f,r,raw_capabilities_v2=b'x'*404)

    def test_independent_range_path_and_error_floor_cannot_be_guessed_from_angles(self):
        for edit in (lambda a:a.update(minimum_work_range_m=0),lambda a:a.update(total_aim_error_bound_rad=0),
                     lambda a:a.update(status='qualified'),lambda a:a['checks'].update(dark_motion_path=False),
                     lambda a:a['retained_aim_ref'].update(input_cut_digest='9'*64)):
            f=old.joint_fixture();r=retained_request(f);edit(r['assessment'])
            with self.subTest(edit=edit),self.assertRaises(FixtureExecutionRefusal):compile_retained(f,r)

    def test_fresh_cut_and_correction_keep_actual_retained_motion_and_deadline(self):
        f=old.joint_fixture();prior=compile_retained(f)
        f['status'].update(phase='lit',revision_id='5',state_generation='10',motion_id='7',
            motion_started_us='200000',motion_deadline_us='2000000',support_expiry_home_us='3900000',
            mechanical_settled=True,current_semantic_target=dict(target_centideg=prior['target_centideg'],
                speed_deg_s=prior['speed_deg_s'],motion_timeout_us='2000000',start_deadline_device_us='300000'))
        old.refresh_joint_fixture(f,captured_device_us=2100000)
        r=retained_request(f,kind='CONTINUE',now_home_us=2100000,previous=prior)
        r['retained_aim_ref']['room_commit_ref']['commit_id']='accepted-2'
        r['retained_aim_ref']['input_cut_digest']='8'*64
        r['lineage']['correction_ref']=dict(intent_id='brightness-1',body_sha256='7'*64)
        r['assessment'].update(retained_aim_ref=deepcopy(r['retained_aim_ref']),lineage=deepcopy(r['lineage']))
        result=compile_retained(f,r,previous=prior,now_home_us=2100000)
        self.assertEqual(result['kind'],'CONTINUE')
        self.assertEqual(result['motion_id'],prior['motion_id'])
        self.assertEqual(result['motion_origin_revision_id'],prior['motion_origin_revision_id'])
        self.assertEqual(result['target_centideg'],prior['target_centideg'])
        self.assertEqual(result['motion_deadline_device_us'],2000000)
        self.assertEqual(result['motion_start_deadline_device_us'],300000)
        changed=deepcopy(r);changed['retained_aim_ref']['motion_trace_sha256']='6'*64
        changed['assessment']['retained_aim_ref']=deepcopy(changed['retained_aim_ref'])
        with self.assertRaises(FixtureExecutionRefusal):compile_retained(f,changed,previous=prior,now_home_us=2100000)


if __name__=='__main__': unittest.main()
