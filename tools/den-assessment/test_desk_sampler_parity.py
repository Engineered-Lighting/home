"""Differential numeric parity against frozen source; no real qualification."""
from copy import deepcopy
import ast
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(r'C:\Claude\home')
ARTIFACT = ROOT/'artifacts/den-bundled-consumer-20261017/assessment-successor-proposal/same-method-service-20261017-01'
for folder in (ROOT/'stack/services/perception-lab', ROOT/'stack/services/home-context', ROOT/'tools/den-assessment'):
    if str(folder) not in sys.path: sys.path.insert(0, str(folder))
import numpy as np
import lab_live_desk_anchor as anchor
import desk_method_export as export
import test_desk_method_bundled_export as bundled_fixture


def frozen_function(module, relative, expected, name):
    path = ARTIFACT/'preimages'/relative
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise AssertionError('fixed_preimage_required')
    node = next(node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef) and node.name == name)
    scope = dict(vars(module))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), 'exec'), scope)
    return scope[name]


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class DeskSamplerParityTests(unittest.TestCase):
    def setUp(self):
        self.old_anchor = frozen_function(anchor, 'stack/services/perception-lab/lab_live_desk_anchor.py',
            '6113bcf845e5b353569fbea75798ece033f1d289347667e59551f827885d1485', '_nose_point')
        self.old_report = frozen_function(export, 'tools/den-assessment/desk_method_export.py',
            'f7d74240ae0f0185c0653077574fb4f6ff2b32727384f60cc9e91e6cea89298b', '_bundled_alignment')
        k = [[100,0,16], [0,100,12], [0,0,1]]
        self.metadata = dict(frames=dict(
            depth=dict(width=32,height=24,intrinsics=deepcopy(k),transformation_valid=True),
            rgb=dict(width=32,height=24,intrinsics=deepcopy(k),transformation_valid=True,
                source_distortion_coefficients=[0.]*14)))
        self.projection = dict(id='authored-projection',mount_revision='authored-mount',
            reprojection=dict(receipt='authored-reprojection',p95_px=1),
            reference_intrinsics=dict(rectified_left=deepcopy(k),rgb=deepcopy(k)),
            rgb_from_rectified_left=dict(rotation=[[1,0,0],[0,1,0],[0,0,1]],translation_m=[0,0,0]))
        self.registration = dict(id='authored-registration',error_bound_m=.01,
            room_from_rectified_left=[[1,0,0,1],[0,0,1,2],[0,-1,0,3],[0,0,0,1]])
        self.plane = np.full((24,32),2000,dtype=np.uint16)
        self.original = dict(frame_metadata=dict(uncertainty_seconds=.001),event=dict(payload=dict(observed_at=10.)))
        self.depth = dict(observed_at=9.99,uncertainty_s=.002)
        self.policy = dict(radius_px=4,min_support=3,max_spread_m=.2,
            max_speed_m_s=.2,max_accel_m_s2=.3,nose_error_bound_m=.04)
        self.pixel = [16,12]

    def anchor_args(self):
        return (self.original,self.depth,self.plane,self.metadata,self.policy,self.registration,self.projection,self.pixel)

    def test_measured_anchor_preserves_frozen_output_bytes_and_shared_numeric_point(self):
        before = self.plane.copy()
        for checked in (False,True):
            old = self.old_anchor(*self.anchor_args(),check_spread=checked)
            new = anchor._nose_point(*self.anchor_args(),check_spread=checked)
            self.assertEqual(encoded(new),encoded(old))
            self.assertEqual(new['camera_xyz_m'],[0.,0.,2.])
            self.assertEqual(new['room_xyz_m'],[1.,4.,3.])
            self.assertGreater(new['support'],0)
        sample = anchor.sample_visible_nose_depth(self.plane,self.metadata,self.projection,self.registration,self.pixel,
            radius_px=4,min_support=3)
        self.assertEqual(set(sample),{'camera_xyz_m','room_xyz_m','spread_m','support','radius_px','min_support'})
        self.assertEqual(sample['room_xyz_m'],new['room_xyz_m'])
        np.testing.assert_array_equal(self.plane,before)

    def test_default_unchecked_spread_is_preserved_and_checked_spread_refuses(self):
        self.policy['max_spread_m'] = 1e-9
        old = self.old_anchor(*self.anchor_args())
        new = anchor._nose_point(*self.anchor_args())
        self.assertEqual(encoded(new),encoded(old))
        self.assertGreater(new['spread_m'],self.policy['max_spread_m'])
        for function in (self.old_anchor,anchor._nose_point):
            with self.assertRaisesRegex(ValueError,'^visible_nose_depth_unavailable$'):
                function(*self.anchor_args(),check_spread=True)

    def test_missing_depth_and_changed_projection_preserve_refusals(self):
        self.plane.fill(0)
        for function in (self.old_anchor,anchor._nose_point):
            with self.assertRaisesRegex(ValueError,'^visible_nose_depth_unavailable$'):
                function(*self.anchor_args())
        self.plane.fill(2000)
        self.projection['reprojection']['p95_px'] = 6
        for function in (self.old_anchor,anchor._nose_point):
            with self.assertRaisesRegex(ValueError,'^validated RGB-depth reprojection required$'):
                function(*self.anchor_args())

    def test_actual_bundled_report_matches_frozen_numeric_path_and_keeps_service_blocked(self):
        _,candidate,document,verified,qualification = bundled_fixture.inputs(self)
        request = export.alignment_request(qualification,verified['collection'])
        old = self.old_report(qualification,verified,request)
        new = export._bundled_alignment(qualification,verified,request)
        self.assertEqual(encoded(new),encoded(old))
        self.assertTrue(any(sample['diagnostic_point'] is not None for sample in new[1].values()))
        value = bundled_fixture.evaluate(candidate,document,verified,qualification)
        self.assertEqual(value['report']['decision'],'block')
        self.assertIn('contemporaneous_exact_method_service_timing_unavailable',value['report']['missing_inputs'])
        self.assertIsNone(value['report']['method_availability'])
        self.assertIsNone(value['commissioning'])
