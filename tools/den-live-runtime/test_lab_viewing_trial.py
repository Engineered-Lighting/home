"""No-network public API session lifecycle tests; no model/media qualification."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('lab_viewing_trial_test_target', HERE / 'lab_viewing_trial.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
WHOLE = MODULE.incumbent(HERE / 'lab_whole_stack_trial.py')
REQUEST = '2f217859-efb1-48f8-9193-2695d3a46401'


class Clock:
    at = 0.
    def now(self): return self.at
    def sleep(self, seconds): self.at += seconds


class API:
    def __init__(self, clock, *, live_failure=False, lost_create=False):
        self.clock, self.live_failure, self.lost_create = clock, live_failure, lost_create
        self.calls, self.created, self.stopped = [], False, False
        self.session = dict(id='authored-session', request_id=REQUEST, camera_id='den',
            experiment_id='placement-best', site_id='victoria', generation=2, state='recording')
        self.event = dict(id=1, session_id='authored-session', kind='perception.output',
            payload=dict(arm='b', status='completed', result={'pose_instances': []}, frame_id='real-original-scope'))
    def __call__(self, method, suffix, body=None):
        self.calls.append((self.clock.at, method, suffix, body))
        if suffix == '/capabilities':
            return dict(site_id='victoria', fixture=False, recording_allowed=True,
                experiments=[dict(id='placement-best', status='ready')], worker_mode='on_demand_bounded',
                storage=dict(max_session_seconds=300), reasoning=dict(available=True, mode='explicit_bounded_session',
                    session_seconds=300, max_session_calls=600, max_calls_per_minute=120))
        if suffix == '/jobs': return {'items': []}
        if suffix == '/sessions' and method == 'GET': return {'items': [dict(self.session)] if self.created else []}
        if suffix == '/sessions' and method == 'POST':
            self.created = True
            if self.lost_create: raise TimeoutError('lost acknowledgement')
            return dict(self.session)
        if suffix.endswith('/stop'):
            self.stopped = True
            self.session['state'] = 'stopped'
            return dict(self.session)
        if suffix.endswith('/live'):
            if self.live_failure and not self.stopped and self.clock.at > 0:
                raise TimeoutError('media or live unavailable')
            return {'session': dict(self.session)}
        if '/events?' in suffix:
            cursor = int(suffix.split('after_id=')[1].split('&')[0])
            cleanup = dict(id=2, session_id=self.session['id'], kind='workers.cleanup', payload=dict(
                state='stopped', local_drain='drained', request_id=REQUEST,
                workers={arm: {'state': 'stopped'} for arm in ('a', 'b')}))
            rows = [event for event in ([self.event, cleanup] if self.stopped else [self.event]) if event['id'] > cursor]
            return {'items': rows, 'after_id': rows[-1]['id'] if rows else cursor}
        raise AssertionError((method, suffix))


class ViewingTests(unittest.TestCase):
    def trial(self, **options):
        clock = Clock(); api = API(clock, **options); retained = []; notes = []
        result = MODULE.run_viewing(api, WHOLE.Plan(REQUEST), lambda kind, value: retained.append((kind, value)),
            notes.append, monotonic=clock.now, sleep=clock.sleep)
        return result, api, notes, retained

    def test_full_30_seconds_and_early_session_notification_without_capture_oracle(self):
        result, api, notes, retained = self.trial()
        self.assertEqual(result['outcome'], 'bounded_viewing_complete')
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(next(c[0] for c in api.calls if c[2].endswith('/stop')), 30)
        self.assertEqual(notes[1]['status'], 'recording')
        self.assertEqual(notes[1]['session_id'], 'authored-session')
        self.assertFalse(any('/media/' in c[2] or c[2].endswith('/graph') for c in api.calls))
        self.assertTrue(any(kind == 'response' and value.get('body', {}).get('items') == [api.event] for kind, value in retained))

    def test_live_or_image_failure_after_start_does_not_shorten_recording_or_stop_metadata_cleanup(self):
        result, api, notes, retained = self.trial(live_failure=True)
        self.assertEqual(next(c[0] for c in api.calls if c[2].endswith('/stop')), 30)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertTrue(any(kind == 'poll_unavailable' for kind, value in retained))

    def test_lost_create_ack_reconciles_same_request_without_second_create_and_stops(self):
        result, api, notes, retained = self.trial(lost_create=True)
        self.assertEqual(len([c for c in api.calls if c[1:3] == ('POST', '/sessions')]), 1)
        self.assertEqual(result['session_id'], 'authored-session')
        self.assertTrue(result['cleanup_confirmed'])
        self.assertFalse(result['recording_completed'])

    def test_retention_failure_cannot_suppress_owned_stop(self):
        clock = Clock(); api = API(clock); notes = []; count = [0]
        def retain(kind, value):
            count[0] += 1
            if count[0] > 4: raise OSError('private disk full')
        result = MODULE.run_viewing(api, WHOLE.Plan(REQUEST), retain, notes.append,
            monotonic=clock.now, sleep=clock.sleep)
        self.assertTrue(api.stopped); self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual(result['outcome'], 'incomplete')

    def test_existing_active_session_or_consumed_request_refuses_without_create(self):
        for state in ('recording', 'indeterminate', 'stopped'):
            clock = Clock(); api = API(clock); api.created = True; api.session['state'] = state
            result = MODULE.run_viewing(api, WHOLE.Plan(REQUEST), lambda *args: None, lambda *args: None,
                monotonic=clock.now, sleep=clock.sleep)
            self.assertEqual(result['error'], 'request_id_consumed_reconcile_only')
            self.assertFalse(any(c[1] == 'POST' for c in api.calls))

    def test_actual_store_default_zero_media_generation_starting_then_recording_remains_bound(self):
        service = HERE.parents[1] / 'stack/services/perception-lab'
        if str(service) not in sys.path: sys.path.append(str(service))
        from config import Settings
        from store import Store
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Settings(Path(directory), min_free_bytes=0, cameras=({'id': 'den'},), developer_fixture_mode=True))
            self.addCleanup(store.close)
            clock = Clock()
            class StoreAPI(API):
                def __call__(self, method, suffix, body=None):
                    if method == 'POST' and suffix == '/sessions':
                        self.calls.append((clock.at, method, suffix, body)); self.created = True
                        row, fresh = store.create_session(body['camera_id'], body['experiment_id'], body['name'], body['request_id'], True)
                        assert fresh and row['generation'] == 0
                        store.transition(row['id'], 'starting')
                        self.session = store.get_session(row['id']); self.event['session_id'] = row['id']
                        return dict(self.session)
                    if suffix.endswith('/live') and self.session['state'] == 'starting':
                        store.transition(self.session['id'], 'recording'); self.session = store.get_session(self.session['id'])
                    return super().__call__(method, suffix, body)
            api = StoreAPI(clock); notes = []
            result = MODULE.run_viewing(api, WHOLE.Plan(REQUEST), lambda *args: None, notes.append,
                monotonic=clock.now, sleep=clock.sleep)
            self.assertEqual(result['outcome'], 'bounded_viewing_complete')
            self.assertEqual(notes[0]['generation'], 0)
            self.assertEqual(notes[1]['generation'], 0)
            self.assertEqual(next(c[0] for c in api.calls if c[2].endswith('/stop')), 30)
            store.close()


if __name__ == '__main__': unittest.main()
