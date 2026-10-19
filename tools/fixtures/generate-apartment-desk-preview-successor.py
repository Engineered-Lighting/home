"""The desk-preview integration export, fed by a verified camera-session successor.

Identical to generate-apartment-desk-preview.py except for the private metric
opt-in: a v2 policy whose reviewed fit is pinned to a retired capture epoch,
plus a successor verified for the fixture's current one. Authored inputs only;
no host, camera, model, browser or physical path runs.
"""
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('generate_apartment_desk_preview',
                                              Path(__file__).resolve().with_name('generate-apartment-desk-preview.py'))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)  # also adds the perception-lab import folders
import test_apartment_viewing_metric_successor as successor_fixture


def generate(options):
    metric_tests = base.source_fixture.source_fixture.MetricSourceTests
    with patch.object(metric_tests, 'approximate_configuration', successor_fixture.successor_configuration):
        out = base.generate(options)
    arm = out['live']['arms']['B']
    session = arm['approximate_room_pose']['approximate_pose']['registration']['camera_session']
    assert session['verified_epoch'] == arm['observation']['capture_epoch'] != session['reviewed_capture_epoch']
    out['view_alignment_camera_session'] = session
    return out


if __name__ == '__main__':
    raw = sys.stdin.buffer.read(16385)
    assert len(raw) <= 16384
    sys.stdout.write(json.dumps(generate(json.loads(raw)), sort_keys=True, separators=(',', ':'), allow_nan=False))
