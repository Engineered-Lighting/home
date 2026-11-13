"""The den camera's measured depth correction, and the pin binding perception-lab to that camera script."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest
import uuid

import numpy as np

import bundle_rgb_input

ROOT = Path(__file__).resolve().parents[3]
PINNED = ROOT / 'tools/fixtures/installed-oak-capture-20261111.py'
EARLIER = ROOT / 'tools/fixtures/installed-oak-capture-20261016.py'
# What the depth-correction release may change. Everything else, the clock checks included, must stay
# byte-identical to the earlier release, so receiver-clock proofs it signed remain valid.
CHANGED = {'__doc__', 'DEPTH_CORRECTION', 'calibration_sha256', 'depth_correction_for', 'correct_depth_mm',
           'capture_session'}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def definitions(path):
    out = {}
    for index, node in enumerate(ast.parse(path.read_text(encoding='utf-8')).body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            key = '__doc__'
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            key = node.name
        elif isinstance(node, ast.Assign):
            key = ','.join(sorted(n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)))
        else:
            key = ast.dump(node)
        out[key] = ast.dump(node)
    return out


class PinTests(unittest.TestCase):
    def test_pin_is_the_release_source(self):
        self.assertEqual(hashlib.sha256(PINNED.read_bytes()).hexdigest(), bundle_rgb_input.CAMERA_SHA)
        self.assertEqual(bundle_rgb_input.CAMERA_SOURCE.parent.name, 'pilot-20261111-depth')
        self.assertIn(hashlib.sha256(EARLIER.read_bytes()).hexdigest(), bundle_rgb_input.EARLIER_CAMERA_SHAS)

    def test_only_the_depth_correction_changed_since_the_earlier_release(self):
        old, new = definitions(EARLIER), definitions(PINNED)
        self.assertLessEqual(set(new) - set(old), CHANGED)
        self.assertLessEqual(set(old) - set(new), CHANGED)
        for key in set(old) & set(new) - CHANGED:
            with self.subTest(definition=key):
                self.assertEqual(old[key], new[key])

    def test_earlier_release_proofs_stay_valid_and_others_do_not(self):
        boot = str(uuid.UUID(int=1))
        evidence = dict(version=1, semantics='commissioned_camera_host_clock_v1', host_boot_id=boot,
                        validation_id='fictional', checked_monotonic_ns=10_000_000_000,
                        checked_unix_ns=1_800_000_000_000_000_000, valid_until_monotonic_ns=11_000_000_000,
                        uncertainty_seconds=.4)
        point = lambda s: dict(before_ns=10_000_000_000 + int(s * 1e9), boot_ns=10_000_000_000 + int(s * 1e9),
                               unix_ns=1_800_000_000_000_000_000 + int(s * 1e9),
                               after_ns=10_000_000_100 + int(s * 1e9))
        proof = lambda validator: dict(evidence=evidence, before=point(.1), after=point(.11), boot_id=boot,
                                       validator_source_sha256=validator)
        for validator in (bundle_rgb_input.CAMERA_SHA, *bundle_rgb_input.EARLIER_CAMERA_SHAS):
            with self.subTest(validator=validator):
                bundle_rgb_input.host_execution_interval(proof(validator))
        for validator in ('f' * 64, ['f' * 64], None):
            with self.subTest(validator=validator), self.assertRaisesRegex(ValueError, 'bundled_receiver_proof_invalid'):
                bundle_rgb_input.host_execution_interval(proof(validator))


class DepthCorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.camera = load(PINNED, '_depth_correction_camera')

    def setUp(self):
        self.measured = dict(self.camera.DEPTH_CORRECTION)
        self.eeprom = {'fictional': 'calibration', 'cameraData': [[0, {'width': 1280, 'height': 800}]]}
        self.camera.DEPTH_CORRECTION['calibration_sha256'] = self.camera.calibration_sha256(self.eeprom)

    def tearDown(self):
        self.camera.DEPTH_CORRECTION.clear()
        self.camera.DEPTH_CORRECTION.update(self.measured)

    def test_measured_factor_is_declared(self):
        self.assertEqual(self.measured['factor'], 0.889)
        self.assertRegex(self.measured['calibration_sha256'], '^[0-9a-f]{64}$')

    def test_matching_calibration_scales_valid_depth_and_keeps_invalid(self):
        correction = self.camera.depth_correction_for(self.eeprom)
        self.assertEqual((correction['applied'], correction['factor']), (True, 0.889))
        depth = np.array([[0, 1, 2, 1000], [1581, 4674, 65535, 0]], dtype=np.uint16)
        out = self.camera.correct_depth_mm(depth, correction)
        self.assertEqual(out.dtype, np.uint16)
        self.assertEqual(out.tolist(), [[0, 1, 2, 889], [1406, 4155, 58261, 0]])

    def test_key_order_does_not_matter(self):
        reordered = {'cameraData': self.eeprom['cameraData'], 'fictional': 'calibration'}
        self.assertTrue(self.camera.depth_correction_for(reordered)['applied'])

    def test_any_other_calibration_leaves_depth_untouched(self):
        for eeprom in ({'fictional': 'recalibrated'}, {}, object(), float('nan')):
            with self.subTest(eeprom=eeprom):
                correction = self.camera.depth_correction_for(eeprom)
                self.assertEqual((correction['applied'], correction['factor']), (False, 1.0))
                depth = np.array([[0, 1000]], dtype=np.uint16)
                self.assertIs(self.camera.correct_depth_mm(depth, correction), depth)


if __name__ == '__main__':
    unittest.main()
