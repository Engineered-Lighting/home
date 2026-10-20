"""The desk-preview integration export under the development timing policy.

Identical to generate-apartment-desk-preview.py, except that the private v2
metric policy sets timing_policy 'development' (with a verified successor),
and Home reads /live, the media descriptor and the PNG after the PNG's
original deadline. So the export carries a real development preview served
past its deadline (timing.policy, timing.warnings, evidence) and a retained
descriptor (retention). Authored inputs only; no host, camera, model,
browser or physical path runs.
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

LATE_S = 1.  # Home reads one second after the PNG's original deadline


def generate(options):
    preview_tests = base.source_fixture.DeskPreviewSourceTests
    metric_tests = base.source_fixture.source_fixture.MetricSourceTests
    build = preview_tests.build
    late = {}

    def late_build(case, **kwargs):
        runtime, source, configured, token = build(case, **kwargs)
        record = source.entries[source.metric.latest_png['media_id']]['record']
        late['original_valid_until'] = record['metadata']['valid_until']
        case.f.now = record['metadata']['valid_until'] + LATE_S
        return runtime, source, configured, token

    with patch.object(metric_tests, 'approximate_configuration',
                      lambda case: successor_fixture.configuration(case, timing='development')), \
            patch.object(preview_tests, 'build', late_build):
        out = base.generate(options)
    arm = out['live']['arms']['B']; packet = arm['virtual_target_preview']
    assert out['now'] == late['original_valid_until'] + LATE_S  # Home's clock is past the original deadline
    assert packet['available'] and packet['evidence'] == 'supporting'
    assert packet['timing']['policy'] == 'development'
    assert 'media_expired' in {item['check'] for item in packet['timing']['warnings']}
    assert packet['valid_until'] <= late['original_valid_until'] < late['original_valid_until'] + LATE_S
    assert out['live']['apartment_viewing']['metric']['timing_policy'] == 'development'
    retention = out['descriptor']['retention']
    assert retention['policy'] == 'development' and retention['original_valid_until'] == late['original_valid_until']
    assert out['descriptor']['metadata']['valid_until'] == late['original_valid_until']
    out['development'] = dict(timing_policy='development', served_after_original_deadline_s=LATE_S,
        original_valid_until=late['original_valid_until'])
    return out


if __name__ == '__main__':
    raw = sys.stdin.buffer.read(16385)
    assert len(raw) <= 16384
    sys.stdout.write(json.dumps(generate(json.loads(raw)), sort_keys=True, separators=(',', ':'), allow_nan=False))
