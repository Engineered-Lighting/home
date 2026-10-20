"""Authored same-NPZ preview tests; no household/model/native qualification."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import importlib
import json
import math
import unittest

import apartment_desk_preview as preview


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def configured_candidate(pack):
    """Closed authored counterpart of the actual desk-target CLI output."""
    centre = [2., 2., .8]
    fixtures = []
    for i in range(4):
        origin = [float(i), 0., 2.]
        delta = [a-b for a,b in zip(centre, origin)]
        distance = math.hypot(*delta)
        fixtures.append(dict(fixture_id=f'authored-fixture-{i}', mount_id=f'authored-mount-{i}',
            status='calculated', display_anchor_m=origin, modeled_origin_m=origin,
            optical_origin_m=None, origin_role='authored_display_origin',
            horizontal_world_reference=[0.,1.,0.], target_m=centre,
            preview_angles=dict(convention='apartment_clockwise_pan_tilt_below_horizontal_degrees', pan_deg=0., tilt_deg=0.),
            delivery_angles=dict(convention='counterclockwise_pan_tilt_from_down_minus_Z_degrees',
                local_to_room=[1,0,0,0,1,0,0,0,1], pan_deg=0., tilt_deg=90.),
            direction_unit=[v/distance for v in delta], distance_m=distance, coverage='unassessed',
            obstruction='unassessed', protection='unassessed', hardware_zero_mapping=None, physical_commands=False))
    return dict(schema='home-registered-desk-aim-candidate-1', version=1,
        scope=dict(site_id='victoria', map_id='victoria-den', room_id='den'),
        binding=dict(content_revision=pack['content_revision'], registration_version=pack['registration_version'],
            manifest_digest='3'*64, frame_digest=pack['frame_sha256'], canonical_collision_digest=pack['collision_sha256']),
        equipment_ref=dict(revision=2, digest='4'*64),
        target_ref=dict(schema='home-registered-desk-target-2', digest='5'*64),
        target=dict(kind='polygon', vertices_m=[[1,1,.8],[3,1,.8],[3,3,.8],[1,3,.8]],
            centre_m=centre, normal_unit=[0,0,1], planarity=dict(max_distance_m=0., qualified=False)),
        fixtures=fixtures, omitted=[], truth_role='calculated_virtual_aim_candidate', qualification=None,
        allocation=None, operational_geometry=None, physical_commands=False,
        input_files=dict(target_sha256='6'*64, equipment_sha256='7'*64, manifest_sha256='8'*64), digest='9'*64)


def add_shoulders(event):
    """Authored model slots BEFORE the actual same-NPZ depth projection."""
    out = deepcopy(event)
    joints = out['payload']['result']['pose_instances'][0]['joints']
    for j in joints:
        j.update(pixel=[315. if j['joint_id']==5 else 325.,150.] if j['joint_id'] in (5,6) else None,
                 status='supported' if j['joint_id'] in (5,6) else 'missing')
    return out


class DeskPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alignment_class = importlib.import_module('test_apartment_view_alignment').ViewAlignmentTests
        cls.alignment_class.setUpClass()
        cls.addClassCleanup(cls.alignment_class.doClassCleanups)

    def setUp(self):
        self.f = self.alignment_class('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.event = add_shoulders(self.f.event)
        self.approximate = self.f.project(event=self.event)
        self.assertTrue(self.approximate['renderable'], self.approximate)
        self.candidate = configured_candidate(self.f.pack)
        self.raw = encoded(self.candidate)
        self.bound = preview.bind_desk_preview(self.raw, hashlib.sha256(self.raw).hexdigest(), self.f.pack)

    def project(self, approximate=None, event=None, state='recording'):
        return self.bound.project(self.approximate if approximate is None else approximate,
            event=self.event if event is None else event, session_state=state)

    def test_actual_npz_shoulders_and_configured_desk_are_distinct_honest_preview(self):
        out = self.project()
        self.assertTrue(out['available'], out)
        self.assertEqual(out['status'], 'preview')
        self.assertEqual(out['label'], 'Person visible · configured desk preview')
        self.assertEqual(out['target']['kind'], 'configured_desk_centre')
        self.assertEqual(out['target']['point_m'], self.candidate['target']['centre_m'])
        joints = self.approximate['approximate_pose']['joints']
        self.assertEqual(out['person_point']['point_m'], [sum(j['position_m'][i] for j in joints)/2 for i in range(3)])
        self.assertEqual(out['person_point']['joint_ids'], [5,6])
        self.assertIsNone(out['person_point']['error_bound_m'])
        self.assertTrue(all(out[k] is False for k in preview.NO_AUTHORITY))
        self.assertNotIn('journal', out);self.assertNotIn('person_ref', out['person_point'])
        self.assertEqual(out['observation']['frame_id'], self.event['payload']['frame_id'])
        self.assertLessEqual(out['valid_until'], self.approximate['frame']['original_times']['valid_until'])

    def test_original_bytes_are_immutable_aliased_and_pinned_to_exact_pack(self):
        self.assertIs(self.bound.retained_byte_allocations[0], self.raw)
        self.assertIs(self.bound.retained_byte_allocations[0], self.bound.retained_byte_allocations[0])
        with self.assertRaises(FrozenInstanceError):self.bound._original = b'changed'
        for raw,pin,pack in ((self.raw,'0'*64,self.f.pack),(self.raw+b' ','0'*64,self.f.pack),
            (self.raw,hashlib.sha256(self.raw).hexdigest(),{**self.f.pack,'frame_sha256':'a'*64})):
            with self.subTest(pack=pack),self.assertRaises(ValueError):preview.bind_desk_preview(raw,pin,pack)

    def test_closed_config_and_authority_escalation_are_rejected(self):
        changes=[lambda c:c.update(qualification='qualified'),lambda c:c.update(physical_commands=True),
            lambda c:c.update(extra=True),lambda c:c['target'].update(centre_m=[99,99,99]),
            lambda c:c['fixtures'][0].update(physical_commands=True),
            lambda c:c['target']['planarity'].update(qualified=True)]
        for change in changes:
            c=deepcopy(self.candidate);change(c);raw=encoded(c)
            with self.subTest(change=change),self.assertRaises(ValueError):preview.bind_desk_preview(raw,hashlib.sha256(raw).hexdigest(),self.f.pack)

    def test_two_people_even_with_only_first_metric_instance_are_not_a_preview(self):
        e=deepcopy(self.event);e['payload']['result']['objects'].append({'label':'person','instance_id':'second-person'})
        a=self.f.project(event=e)
        self.assertTrue(a['renderable'], a)  # Incumbent approximation selects0.
        out=self.project(a,e);self.assertFalse(out['available']);self.assertEqual(out['reason'],'desk_preview_single_person_required')

    def test_a_low_confidence_duplicate_box_is_the_same_person_but_real_second_people_refuse(self):
        def scene(extra, *, first=False, person_confidence=.9):
            e=deepcopy(self.event);r=e['payload']['result']
            for item in (r['objects'][0],r['boxes'][0]):item['confidence']=person_confidence
            box=dict(label='person',instance_id='authored-duplicate',**extra)
            entry=dict(label='person',instance_id='authored-duplicate',confidence=extra['confidence'])
            r['objects'].insert(0 if first else 1,entry);r['boxes'].insert(0 if first else 1,box)
            return e
        near=dict(x=261.,y=81.,w=119.,h=258.)
        for name,extra,options,available in (
                ('duplicate_after',{**near,'confidence':.3},{},True),
                ('duplicate_first',{**near,'confidence':.3},{'first':True},True),
                ('confident_second',{**near,'confidence':.45},{},False),
                ('distinct_second',dict(x=420.,y=80.,w=120.,h=260.,confidence=.3),{},False),
                ('confident_overlap',{**near,'confidence':.8},{},False),
                ('pose_on_weaker_box',{**near,'confidence':.9},{'person_confidence':.3},False)):
            e=scene(extra,**options);a=self.f.project(event=e)
            with self.subTest(name=name):
                self.assertTrue(a['renderable'],a);out=self.project(a,e)
                self.assertEqual(out['available'],available,out['reason'])
                if not available:self.assertEqual(out['reason'],'desk_preview_single_person_required')
                self.assertEqual(len(preview.distinct_people(e['payload']['result'])),1 if available or name=='pose_on_weaker_box' else 2)

    def test_a_second_invalid_raw_pose_cannot_disappear_into_the_normalized_first_slot(self):
        e=deepcopy(self.event);e['payload']['result']['pose_instances'].append({'layout':'unknown','joints':[]})
        a=deepcopy(self.approximate);a['approximate_pose']['provenance']['original_event_sha256']=hashlib.sha256(encoded(e)).hexdigest()
        out=self.project(a,e);self.assertFalse(out['available']);self.assertEqual(out['reason'],'desk_preview_single_person_required')

    def test_foreign_event_frame_media_epoch_generation_and_original_digest_are_withheld(self):
        changes=[lambda e:e.update(id=e['id']+1),lambda e:e.update(session_id='foreign'),
            lambda e:e['payload'].update(frame_id='foreign'),lambda e:e['payload'].update(media_ids=['foreign']),
            lambda e:e['payload'].update(capture_epoch='foreign'),lambda e:e['payload'].update(generation=999),
            lambda e:e['payload'].update(engine_epoch='foreign')]
        for change in changes:
            e=deepcopy(self.event);change(e)
            with self.subTest(change=change):self.assertFalse(self.project(event=e)['available'])
        a=deepcopy(self.approximate);a['approximate_pose']['provenance']['original_event_sha256']='f'*64
        self.assertFalse(self.project(a)['available'])

    def test_missing_or_unsupported_shoulders_never_fall_back_to_nose_or_desk_alone(self):
        for kind in ('missing','duplicate','unsupported'):
            a=deepcopy(self.approximate);j=a['approximate_pose']['joints']
            if kind=='missing':j.pop()
            elif kind=='duplicate':j.append(deepcopy(j[0]))
            else:j[0]['depth_support']=0
            out=self.project(a);self.assertFalse(out['available']);self.assertIsNone(out['target'])

    def test_stop_upstream_retirement_and_authority_changes_withdraw(self):
        self.assertFalse(self.project(state='stopped')['available'])
        for change in (lambda a:a['timing'].update(remaining_receipt_hold_s=0),
            lambda a:a.update(renderable=False,reason='approximate_receipt_expired'),
            lambda a:a.update(geometry_authority=True),lambda a:a.update(physical_commands=True),
            lambda a:a['approximate_pose']['scope'].update(frameDigest='f'*64)):
            a=deepcopy(self.approximate);change(a);out=self.project(a)
            self.assertFalse(out['available']);self.assertIsNone(out['target'])

    def test_polls_copy_original_times_and_decrease_hold_without_renewing(self):
        a=deepcopy(self.approximate);a['timing'].update(receipt_age_s=1.9,remaining_receipt_hold_s=.1)
        first=self.project(a);second=self.project(a)
        self.assertTrue(first['available'], first)
        self.assertEqual(first,second);self.assertEqual(first['timing'],a['timing'])
        self.assertEqual(first['source_times'],a['approximate_pose']['source_times'])
        first['target']['point_m'][0]=999
        self.assertEqual(self.project(a)['target']['point_m'],self.candidate['target']['centre_m'])


if __name__ == '__main__':unittest.main()
