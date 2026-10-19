"""Launch-free setup readiness: host probes, cache, catalog annotation, wiring."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import ssl
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import jetson_lifecycle
from jetson_lifecycle import JetsonLauncher, JetsonLifecycleClient
from managed_readiness import ManagedReadiness, ReadinessPair, prerequisites
from worker_lifecycle_client import PROBE_REQUEST, LifecycleClient

GIB = 1024**3
RECIPES = json.loads(Path(__file__).with_name('experiments.json').read_text())['experiments']
BOUND = {'placement-same', 'placement-best'}


class Reply:
    """One fixed lifecycle response; records what the client sent."""
    def __init__(self, status=404, body=b'{"reason": "request_not_found"}', error=None):
        self.status, self.body, self.error, self.requests, self.closed = status, body, error, [], False
    def request(self, *args):
        self.requests.append(args)
        if self.error:
            raise self.error
    def getresponse(self):
        return self
    def read(self, maximum):
        return self.body[:maximum]
    def close(self):
        self.closed = True


class Supervisor:
    """Routes the client's request into the real LA supervisor ASGI app."""
    def __init__(self, app):
        self.app, self.requests = app, []
    def request(self, method, path, body, headers):
        self.requests.append((method, path, body, headers))
        messages = []
        async def receive():
            return {'type': 'http.request', 'body': body or b'', 'more_body': False}
        async def send(value):
            messages.append(value)
        asyncio.run(self.app({'type': 'http', 'scheme': 'https', 'method': method, 'path': path, 'query_string': b'',
                              'headers': [(k.lower().encode(), v.encode()) for k, v in headers.items()]}, receive, send))
        self.status, self.body = messages[0]['status'], messages[1]['body']
    def getresponse(self):
        return self
    def read(self, maximum):
        return self.body[:maximum]
    def close(self):
        pass


def la_client(reply, credential=lambda: 'x' * 40):
    return LifecycleClient('https://192.168.0.100:9443', credential, None, 'echo',
                           connection_factory=lambda: (_ for _ in ()).throw(AssertionError('probe used the start connection')),
                           probe_connection_factory=lambda: reply)


class LAProbeTests(unittest.TestCase):
    def test_reachable_supervisor_is_one_authenticated_status_read(self):
        reply = Reply()
        client = la_client(reply)
        self.assertEqual(client.probe(), [])
        self.assertEqual(reply.requests, [('GET', '/v1/lifecycle/requests/' + PROBE_REQUEST, None,
                                           {'Authorization': 'Bearer ' + 'x' * 40})])
        self.assertTrue(reply.closed)
        # A probe is not a lifecycle request: status/stop still refuse it.
        self.assertEqual(client.sessions, {})
        with self.assertRaisesRegex(ValueError, 'Unknown lifecycle request'):
            client.status(PROBE_REQUEST)

    def test_every_other_outcome_is_an_explicit_reason(self):
        receipt = json.dumps({'version': 1, 'request_id': PROBE_REQUEST, 'state': 'ready'}).encode()
        for reply, code in ((Reply(503, b'{"reason": "supervisor_unavailable"}'), 'la_supervisor_unavailable'),
                            (Reply(401, b'{"reason": "unauthorized"}'), 'la_credential_rejected'),
                            (Reply(200, receipt), 'la_probe_unexpected'),
                            (Reply(404, b'{"reason": "route_not_found"}'), 'la_probe_unexpected'),
                            (Reply(404, b'not json'), 'la_probe_unexpected'),
                            (Reply(404, b'{"reason": "request_not_found"}' + b' ' * 1024), 'la_probe_unexpected'),
                            (Reply(error=TimeoutError()), 'la_unreachable'),
                            (Reply(error=ConnectionRefusedError()), 'la_unreachable'),
                            (Reply(error=ssl.SSLCertVerificationError('bad chain')), 'la_tls_failed')):
            with self.subTest(code=code, status=reply.status):
                self.assertEqual(la_client(reply).probe(), [{'code': code}])
                self.assertTrue(reply.closed)

    def test_unusable_credential_is_reported_before_any_connection(self):
        def unreadable():
            raise PermissionError('private')
        for credential in (unreadable, lambda: 'short', lambda: 'x' * 40 + '\n'):
            reply = Reply()
            self.assertEqual(la_client(reply, credential).probe(), [{'code': 'la_credential_unavailable'}])
            self.assertEqual(reply.requests, [])

    def test_the_real_supervisor_answers_the_probe_without_touching_a_lease(self):
        from lifecycle import Lifecycle
        from lifecycle_http import LifecycleHTTP
        launched = []
        launcher = SimpleNamespace(start=lambda *a, **k: launched.append(a), inspect=lambda *a: launched.append(a),
                                   stop=lambda *a: launched.append(a))
        with tempfile.TemporaryDirectory() as directory:
            lifecycle = Lifecycle(Path(directory) / 'lifecycle.sqlite', launcher, site_id='echo',
                                  epoch_prefix='la-live-', lease_seconds=115)
            try:
                app = LifecycleHTTP(lifecycle, controller_token='c' * 40, execution_token='e' * 40)
                app.accepting = True
                self.assertEqual(la_client(Supervisor(app), lambda: 'c' * 40).probe(), [])
                self.assertEqual(la_client(Supervisor(app), lambda: 'e' * 40).probe(), [{'code': 'la_credential_rejected'}])
                app.accepting = False
                self.assertEqual(la_client(Supervisor(app), lambda: 'c' * 40).probe(), [{'code': 'la_supervisor_unavailable'}])
                self.assertEqual(lifecycle.db.execute('SELECT count(*) FROM leases').fetchone()[0], 0)
                self.assertEqual(launched, [])
            finally:
                self.assertTrue(lifecycle.close())


def measured(**extra):
    return {'storage_free_bytes': 40 * GIB, 'memory_available_bytes': 4 * GIB, 'max_temperature_c': 55.0,
            'temperatures_readable': True, **extra}


IDLE_UNIT = {'MainPID': '0', 'ActiveState': 'inactive', 'ControlGroup': ''}


class JetsonProbeTests(unittest.TestCase):
    def launcher(self):
        return JetsonLauncher('0123456789abcdef')

    def test_thresholds_are_unchanged_and_boundaries_exact(self):
        self.assertEqual((JetsonLauncher.MIN_STORAGE_BYTES, JetsonLauncher.MIN_MEMORY_BYTES, JetsonLauncher.MAX_CELSIUS),
                         (20 * GIB, 3 * GIB, 80))
        gates = JetsonLauncher.headroom_gates
        self.assertEqual(gates(measured(memory_available_bytes=3 * GIB, storage_free_bytes=20 * GIB, max_temperature_c=79.9)), [])
        self.assertEqual(gates(measured(memory_available_bytes=3 * GIB - 1)), ['Memory headroom unavailable'])
        self.assertEqual(gates(measured(storage_free_bytes=20 * GIB - 1)), ['Storage headroom unavailable'])
        self.assertEqual(gates(measured(max_temperature_c=80)), ['Thermal headroom unavailable'])
        self.assertEqual(gates(measured(max_temperature_c=None)), ['Thermal headroom unavailable'])
        self.assertEqual(gates(measured(temperatures_readable=False, max_temperature_c=90)), ['Required temperature unavailable'])
        self.assertEqual(gates(measured(storage_free_bytes=0, memory_available_bytes=0, max_temperature_c=85)),
                         ['Storage headroom unavailable', 'Memory headroom unavailable', 'Thermal headroom unavailable'])

    def test_health_keeps_its_first_failure_order(self):
        launcher = self.launcher()
        def run(args, **kwargs):
            return SimpleNamespace(returncode=1, stdout='')
        def command(args, timeout=15):
            return 'active' if args[:2] == ['systemctl', 'is-active'] else ''
        for values, message in ((measured(), None),
                                (measured(storage_free_bytes=0, memory_available_bytes=0), 'Storage headroom unavailable'),
                                (measured(memory_available_bytes=2 * GIB, temperatures_readable=False), 'Memory headroom unavailable'),
                                (measured(temperatures_readable=False), 'Required temperature unavailable'),
                                (measured(max_temperature_c=81), 'Thermal headroom unavailable')):
            with self.subTest(message=message), patch.object(jetson_lifecycle.socket, 'gethostname', return_value='victoria-den-jetson'), \
                    patch.object(jetson_lifecycle, 'command', side_effect=command), \
                    patch.object(jetson_lifecycle.subprocess, 'run', side_effect=run), \
                    patch.object(JetsonLauncher, 'unit', return_value=IDLE_UNIT), \
                    patch.object(JetsonLauncher, 'headroom', return_value=values):
                if message is None:
                    self.assertIsNone(launcher.health())
                else:
                    with self.assertRaises(ValueError) as raised:
                        launcher.health()
                    self.assertEqual(str(raised.exception), message)

    def test_probe_reads_headroom_and_unit_only(self):
        launcher = self.launcher()
        busy = {'MainPID': '42', 'ActiveState': 'active', 'ControlGroup': ''}
        with patch.object(jetson_lifecycle.socket, 'gethostname', return_value='victoria-den-jetson'), \
                patch.object(jetson_lifecycle, 'command', side_effect=AssertionError('no systemctl or launch')), \
                patch.object(jetson_lifecycle.subprocess, 'run', side_effect=AssertionError('no journal or launch')), \
                patch.object(JetsonLauncher, 'headroom', return_value=measured(memory_available_bytes=2 * GIB)), \
                patch.object(JetsonLauncher, 'unit', side_effect=[IDLE_UNIT, busy]):
            first, second = launcher.probe(), launcher.probe()
        self.assertEqual(first['gates'], ['preflight_memory'])
        self.assertEqual(second['gates'], ['preflight_memory', 'preflight_worker_active'])
        self.assertEqual(first['measured']['memory_available_bytes'], 2 * GIB)
        self.assertEqual(first['thresholds'], {'storage_free_bytes': 20 * GIB, 'memory_available_bytes': 3 * GIB, 'max_temperature_c': 80})
        self.assertEqual(launcher.gates, {}, 'a probe never records a launch gate')
        with patch.object(jetson_lifecycle.socket, 'gethostname', return_value='workstation'):
            with self.assertRaisesRegex(ValueError, 'Jetson host required'):
                launcher.probe()

    def test_client_reasons_carry_measured_values(self):
        client = object.__new__(JetsonLifecycleClient)
        limits = {'storage_free_bytes': 20 * GIB, 'memory_available_bytes': 3 * GIB, 'max_temperature_c': 80}
        def reasons(gates, **values):
            client.lifecycle = SimpleNamespace(launcher=SimpleNamespace(
                probe=lambda: {'gates': gates, 'measured': measured(**values), 'thresholds': limits}))
            return client.probe()
        self.assertEqual(reasons([]), [])
        self.assertEqual(reasons(['preflight_memory', 'preflight_worker_active'], memory_available_bytes=2 * GIB),
                         [{'code': 'jetson_memory_below_gate', 'measured': 2 * GIB, 'threshold': 3 * GIB},
                          {'code': 'jetson_worker_active'}])
        self.assertEqual(reasons(['preflight_storage', 'preflight_thermal'], storage_free_bytes=GIB, max_temperature_c=83.5),
                         [{'code': 'jetson_storage_below_gate', 'measured': GIB, 'threshold': 20 * GIB},
                          {'code': 'jetson_thermal_above_gate', 'measured': 83.5, 'threshold': 80}])
        self.assertEqual(reasons(['preflight_thermal'], max_temperature_c=None), [{'code': 'jetson_thermal_unreadable'}])
        self.assertEqual(reasons(['preflight_thermal_read']), [{'code': 'jetson_thermal_unreadable'}])


class Host:
    """Lifecycle client double: probe results only; any launch path fails the test."""
    def __init__(self, reasons=None):
        self.reasons, self.probes = reasons or [], 0
    def probe(self):
        self.probes += 1
        if isinstance(self.reasons, Exception):
            raise self.reasons
        return deepcopy(self.reasons)
    def start(self, *args, **kwargs):
        raise AssertionError('readiness must not start a worker')
    status = stop = start


class Clock:
    def __init__(self):
        self.now = 100.0
    def __call__(self):
        return self.now


def rows(readiness, **kwargs):
    return {r['id']: r for r in readiness.annotate(deepcopy(RECIPES), **kwargs)}


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hosts = {'a': Host(), 'b': Host()}
        self.readiness = ManagedReadiness(self.hosts, BOUND, clock=self.clock, wall=lambda: 1_750_000_000.0)

    def test_only_bound_recipes_with_passing_probes_are_ready(self):
        catalog = rows(self.readiness)
        for rid in BOUND:
            self.assertEqual(catalog[rid]['status'], 'ready')
            self.assertEqual(catalog[rid]['readiness_reason'], 'prepares_on_start')
            self.assertEqual(catalog[rid]['readiness'], {'state': 'ready', 'reasons': [], 'prerequisites': [], 'prepares_on_start': True,
                                                         'checked_at': 1_750_000_000.0, 'max_age_seconds': 5})
        later = [r for rid, r in catalog.items() if rid not in BOUND]
        self.assertEqual(len(later), 18)
        for recipe in later:
            self.assertEqual((recipe['status'], recipe['readiness_reason'], recipe['readiness']['state']),
                             ('blocked', 'experiment_pair_not_provisioned', 'coming_later'))
            self.assertEqual(recipe['readiness']['prerequisites'][0], 'worker_binding')
            self.assertNotIn('source', recipe['readiness']['prerequisites'])
        self.assertEqual([h.probes for h in self.hosts.values()], [1, 1], 'one probe per host for both bound setups')

    def test_host_reasons_block_with_the_arm_and_measurement(self):
        self.hosts['a'].reasons = [{'code': 'jetson_memory_below_gate', 'measured': 2 * GIB, 'threshold': 3 * GIB}]
        self.hosts['b'].reasons = [{'code': 'la_unreachable'}]
        recipe = rows(self.readiness)['placement-same']
        self.assertEqual((recipe['status'], recipe['readiness_reason'], recipe['readiness']['state']),
                         ('blocked', 'jetson_memory_below_gate', 'blocked'))
        self.assertEqual(recipe['readiness']['reasons'],
                         [{'code': 'jetson_memory_below_gate', 'measured': 2 * GIB, 'threshold': 3 * GIB, 'arm': 'a'},
                          {'code': 'la_unreachable', 'arm': 'b'}])

    def test_probe_results_are_reused_only_within_the_bounded_age(self):
        rows(self.readiness); self.clock.now += 4.9; rows(self.readiness)
        self.assertEqual(self.hosts['a'].probes, 1)
        self.hosts['a'].reasons = [{'code': 'jetson_worker_active'}]
        self.clock.now += .1
        self.assertEqual(rows(self.readiness)['placement-same']['readiness_reason'], 'jetson_worker_active')
        self.assertEqual(self.hosts['a'].probes, 2)
        self.clock.now -= 50  # a clock step backwards never extends reuse
        rows(self.readiness)
        self.assertEqual(self.hosts['a'].probes, 3)

    def test_failed_invalid_or_missing_probes_are_never_ready(self):
        for reasons, code in ((RuntimeError('host read failed'), 'readiness_probe_failed'), ({'code': 'x'}, 'readiness_probe_failed'),
                              ([{'code': 'Not A Code'}], 'readiness_probe_failed'), ([{'reason': 'x'}], 'readiness_probe_failed')):
            with self.subTest(reasons=reasons):
                self.hosts['b'].reasons = reasons
                self.clock.now += 10
                recipe = rows(self.readiness)['placement-best']
                self.assertEqual(recipe['status'], 'blocked')
                self.assertEqual(recipe['readiness']['reasons'], [{'code': code, 'arm': 'b'}])
        readiness = ManagedReadiness({'a': Host(), 'b': object()}, BOUND, clock=self.clock)
        self.assertEqual(rows(readiness)['placement-same']['readiness']['reasons'],
                         [{'code': 'readiness_probe_unavailable', 'arm': 'b'}])

    def test_an_active_pair_is_reported_without_probing(self):
        recipe = rows(self.readiness, busy='comparison_active')['placement-same']
        self.assertEqual((recipe['status'], recipe['readiness']['reasons'], recipe['readiness']['checked_at']),
                         ('blocked', [{'code': 'comparison_active'}], None))
        self.assertEqual(self.hosts['a'].probes + self.hosts['b'].probes, 0)
        unbound = ManagedReadiness(self.hosts, set(), clock=self.clock)
        self.assertTrue(all(r['readiness']['state'] == 'coming_later' for r in rows(unbound).values()))
        self.assertEqual(self.hosts['a'].probes, 0, 'no bound setup, no probe')

    def test_requirements_map_to_explicit_prerequisites(self):
        by_id = {r['id']: r for r in RECIPES}
        self.assertEqual(prerequisites(by_id['depth']),
                         ['worker_binding', 'model_artifacts', 'resource_admission', 'depth', 'calibration', 'room_registration'])
        self.assertEqual(prerequisites(by_id['qwen-jev']),
                         ['worker_binding', 'model_artifacts', 'resource_admission', 'saved_session', 'qwen_admission', 'jev_household_egress'])
        self.assertIn('source_rate_4hz', prerequisites(by_id['sampling']))
        self.assertIn('high_resolution_source', prerequisites(by_id['input-detail']))
        self.assertIn('reviewed_preferences', prerequisites(by_id['preference']))
        self.assertIn('furniture_map', prerequisites(by_id['spatial-combined']))
        self.assertEqual(prerequisites({'requirements': ['source', 'new_sensor', 'Bad Value!', 'depth', 'depth']}),
                         ['worker_binding', 'new_sensor', 'unrecognized_requirement', 'depth'])


class Inner:
    def start_session(self, session):
        return {'state': 'recording', 'inference': 'ready'}
    def stop_session(self, session):
        return {'state': 'stopped'}
    def close(self, timeout=5):
        return {'drained': True}


class Launching(Host):
    def __init__(self, reasons=None):
        super().__init__(reasons)
        self.state, self.starts = 'ready', 0
    def start(self, request_id, session_id, variant='detector'):
        self.starts += 1
        return self.status(request_id)
    def status(self, request_id):
        import time
        return {'state': self.state, 'expires_at': time.time() + 100, 'engine_epoch': 'test-epoch', 'profile': {}, 'reason': None}
    def stop(self, request_id, epoch):
        self.state = 'stopped'
        return self.status(request_id)


class ReadinessPairTests(unittest.TestCase):
    def setUp(self):
        from config import Settings
        from controller import Controller
        from store import Store
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Settings(Path(self.temp.name), max_session_seconds=60, cameras=({'id': 'den'},)))
        self.hosts = {'a': Launching([{'code': 'jetson_memory_below_gate', 'measured': GIB, 'threshold': 3 * GIB}]), 'b': Launching()}
        catalog = [{'id': 'placement-same', 'status': 'unverified', 'readiness_reason': 'prepares_on_start'}]
        self.runtime = ReadinessPair(self.store, self.hosts, lambda _: Inner(), catalog, prepare_seconds=.5,
                                     setup_readiness=ManagedReadiness(self.hosts, {'placement-same'}))
        self.controller = Controller(self.store, self.runtime)

    def tearDown(self):
        self.runtime.close()
        self.store.close()
        self.temp.cleanup()

    def settle(self, state, sid):
        import time
        until = time.monotonic() + 5
        while time.monotonic() < until and self.store.get_session(sid)['state'] != state:
            time.sleep(.01)
        self.assertEqual(self.store.get_session(sid)['state'], state)

    def test_browsing_probes_but_never_launches_and_admission_is_unchanged(self):
        caps = self.runtime.capabilities()
        self.assertEqual(caps['experiments'][0]['readiness']['reasons'][0]['code'], 'jetson_memory_below_gate')
        self.assertEqual(caps['experiments'][0]['status'], 'blocked')
        self.assertTrue(caps['recording_allowed'])
        self.assertEqual([h.starts for h in self.hosts.values()], [0, 0])
        # The advisory probe never replaces the lifecycle preflight at Start.
        session = self.controller.start(dict(camera_id='den', experiment_id='placement-same', name='t', request_id='r1'))
        self.settle('recording', session['id'])
        self.assertEqual([h.starts for h in self.hosts.values()], [1, 1])
        probes = self.hosts['a'].probes
        busy = self.runtime.capabilities()['experiments'][0]['readiness']
        self.assertEqual(busy['reasons'], [{'code': 'comparison_active'}])
        self.assertEqual(self.hosts['a'].probes, probes, 'no host probe while a comparison owns the workers')
        self.controller.stop(session['id'])
        self.settle('stopped', session['id'])

    def test_managed_runtime_advertises_bound_setups_and_coming_later_ones(self):
        from managed_detector_app import create_managed_runtime
        vjepa = {'pose_profiles': {}, 'vjepa_live': {'enabled': True}}
        for config, ready in (({}, {'placement-same'}), ({'pose_profiles': {}}, BOUND),
                              ({'vjepa_live': {'enabled': True}}, {'placement-same'}),
                              ({'pose_profiles': {}, 'vjepa_live': {'enabled': False}}, BOUND),
                              (vjepa, BOUND | {'vjepa-onoff'})):
            with self.subTest(ready=ready):
                hosts = {'a': Host(), 'b': Host()}
                runtime = create_managed_runtime(None, None, config, hosts)
                self.assertIsInstance(runtime, ReadinessPair)
                catalog = {r['id']: r['readiness']['state'] for r in runtime.capabilities()['experiments']}
                self.assertEqual({k for k, v in catalog.items() if v == 'ready'}, ready)
                self.assertEqual(len(catalog), 20)
                self.assertTrue(all(v == 'coming_later' for k, v in catalog.items() if k not in ready))
                # Admission still uses the static catalog, not the advertised status.
                self.assertEqual({r['id'] for r in runtime.catalog if r['status'] != 'blocked'}, ready)

    def test_saved_analysis_stays_a_saved_session_setup_and_never_starts_live(self):
        from managed_detector_app import create_managed_runtime
        from managed_pair import ManagedPair
        class Jobs:
            class analysis:
                uncertain_steps = staticmethod(lambda: 0)
            def start_reconcile(self): pass
            def active(self, *_): return False
            def close(self, timeout=0): return True
        hosts = {'a': Host(), 'b': Host()}
        runtime = create_managed_runtime(None, None, {}, hosts)
        base = {r['id']: r for r in ManagedPair.capabilities(runtime)['experiments']}
        self.assertEqual(base['qwen-evidence']['readiness_reason'], 'qwen_admission_not_provisioned')
        rows = {r['id']: r for r in runtime.capabilities()['experiments']}
        self.assertEqual(rows['qwen-evidence']['readiness']['state'], 'coming_later', 'no Qwen admission: still coming later')
        runtime.analysis = Jobs()
        caps = runtime.capabilities()
        self.assertTrue(caps['analysis']['available'])
        rows = {r['id']: r for r in caps['experiments']}
        self.assertEqual({k: rows['qwen-evidence'][k] for k in ('status', 'readiness_reason')},
                         {'status': 'unverified', 'readiness_reason': 'saved_analysis_on_request'})
        self.assertEqual(rows['qwen-evidence']['readiness'], {'state': 'saved_session', 'reasons': [], 'prerequisites': [],
                                                              'prepares_on_start': False, 'checked_at': None, 'max_age_seconds': None})
        self.assertEqual(rows['qwen-jev']['readiness']['state'], 'coming_later')
        self.assertEqual(sum(r['readiness']['state'] == 'ready' for r in rows.values()), 1)
        # Advertising it never makes it live-admissible (Host fails on any launch).
        self.assertEqual(runtime.start_session({'id': 's', 'experiment_id': 'qwen-evidence', 'site_id': 'victoria', 'camera_id': 'den'}),
                         {'state': 'blocked', 'reason': 'experiment_pair_not_provisioned'})


if __name__ == '__main__':
    unittest.main()
