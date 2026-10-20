"""Actual config4/Store/ASGI collector; fictional external clock/model I/O.

Artifact-only proposal. No production clock, camera, model, host or process is
used. The coordinator alone executes this module under the unchanged guard.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(r'C:\Claude\home')
for folder in (ROOT / 'tools/den-assessment', ROOT / 'tools/den-live-runtime',
               ROOT / 'stack/services/perception-lab', ROOT / 'stack/services/home-context'):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
import test_desk_method_bundled_export as fixture
import test_lab_bundled_rgb_stereo as generated
import lab_assessment_collect as collector
from bundle_rgb_input import host_execution_interval


class FrozenDeskMethodCollectionTests(unittest.TestCase):
    def inputs(self):
        f, _, _, verified, qualification = fixture.inputs(self)
        self.assertEqual(f.store.get_session(f.session['id'])['state'], 'recording')
        settings = fixture.actual.replace(f.store.settings, token_grants=({
            'subject': 'authored-candidate-reader', 'site_id': 'victoria',
            'token_sha256': hashlib.sha256(fixture.actual.legacy.TOKEN.encode()).hexdigest(),
            'capabilities': ['read']},))
        api = fixture.actual.legacy.ActualReadAPI(fixture.actual.create_app(
            settings=settings, runtime=f.runtime, store=f.store))
        return f, verified, qualification, api

    def collect(self, f, q, api, *, label='execution', clock_case=None, seconds=2):
        path = Path(f.directory.name) / (label + '.jsonl')
        # The measurement is new local work, after the original fixture's
        # complete NPZ read. Reuse actual protected-clock validators at this
        # event era; only protected-file/kernel points are external doubles.
        clock = generated.AuthoredBundledClock(b'', shift=Decimal('.10'))
        if clock_case == 'expired':
            clock.evidence['valid_until_monotonic_ns'] = clock.receiver_before.before_ns
        def context():
            camera, pilot = clock.context()
            if clock_case == 'replaced':
                calls = [0]
                def evidence(*_):
                    calls[0] += 1
                    value = deepcopy(clock.evidence)
                    if calls[0] > 1:
                        value['validation_id'] = 'replaced-authored-evidence'
                    return value
                camera.read_protected_json = evidence
            return camera, pilot
        checked = []
        class ReadBoundary:
            def __call__(_, method, suffix, body=None):
                # The actual first API read sees an already-durable freeze.
                if not checked:
                    first = json.loads(path.read_bytes().splitlines()[0])
                    self.assertEqual(first['kind'], 'exact_method_frozen')
                    self.assertEqual(first['value']['qualification'], q)
                    checked.append(True)
                return api(method, suffix, body)
            read_media = api.read_media
        with ExitStack() as stack:
            stack.enter_context(patch('bundle_rgb_input.clock_context', side_effect=context))
            if sys.platform == 'win32':
                stack.enter_context(patch('lab_whole_stack_trial.sync_parent'))
            result = collector.collect_existing(ReadBoundary(), session_id=f.session['id'],
                output_file=path, clock=lambda: f.now, seconds=seconds, max_attempts=1,
                collection_mode=collector.BUNDLED_MODE, frozen_method=q)
        self.assertTrue(checked)
        self.assertTrue(all(method == 'GET' for method, _ in api.calls))
        return path, result

    def read(self, path, q):
        # Historical replay must not consult the protected *current* clock.
        with patch('bundle_rgb_input.clock_context', side_effect=AssertionError('live clock during replay')):
            return collector.read_verified_method_executions(path,
                expected_qualification_sha256=collector.sha(collector.encoded(q)))

    def rows(self, path):
        return [json.loads(line) for line in path.read_bytes().splitlines()]

    def changed(self, path, rows, label):
        target = path.with_name(label + '.jsonl')
        with target.open('xb') as stream:
            stream.write(b''.join(collector.encoded(row) + b'\n' for row in rows))
        return target

    def test_actual_factory_measured_work_replays_same_sample_and_original_clock(self):
        f, old, q, api = self.inputs()
        q_before = deepcopy(q)
        path, result = self.collect(f, q, api)
        self.assertTrue(result['complete'], result)
        rows = self.rows(path)
        frozen_i = next(i for i, r in enumerate(rows) if r['kind'] == 'exact_method_frozen')
        self.assertTrue(all(i > frozen_i for i, r in enumerate(rows)
                            if r['kind'] in {'original_attempt', 'exact_method_execution'}))
        saved = next(r['value'] for r in rows if r['kind'] == 'exact_method_execution')
        reads = []
        original_read = Path.read_bytes
        def counted(selected):
            if selected == path:
                reads.append(selected)
            return original_read(selected)
        with patch.object(Path, 'read_bytes', counted):
            value = self.read(path, q)
        self.assertEqual(reads, [path])
        self.assertEqual(set(value), {'collection', 'original_family', 'input_binding', 'captures',
                                      'frozen_method', 'method_executions'})
        self.assertEqual(value['method_executions'], [saved])
        self.assertEqual(saved['status'], 'measured', saved)
        self.assertIsNone(saved['reason'])
        self.assertAlmostEqual(saved['sample']['camera_xyz_m'][2], 1.234, places=9)
        self.assertGreaterEqual(saved['sample']['support'], q['measurement_method']['sampling']['min_support'])
        self.assertEqual(saved['timing'], host_execution_interval(saved['clock_proof']))
        self.assertEqual(saved['timing']['completion_lower_unix_ns'], '1800000000699999000')
        self.assertEqual(saved['timing']['completion_upper_unix_ns'], '1800000000700001100')
        self.assertEqual(saved['original_event_sha256'], collector.sha(collector.encoded(f.row)))
        self.assertEqual(saved['source_event_id'], old['captures'][0]['source_event_id'])
        self.assertEqual(set(saved['media_refs']), set(generated.BASE_ROLES))
        self.assertIsNone(saved['operational_geometry'])
        self.assertTrue(all(saved[k] is False for k in ('geometry_authority', 'identity_authority',
            'protection_authority', 'beam_authority', 'physical_authority')))
        self.assertEqual(q, q_before)
        value['method_executions'][0]['sample'].clear()
        self.assertEqual(self.read(path, q)['method_executions'], [saved])

    def test_exact_selected_roi_numeric_miss_remains_a_clocked_outcome(self):
        f, _, q, api = self.inputs()
        # A radius-one depth neighbourhood has at most nine points. A frozen
        # valid requirement of256 therefore misses without altering originals,
        # the sampler, prediction, projection or qualification thresholds.
        q['measurement_method']['sampling'].update(radius_px=1, min_support=256)
        q['coverage']['measurement_method_ref']['digest'] = fixture.export.digest(q['measurement_method'])
        path, result = self.collect(f, q, api)
        self.assertTrue(result['complete'], result)
        value = self.read(path, q)
        self.assertEqual(len(value['method_executions']), 1)
        row = value['method_executions'][0]
        self.assertEqual(row['status'], 'refused')
        self.assertEqual(row['reason'], 'visible_nose_depth_unavailable')
        self.assertIsNone(row['sample'])
        self.assertIsNotNone(row['clock_proof'])
        self.assertEqual(row['timing'], host_execution_interval(row['clock_proof']))
        self.assertEqual(len(value['captures']), 1)

    def test_expired_or_replaced_current_clock_is_unqualified_not_service(self):
        f, _, q, api = self.inputs()
        for kind in ('expired', 'replaced'):
            with self.subTest(kind=kind):
                path, result = self.collect(f, q, api, label=kind, clock_case=kind)
                self.assertTrue(result['complete'], result)
                row = self.read(path, q)['method_executions'][0]
                self.assertEqual(row['status'], 'refused')
                self.assertIsNotNone(row['reason'])
                self.assertIsNone(row['clock_proof'])
                self.assertIsNone(row['timing'])
                self.assertFalse(row['geometry_authority'])

    def test_missing_terminal_keeps_unresolved_original_without_invented_capture(self):
        f, original, q, api = self.inputs()
        terminal_id = original['captures'][0]['capture_terminal']['id']
        class MissingTerminal:
            def __call__(_, method, suffix, body=None):
                value = api(method, suffix, body)
                if '/events?' in suffix:
                    value['items'] = [row for row in value['items'] if row['id'] != terminal_id]
                return value
            read_media = api.read_media
            calls = api.calls
        # The bundled start identifies B, not C. Without its terminal/support
        # no capture may be attributed to the actual model output, even when
        # it is tempting to infer one from this single-person fixture.
        path, result = self.collect(f, q, MissingTerminal(), seconds=2)
        self.assertFalse(result['complete'], result)
        value = self.read(path, q)
        self.assertEqual(value['collection']['attempts'], [])
        self.assertEqual(value['method_executions'], [])
        self.assertEqual(value['captures'], [])
        summary = value['collection']['summary']
        self.assertEqual(summary['unresolved_window_outputs'], 1)
        self.assertEqual(summary['missing_capture_terminals'], 1)
        self.assertEqual(summary['unprocessed_matching_outputs'], 0)
        rows = self.rows(path)
        original = next(r['value']['event'] for r in rows if r['kind'] == 'original_attempt')
        self.assertEqual(original, f.row)
        self.assertEqual([r['value']['event_id'] for r in rows if r['kind'] == 'unresolved_window'], [f.row['id']])

    def test_changed_method_completion_original_or_missing_outcome_refuses(self):
        f, _, q, api = self.inputs()
        path, result = self.collect(f, q, api)
        self.assertTrue(result['complete'], result)
        original = self.rows(path)
        for kind in ('method', 'completion', 'clock-proof', 'original', 'missing-outcome', 'late-freeze'):
            rows = deepcopy(original)
            frozen = next(r['value'] for r in rows if r['kind'] == 'exact_method_frozen')
            outcome = next(r['value'] for r in rows if r['kind'] == 'exact_method_execution')
            if kind == 'method':
                frozen['qualification']['measurement_method']['implementation_sha256']['lab_live_desk_anchor.py'] = 'f' * 64
            elif kind == 'completion':
                outcome['timing']['completion_upper_unix_ns'] = str(int(outcome['timing']['completion_upper_unix_ns']) - 1)
            elif kind == 'clock-proof':
                outcome['clock_proof']['after']['unix_ns'] += 10**9
            elif kind == 'original':
                next(r['value'] for r in rows if r['kind'] == 'original_attempt')['event']['payload']['frame_id'] = 'foreign-frame'
            elif kind == 'missing-outcome':
                rows = [r for r in rows if r['kind'] != 'exact_method_execution']
            else:
                frozen_row = next(r for r in rows if r['kind'] == 'exact_method_frozen')
                rows.remove(frozen_row)
                first_prediction = next(i for i, r in enumerate(rows) if r['kind'] == 'original_attempt')
                rows.insert(first_prediction + 1, frozen_row)
            changed = self.changed(path, rows, kind)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.read(changed, q)
        with self.assertRaises(ValueError):
            collector.read_verified_method_executions(path, expected_qualification_sha256='f' * 64)

    def test_foreign_method_source_pin_refuses_before_any_api_read(self):
        f, _, q, api = self.inputs()
        q['measurement_method']['implementation_sha256']['lab_live_desk_anchor.py'] = 'f' * 64
        q['coverage']['measurement_method_ref']['digest'] = fixture.export.digest(q['measurement_method'])
        before = list(api.calls)
        path = Path(f.directory.name) / 'refused-before-read.jsonl'
        with self.assertRaisesRegex(ValueError, 'operational_measurement_implementation_changed'):
            collector.collect_existing(api, session_id=f.session['id'], output_file=path,
                clock=lambda: f.now, seconds=2, max_attempts=1,
                collection_mode=collector.BUNDLED_MODE, frozen_method=q)
        self.assertEqual(api.calls, before)
        self.assertFalse(path.exists())

    def test_historical_readers_keep_exact_shapes_and_unfrozen_file_is_not_execution(self):
        f, _, q, api = self.inputs()
        old_path = Path(f.directory.name) / 'authored-bundled-assessment.jsonl'
        ordinary = collector.read_verified_attempts(old_path)
        self.assertEqual(set(ordinary), {'binding', 'attempts', 'summary', 'collection_sha256'})
        bundled = collector.read_verified_bundled_originals(old_path)
        self.assertEqual(set(bundled), {'collection', 'original_family', 'input_binding', 'captures'})
        with self.assertRaisesRegex(ValueError, 'one_independently_pinned_execution_method_required'):
            self.read(old_path, q)
        path, result = self.collect(f, q, api)
        self.assertTrue(result['complete'], result)
        self.assertEqual(set(collector.read_verified_attempts(path)), set(ordinary))
        self.assertEqual(set(collector.read_verified_bundled_originals(path)), set(bundled))


if __name__ == '__main__':
    unittest.main()
