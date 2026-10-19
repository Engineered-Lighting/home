import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from analysis import SequentialAnalysis
from analysis_jobs import AnalysisJobs
from config import Settings
from controller import Controller
from managed_pair import ManagedPair
from qwen_transport import saved_analysis
from qwen_adapter import MODEL, PROMPT_VERSION
from store import LabError, Store


class Client:
    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.fail_stop = False
        self.state = 'ready'
        self.reason = None

    def start(self, request_id, session_id,variant='detector'):
        self.variant=variant
        self.starts += 1
        return self.status(request_id)

    def status(self, request_id):
        return {'state': self.state, 'expires_at': time.time() + 100,
                'engine_epoch': 'test-epoch', 'profile': {}, 'reason': self.reason}

    def stop(self, request_id, epoch):
        self.stops += 1
        if self.fail_stop:
            raise TimeoutError()
        self.state = 'stopped'
        return self.status(request_id)


class Inner:
    def start_session(self, session):
        return {'state': 'recording', 'inference': 'ready'}

    def stop_session(self, session):
        return {'state': 'stopped'}

    def close(self, timeout=5):
        return {'drained': True}


class ManagedTests(unittest.TestCase):
    def test_pose_recipe_selects_both_workers_and_matching_factory(self):
        self.runtime.catalog.append({'id':'placement-best','status':'unverified'})
        selected=[]
        self.runtime.factory=lambda receipts,experiment_id: selected.append(experiment_id) or Inner()
        session=self.controller.start({**self.body,'experiment_id':'placement-best'})
        self.settle('recording',session['id'])
        self.assertEqual(selected,['placement-best'])
        self.assertEqual([c.variant for c in self.clients.values()],['pose','pose'])
        self.controller.stop(session['id']);self.settle('stopped',session['id'])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Settings(Path(self.temp.name), max_session_seconds=60,
                                   cameras=({'id': 'den'},)))
        self.clients = {a: Client() for a in ('a', 'b')}
        self.runtime = ManagedPair(self.store, self.clients, lambda _: Inner(),
                                   [{'id': 'placement-same'}], prepare_seconds=.1)
        self.controller = Controller(self.store, self.runtime)
        self.body = dict(camera_id='den', experiment_id='placement-same', name='test', request_id='start1')

    def tearDown(self):
        for c in self.clients.values():
            c.fail_stop = False
        self.runtime.close()
        self.store.close()
        self.temp.cleanup()

    def settle(self, state, sid):
        until = time.monotonic() + 5  # cleanup drains first (up to 5 s) on a loaded host
        while time.monotonic() < until:
            if self.store.get_session(sid)['state'] == state:
                return
            time.sleep(.01)
        self.fail(self.store.get_session(sid)['state'])

    def test_browsing_does_not_launch_and_duplicate_start_does_not_repeat(self):
        self.controller.capabilities()
        self.assertEqual(sum(c.starts for c in self.clients.values()), 0)
        session = self.controller.start(self.body)
        self.settle('recording', session['id'])
        self.controller.start(self.body)
        self.assertEqual([c.starts for c in self.clients.values()], [1, 1])
        self.controller.stop(session['id'])
        self.settle('stopped', session['id'])

    def test_cancel_during_preparation_cleans_both_workers(self):
        for c in self.clients.values():
            c.state = 'starting'
        session = self.controller.start(self.body)
        self.controller.stop(session['id'])
        self.settle('stopped', session['id'])
        with self.runtime.lock:
            self.assertIsNone(self.runtime.current)

    def test_failed_cleanup_keeps_admission_closed(self):
        session = self.controller.start(self.body)
        self.settle('recording', session['id'])
        self.clients['b'].fail_stop = True
        self.controller.stop(session['id'])
        self.settle('indeterminate', session['id'])
        self.assertIsNotNone(self.runtime.current)
        self.assertEqual(self.runtime.start_session(session)['reason'], 'worker_cleanup_pending')

    def phase(self, phase):
        until = time.monotonic() + 3
        while time.monotonic() < until:
            with self.runtime.lock:
                item = self.runtime.current
                if (item['phase'] if item else None) == phase and not (item and item['thread'].is_alive()):
                    return
            time.sleep(.01)
        self.fail(self.runtime.current and self.runtime.current['phase'])

    def test_recorder_stop_first_with_slow_local_drain_does_not_wedge(self):
        class SlowInner(Inner):
            drained = False
            closes = 0
            def close(inner, timeout=5):
                inner.closes += 1
                return {'drained': inner.drained}  # e.g. a JEV future still in flight
        inner = SlowInner()
        self.runtime.factory = lambda _: inner
        session = self.controller.start(self.body)
        self.settle('recording', session['id'])
        # The recorder monitor finalizes first; no UI Stop has happened.
        self.store.transition(session['id'], 'stopped', 'source_ended')
        self.controller.expire()
        self.phase('draining')
        self.assertEqual([c.stops for c in self.clients.values()], [1, 1])
        self.assertEqual(self.store.get_session(session['id'])['state'], 'stopped')
        capabilities = self.controller.capabilities()
        self.assertFalse(capabilities['recording_allowed'])
        self.assertIn({'id': 'workers', 'state': 'blocked', 'reason': 'local_drain_pending'}, capabilities['gates'])
        self.assertEqual(self.runtime.start_session(session)['reason'], 'worker_cleanup_pending')
        # Stop on the already-stopped session retries only the pending cleanup.
        self.controller.stop(session['id'])
        self.phase('draining')
        self.assertEqual([c.stops for c in self.clients.values()], [1, 1])
        self.controller.expire()  # still busy: stays closed, no worker traffic
        self.phase('draining')
        inner.drained = True
        self.controller.expire()
        self.phase(None)
        self.assertTrue(self.controller.capabilities()['recording_allowed'])
        kinds = [e['kind'] for e in self.store.list_events(session['id'])]
        self.assertIn('workers.local_drained', kinds)
        cleanup = [e['payload'] for e in self.store.list_events(session['id']) if e['kind'] == 'workers.cleanup']
        self.assertEqual({(c['state'], c['local_drain']) for c in cleanup}, {('stopped', 'pending')})
        self.assertEqual(inner.closes, 4)
        for client in self.clients.values():
            client.state = 'ready'  # fresh worker leases for the next session
        following = self.controller.start({**self.body, 'request_id': 'start2'})
        self.settle('recording', following['id'])
        self.controller.stop(following['id'])
        self.settle('stopped', following['id'])

    def test_user_stop_waits_for_drain_before_marking_stopped(self):
        class SlowInner(Inner):
            drained = False
            def close(inner, timeout=5):
                return {'drained': inner.drained}
        inner = SlowInner()
        self.runtime.factory = lambda _: inner
        session = self.controller.start(self.body)
        self.settle('recording', session['id'])
        self.controller.stop(session['id'])
        self.phase('draining')
        self.assertEqual(self.store.get_session(session['id'])['state'], 'stopping')
        self.assertEqual(self.runtime.live(session)['reason'], 'local_drain_pending')
        inner.drained = True
        self.controller.expire()
        self.settle('stopped', session['id'])
        self.phase(None)

    def test_precise_worker_gate_reason_reaches_cleanup_event_and_capabilities(self):
        self.clients['a'].state, self.clients['a'].reason = 'failed', 'preflight_memory'
        session = self.controller.start(self.body)
        self.settle('stopped', session['id'])
        self.assertEqual(self.store.get_session(session['id'])['reason'], 'worker_preparation_failed')
        cleanup = [e['payload'] for e in self.store.list_events(session['id']) if e['kind'] == 'workers.cleanup']
        self.assertEqual(cleanup[-1]['workers']['a'], {'state': 'failed', 'reason': 'preflight_memory'})
        last = self.controller.capabilities()['last_worker_cleanup']
        self.assertEqual((last['session_id'], last['state'], last['workers']['a']['reason']),
                         (session['id'], 'stopped', 'preflight_memory'))



PROFILE = {'model_version': MODEL, 'served_model': 'qwen3-vl-30b', 'artifact_sha256': 'a' * 64,
           'engine_epoch': 'epoch1', 'runtime_revision': 'fixture-runtime', 'adapter_revision': PROMPT_VERSION}
TRANSPORT = {'hostname': 'la-lab.example', 'port': 9447, 'ca_file': '/fixture/ca.pem',
             'client_certificate': '/fixture/client.pem', 'client_key': '/fixture/client.key',
             'deadline_seconds': 15, 'response_grace_seconds': 5, 'poll_seconds': .2}


class ManagedAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Settings(Path(self.temp.name), max_session_seconds=60, cameras=({'id': 'den'},)))
        self.clients = {a: Client() for a in ('a', 'b')}
        self.body = dict(camera_id='den', experiment_id='placement-same', name='test', request_id='start1')
        self.saved = self.saved_session()
        self.catalog = [{'id': 'placement-same'}, {'id': 'qwen-evidence', 'status': 'blocked',
                                                   'readiness_reason': 'experiment_pair_not_provisioned'}]
        self.runtime = None

    def tearDown(self):
        if self.runtime is not None:
            self.runtime.close(timeout=2)
        self.store.close()
        self.temp.cleanup()

    def saved_session(self):
        from PIL import Image
        session, _ = self.store.create_session('den', 'placement-same', 'saved', 'saved1')
        self.store.transition(session['id'], 'starting')
        self.store.transition(session['id'], 'recording')
        encoded = io.BytesIO()
        Image.new('RGB', (64, 36), (10, 20, 30)).save(encoded, format='JPEG')
        started = self.store.get_session(session['id'])['started_at']
        self.media = self.store.add_media(session['id'], encoded.getvalue(), 'image/jpeg', {
            'site_id': 'victoria', 'camera_id': 'den', 'stream': 'rgb', 'frame_id': 'f1', 'capture_epoch': 'c1',
            'observed_at': started + 2, 'available_at': started + 2.1, 'uncertainty_seconds': .01,
            'clock_mapping_status': 'verified', 'clock_mapping_id': 'fixture', 'width': 64, 'height': 36})
        self.store.transition(session['id'], 'stopped')
        with self.store.transaction() as db:
            db.execute('UPDATE sessions SET ended_at=? WHERE id=?', (started + 10, session['id']))
        return self.store.get_session(session['id'])

    def pair(self, execute, **kwargs):
        frames = [{'media_id': self.media['id'], 'site_id': 'victoria', 'session_id': self.saved['id'],
                   'observed_at': self.media['metadata']['observed_at'], 'available_at': self.media['metadata']['available_at']}]
        jobs = AnalysisJobs(self.store, SequentialAnalysis(self.store, PROFILE, execute), lambda _: (frames, []), **kwargs)
        self.runtime = ManagedPair(self.store, self.clients, lambda _: Inner(), self.catalog, prepare_seconds=5, analysis_jobs=jobs)
        self.controller = Controller(self.store, self.runtime)
        return jobs

    def response(self, request):
        return {'request_id': request['request_id'], 'terminal': True, 'state': 'completed',
                'engine_epoch': 'epoch1', 'artifact_sha256': 'a' * 64, 'result': None}

    def settle(self, predicate, seconds=5):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if predicate():
                return
            time.sleep(.01)
        self.fail('fixture work did not settle')

    def analyse(self, request_id):
        return self.controller.job({'kind': 'analysis', 'request_id': request_id, 'session_id': self.saved['id'],
                                    'parameters': {'run_ids': ['qBase'], 'mode': 'causal'}})

    def test_absent_qwen_provisioning_reports_unavailable_without_crashing(self):
        self.runtime = ManagedPair(self.store, self.clients, lambda _: Inner(), self.catalog, prepare_seconds=.1)
        self.controller = Controller(self.store, self.runtime)
        capabilities = self.runtime.capabilities()
        self.assertEqual((capabilities['analysis']['available'], capabilities['analysis']['reason']),
                         (False, 'qwen_admission_not_provisioned'))
        self.assertEqual(capabilities['analysis']['unsupported_runs'], {'qJev': 'qwen_assisted_jev_not_implemented'})
        self.assertIn({'id': 'saved_analysis', 'state': 'unavailable', 'reason': 'qwen_admission_not_provisioned'}, capabilities['gates'])
        recipe = next(r for r in capabilities['experiments'] if r['id'] == 'qwen-evidence')
        self.assertEqual((recipe['status'], recipe['readiness_reason']), ('blocked', 'qwen_admission_not_provisioned'))
        self.assertTrue(self.controller.capabilities()['recording_allowed'])
        job = self.analyse('absent1')
        self.assertEqual((job['state'], job['error']), ('blocked', 'qwen_admission_not_provisioned'))
        with self.assertRaisesRegex(ValueError, 'together'):
            saved_analysis(self.store, PROFILE, None)

    def test_analysis_is_refused_while_preparing_or_recording_then_runs(self):
        calls = []
        self.pair(lambda request: calls.append(request) or self.response(request))
        recipe = next(r for r in self.runtime.capabilities()['experiments'] if r['id'] == 'qwen-evidence')
        self.assertEqual((recipe['status'], recipe['readiness_reason']), ('unverified', 'saved_analysis_on_request'))
        for c in self.clients.values():
            c.state = 'starting'
        session = self.controller.start(self.body)
        job = self.analyse('during-preparation')
        self.assertEqual((job['state'], job['error']), ('blocked', 'worker_preparation_active'))
        for c in self.clients.values():
            c.state = 'ready'
        self.settle(lambda: self.store.get_session(session['id'])['state'] == 'recording')
        job = self.analyse('during-recording')
        self.assertEqual((job['state'], job['error']), ('blocked', 'paired_recording_active'))
        analysis = self.runtime.capabilities()['analysis']
        self.assertEqual((analysis['available'], analysis['reason']), (False, 'paired_recording_active'))
        self.controller.stop(session['id'])
        self.settle(lambda: self.store.get_session(session['id'])['state'] == 'stopped' and self.runtime.current is None)
        job = self.analyse('after-recording')
        self.settle(lambda: self.store.get_job(job['id'])['state'] == 'completed')
        self.assertEqual(len(calls), 2)
        self.assertTrue(self.runtime.capabilities()['analysis']['available'])

    def test_recording_start_interrupts_analysis_before_its_next_step(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def execute(request):
            calls.append(request)
            started.set()
            release.wait(3)
            return self.response(request)
        self.pair(execute)
        job = self.analyse('interrupted1')
        self.assertTrue(started.wait(2))
        session = self.controller.start(self.body)
        self.settle(lambda: self.store.get_session(session['id'])['state'] == 'recording')
        release.set()
        self.settle(lambda: self.store.get_job(job['id'])['state'] == 'interrupted')
        current = self.store.get_job(job['id'])
        self.assertEqual((current['error'], current['result']['dispatched'], len(calls)), ('paired_recording_active', True, 1))
        self.controller.stop(session['id'])
        self.settle(lambda: self.store.get_session(session['id'])['state'] == 'stopped')

    def test_active_analysis_guards_deletion_and_close_reports_drain(self):
        started, release = threading.Event(), threading.Event()
        def execute(request):
            started.set()
            release.wait(3)
            return self.response(request)
        self.pair(execute)
        job = self.analyse('guarded1')
        self.assertTrue(started.wait(2))
        with self.assertRaises(LabError) as refused:
            self.runtime.delete_session(self.saved, self.store.get_session(self.saved['id'])['revision'])
        self.assertEqual(refused.exception.code, 'execution_pending')
        self.assertTrue(any(j['worker_active'] for j in self.runtime.list_jobs(self.saved['id'])))
        self.assertEqual(self.runtime.close(timeout=.05), {'drained': False, 'state': 'analysis_step_pending'})
        release.set()
        self.settle(lambda: self.runtime.close(timeout=.05)['drained'])
        self.assertEqual(self.store.get_job(job['id'])['state'], 'cancelled')
        self.runtime = None

    def test_operator_diagnostic_job_reconciles_and_other_diagnostics_stay_blocked(self):
        def timeout(request):
            raise TimeoutError()
        lookups = []
        def lookup(request, reference):
            lookups.append(request['request_id'])
            return self.response(request)
        self.pair(timeout, lookup=lookup)
        self.settle(lambda: not self.runtime.analysis.active())
        job = self.analyse('uncertain1')
        self.settle(lambda: self.store.get_job(job['id'])['state'] == 'indeterminate')
        self.assertEqual(self.runtime.capabilities()['analysis']['uncertain_steps'], 1)
        hook = self.controller.job({'kind': 'diagnostic', 'request_id': 'reconcile1',
                                    'parameters': {'operation': 'analysis_reconcile', 'job_id': job['id']}})
        self.assertEqual(hook['state'], 'queued')
        self.settle(lambda: self.store.get_job(hook['id'])['state'] == 'completed')
        self.assertEqual(self.store.get_job(job['id'])['state'], 'interrupted')
        self.assertEqual(len(lookups), 1)
        other = self.controller.job({'kind': 'diagnostic', 'request_id': 'other1', 'parameters': {'operation': 'restart_engine'}})
        self.assertEqual((other['state'], other['error']), ('blocked', 'job_handler_not_provisioned'))

    def test_provisioned_analysis_reconciles_an_engine_restart_at_controller_start(self):
        class Transport:
            def __init__(inner, config):
                inner.config, inner.sent, inner.lookups = config, [], []
                inner.epoch = 'epoch1'
            def __call__(inner, envelope):
                inner.sent.append(envelope)
                raise TimeoutError()
            def lookup(inner, reference):
                inner.lookups.append(reference)
                return {'request_id': reference['request_id'], 'body_sha256': reference['body_sha256'], 'terminal': False,
                        'state': 'not_found', 'engine_epoch': inner.epoch, 'artifact_sha256': 'a' * 64}
        made = []
        def factory(config):
            made.append(Transport(config))
            return made[-1]
        jobs = saved_analysis(self.store, PROFILE, TRANSPORT, transport_factory=factory)
        self.assertEqual(made[0].config['deadline_seconds'], 15)
        self.runtime = ManagedPair(self.store, self.clients, lambda _: Inner(), self.catalog, prepare_seconds=.1, analysis_jobs=jobs)
        self.controller = Controller(self.store, self.runtime)
        job = self.analyse('restart1')
        self.settle(lambda: self.store.get_job(job['id'])['state'] == 'indeterminate')
        self.assertEqual(len(made[0].sent), 1)
        self.assertNotIn('body', made[0].lookups[0] if made[0].lookups else {})
        self.runtime.close(timeout=2)
        # The LA engine restarted: the operator provisions the new epoch and
        # the controller restarts. Startup reconcile runs lookups only.
        rotated = saved_analysis(self.store, {**PROFILE, 'engine_epoch': 'epoch2'}, TRANSPORT, transport_factory=factory)
        made[-1].epoch = 'epoch2'
        self.runtime = ManagedPair(self.store, self.clients, lambda _: Inner(), self.catalog, prepare_seconds=.1, analysis_jobs=rotated)
        self.settle(lambda: self.store.get_job(job['id'])['state'] == 'failed')
        self.assertEqual(self.store.get_job(job['id'])['error'], 'analysis_engine_epoch_ended')
        self.assertEqual(made[-1].sent, [])
        self.assertEqual(len(made[-1].lookups), 1)
        self.assertNotIn('body', made[-1].lookups[0])
        self.assertEqual(self.runtime.capabilities()['analysis']['uncertain_steps'], 0)

    def test_a_rotated_la_engine_needs_no_reprovisioning_for_new_jobs(self):
        class Transport:
            def __init__(inner, config):
                inner.sent, inner.lookups, inner.closed = [], [], False
            def __call__(inner, envelope):
                inner.sent.append(envelope)
                return {'request_id': envelope['request_id'], 'body_sha256': envelope['body_sha256'], 'terminal': True,
                        'state': 'completed', 'engine_epoch': 'epoch2', 'artifact_sha256': 'a' * 64,
                        'response': {'model': 'qwen3-vl-30b', 'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{}'}}]}}
            def lookup(inner, reference):
                inner.lookups.append(reference)
                return {'request_id': reference['request_id'], 'body_sha256': reference['body_sha256'], 'terminal': False,
                        'state': 'not_found', 'engine_epoch': 'epoch2', 'artifact_sha256': 'a' * 64}
            def close(inner):
                inner.closed = True
        made = []
        jobs = saved_analysis(self.store, PROFILE, TRANSPORT, transport_factory=lambda config: made.append(Transport(config)) or made[-1])
        self.assertIsNotNone(jobs.stop)
        self.runtime = ManagedPair(self.store, self.clients, lambda _: Inner(), self.catalog, prepare_seconds=.1, analysis_jobs=jobs)
        self.controller = Controller(self.store, self.runtime)
        job = self.analyse('rotated1')
        self.settle(lambda: self.store.get_job(job['id'])['state'] == 'completed')
        self.assertEqual({e['profile']['engine_epoch'] for e in made[0].sent}, {'epoch2'})
        self.assertEqual(len(made[0].sent), 2)

if __name__ == '__main__':
    unittest.main()
