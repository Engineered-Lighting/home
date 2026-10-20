"""Protected mount3/bootstrap2 source wiring; no source/worker/device starts.

External lifecycle receipts, qualification authority and private-file/Windows
ports are explicit authored doubles. The actual source4 worker/positive replay
gate lives in test_lab_stereo_worker_runtime.py and is not duplicated here.
"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for directory in (HERE.parent / 'home-context', ROOT / 'tools/den-live-runtime'):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

import live_den_mount as mount
import lab_stereo_mount_source as stereo
import lab_live_runtime as runtime
from lab_live_desk_anchor import LabLiveDeskAnchor
from lab_stereo_desk_capability import LabStereoDeskCapability
from paired_preview_client import encoded
from live_test_helpers import TOKEN, NOW
import test_lab_stereo_desk_runtime as source_fixture
import test_lab_stereo_session_package as package_fixture

mount._runtime_module()
from home_den_runtime.supervisor import SessionSupervisor, SupervisorRefusal
from home_den_runtime.lab_stereo_media import LabStereoMedia


class AuthoredQualificationAuthority:
    """Only a fixed provider port double, never commissioned household truth."""
    def __init__(self, source, binding):
        self.source, self.fixed_binding = source, deepcopy(binding)
        self.binding_calls, self.resolve_calls = 0, []

    def binding(self):
        self.binding_calls += 1
        return deepcopy(self.fixed_binding)

    def resolve(self, descriptor):
        self.resolve_calls.append(deepcopy(descriptor))
        return self.source.f.qualification(descriptor)


class LabStereoMountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = source_fixture.LabStereoDeskRuntimeTests()
        cls.addClassCleanup(cls.source.doCleanups)
        cls.source.setUp()
        # Reuse exact existing source4/package commissioning; no new geometry,
        # model output, activation, journal publication or checkpoint is seeded.
        cls.inputs = package_fixture.LabStereoSessionPackageTests.inputs.__func__(
            SimpleNamespace(source=cls.source))
        prepared = package_fixture.package.prepare_stereo_session_package(cls.inputs, now=NOW + .2)
        if prepared['status'] != 'prepared':
            raise AssertionError(prepared)
        cls.prepared = prepared

    def setUp(self):
        self.now = NOW + .2
        self.files = deepcopy(self.prepared['files'])
        self.provision = self.files['mount.json']
        self.bootstrap = self.files['bootstrap.json']
        self.paths = deepcopy(self.inputs['provisioning'])
        self.records = {self.paths['mount_file']: self.provision,
            self.paths['commissioning_file']: self.files['stereo-commissioning.json'],
            self.paths['qualification_file']: self.files['stereo-qualification.json']}
        self.reads, self.lab_requests, self.mounts = [], [], []
        self.authority = AuthoredQualificationAuthority(self.source, self.inputs['stereo']['authority_binding'])
        self.operator_token, self.context_token = 'authored-stereo-operator', 'authored-stereo-owner'
        self.tokens = {self.provision['producer']['publication_token_file']: TOKEN,
                       self.provision['runtime']['worker_config']['token_file']: 'authored-no-worker-start'}
        self.lab_grants = [{'token_sha256': 'e' * 64}]
        self.incumbent, self.occupancy = SimpleNamespace(capture_scope=object()), object()
        async def app(scope, receive, send):
            self.lab_requests.append(scope['path'])
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', b'application/json')]})
            await send({'type': 'http.response.body', 'body': b'{"route":"ordinary-lab"}'})
        app.state = SimpleNamespace(store=self.source.f.store, marker=object())
        self.app = app
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(stereo, 'utc_now', side_effect=lambda: self.now))
        self.reader = self.stack.enter_context(patch.object(stereo, 'read_private_record', side_effect=self.read_record))
        self.bootstrap_reader = self.stack.enter_context(patch.object(mount, 'private_json', return_value=self.bootstrap))
        self.stack.enter_context(patch.object(mount, 'private_token', side_effect=self.read_token))
        self.addCleanup(self.cleanup_mounts)

    def read_record(self, filename, *, maximum):
        self.reads.append(str(filename))
        value = self.records[str(filename)]
        self.assertLessEqual(len(encoded(value)), maximum)
        return deepcopy(value)

    def read_token(self, filename):
        self.assertIn(str(filename), self.tokens)
        return self.tokens[str(filename)]

    def cleanup_mounts(self):
        for mounted in reversed(self.mounts):
            guard = getattr(mounted.state, 'den_shutdown_guard', None)
            if callable(guard):
                guard()

    def assert_no_source_files(self):
        producer = self.provision['producer']
        self.assertFalse(Path(producer['journal_path']).exists())
        self.assertFalse(Path(producer['original_archive']['filename']).exists())
        self.assertFalse(Path(self.provision['stereo_source']['media']['directory']).exists())
        self.assertEqual(self.authority.resolve_calls, [])

    def direct(self, *, authority='authored', provision=None):
        selected = self.authority if authority == 'authored' else authority
        mounted = mount._mount_provision(self.app, deepcopy(provision or self.provision),
            incumbent=self.incumbent, occupancy=self.occupancy, lab_grants=self.lab_grants,
            lab_store=self.source.f.store, stereo_qualification_authority=selected)
        self.mounts.append(mounted)
        return mounted

    def deferred(self):
        mounted = mount.mount_den_session(self.app, self.paths['bootstrap_file'],
            incumbent=self.incumbent, occupancy=self.occupancy, lab_grants=self.lab_grants,
            lab_store=self.source.f.store, stereo_qualification_authority=self.authority)
        self.mounts.append(mounted)
        return mounted

    def command(self, operation='start', **changes):
        value = {'schema': 'home-live-session-command-1', 'version': 1,
                 'id': 'authored-stereo-' + operation, 'operation': operation,
                 **deepcopy(self.provision['runtime']['session'])}
        for key in tuple(value):
            if key not in {'schema', 'version', 'id', 'operation', 'session_ref', 'configuration_digest'}:
                del value[key]
        # The runtime session's schema/version identify a different contract.
        value.update(schema='home-live-session-command-1', version=1)
        value.update(changes)
        return value

    @staticmethod
    def request(app, method, path, *, token=None, command=None):
        async def run():
            headers = {'Authorization': 'Bearer ' + token} if token else {}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='https://authored-controller.invalid') as client:
                return await client.request(method, path, headers=headers, json=command)
        return asyncio.run(run())

    def test_direct_mount_is_inert_real_source4_composition_and_preserves_shared_state(self):
        state = self.app.state
        with patch('subprocess.Popen', side_effect=AssertionError('process_at_install')), \
                patch('threading.Thread.start', side_effect=AssertionError('thread_at_install')), \
                patch('http.client.HTTPConnection.connect', side_effect=AssertionError('network_at_install')):
            mounted = self.direct()
        supervisor = mounted.supervisor
        self.assertIsInstance(supervisor, SessionSupervisor)
        composition = supervisor.graph_factory.__self__
        self.assertIsInstance(composition, runtime.LabRuntimeComposition)
        self.assertIsInstance(composition.desk_capability, LabStereoDeskCapability)
        self.assertIsInstance(composition.desk_capability.anchor_source, LabLiveDeskAnchor)
        self.assertIsInstance(composition.desk_capability.media_archive, LabStereoMedia)
        self.assertIs(composition.store, self.source.f.store)
        self.assertEqual(composition.producer['version'], 4)
        self.assertIsNone(supervisor.graph)
        self.assertIs(mounted.state, state)
        self.assertIs(self.app.state, state)
        self.assertFalse(supervisor.status()['physical_authority'])
        self.assert_no_source_files()

    def test_missing_dict_and_foreign_fixed_authority_refuse_before_journal_or_spool(self):
        foreign = AuthoredQualificationAuthority(self.source, self.inputs['stereo']['authority_binding'])
        foreign.fixed_binding['lab_binding_digest'] = 'f' * 64
        for authority in (None, {'status': 'qualified'}, foreign):
            with self.subTest(authority=type(authority).__name__), self.assertRaises(ValueError):
                self.direct(authority=authority)
            self.assert_no_source_files()
        self.assertEqual(foreign.resolve_calls, [])

    def test_scope_context_operator_and_lab_credentials_cannot_cross(self):
        for mutate in (
            lambda p: p['runtime']['session']['session_ref'].update(session_id='foreign-session'),
            lambda p: p.update(operator_token_sha256=p['context_token_sha256']),
            lambda p: p.update(context_token_sha256='e' * 64),
        ):
            value = deepcopy(self.provision)
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.direct(provision=value)
            self.assert_no_source_files()

    def test_bootstrap_install_is_lazy_and_ordinary_lab_remains_available(self):
        mounted = self.deferred()
        self.bootstrap_reader.assert_called_once_with(self.paths['bootstrap_file'])
        self.assertEqual(self.reads, [])
        self.assertEqual(self.authority.binding_calls, 0)
        self.assertIsNone(mounted.supervisor.supervisor)
        response = self.request(mounted, 'GET', '/api/perception-lab/v1/capabilities')
        self.assertEqual(response.json(), {'route': 'ordinary-lab'})
        response = self.request(mounted, 'GET', '/api/home-context/v1/snapshot')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.reads, [])
        self.assert_no_source_files()

    def test_authenticated_first_start_loads_once_and_rejects_scope_and_other_credentials(self):
        mounted = self.deferred()
        for token in (None, self.context_token, TOKEN, 'authored-wrong-token'):
            response = self.request(mounted, 'POST', '/api/home-live-session/v1/start',
                                    token=token, command=self.command())
            self.assertEqual(response.status_code, 403)
        wrong = self.command()
        wrong['session_ref']['site_id'] = 'foreign-site'
        response = self.request(mounted, 'POST', '/api/home-live-session/v1/start',
                                token=self.operator_token, command=wrong)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.reads, [])
        starts = []
        def start(supervisor):
            starts.append(supervisor)
            return {'status': 'started', 'physical_authority': False, 'authored_start_boundary_double': True}
        with patch.object(SessionSupervisor, 'start', autospec=True, side_effect=start):
            first = self.request(mounted, 'POST', '/api/home-live-session/v1/start',
                                 token=self.operator_token, command=self.command())
            second = self.request(mounted, 'POST', '/api/home-live-session/v1/start',
                                  token=self.operator_token, command=self.command())
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertTrue(first.json()['authored_start_boundary_double'])
        self.assertEqual(len(starts), 1)
        self.assertEqual(self.reads.count(self.paths['mount_file']), 1)
        self.assertIs(starts[0], mounted.supervisor.supervisor)
        self.assertIs(self.app.state.den_session_supervisor, mounted.supervisor)
        wrong_session = self.command()
        wrong_session['session_ref']['session_id'] = 'foreign-session'
        response = self.request(mounted, 'POST', '/api/home-live-session/v1/start',
                                token=self.operator_token, command=wrong_session)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.reads.count(self.paths['mount_file']), 1)
        self.assert_no_source_files()

    def test_stop_during_late_binding_preserves_bootstrap_guard_and_never_starts(self):
        mounted = self.deferred()
        deferred, original_guard = mounted.supervisor, self.app.state.den_shutdown_guard
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        # Construct the real borrowed-Store composition before the short race
        # barrier. Validation cost is not the cancellation behavior under test.
        late_mount = mount._mount_provision(self.app, deepcopy(self.provision),
            incumbent=self.incumbent, occupancy=self.occupancy, lab_grants=self.lab_grants,
            lab_store=self.source.f.store, stereo_qualification_authority=self.authority,
            publish_state=False)
        self.mounts.append(late_mount)
        self.assertIs(self.app.state.den_shutdown_guard, original_guard)
        late = []
        def delayed(*args, **kwargs):
            self.assertEqual(args[1], self.provision)
            self.assertIs(kwargs['lab_store'], self.source.f.store)
            self.assertIs(kwargs['stereo_qualification_authority'], self.authority)
            self.assertIs(kwargs['publish_state'], False)
            late.append(late_mount)
            entered.set()
            if not release.wait(4):
                raise AssertionError('authored mount delay not released')
            return late_mount
        errors = []
        def run():
            try:
                deferred.control(self.command())
            except BaseException as error:
                errors.append(error)
        with patch.object(mount, '_mount_provision', side_effect=delayed), \
                patch.object(SessionSupervisor, 'start', side_effect=AssertionError('start_after_cancellation')):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(entered.wait(3), errors)
                self.assertIs(self.app.state.den_shutdown_guard, original_guard)
                self.assertEqual(deferred.control(self.command('stop'))['status'], 'cancelled')
            finally:
                release.set(); thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), 'den_binding_cancelled')
        self.assertIsNone(deferred.supervisor)
        self.assertIs(self.app.state.den_shutdown_guard, original_guard)
        self.assertEqual(len(late), 1)
        composition = late[0].state.den_session_supervisor.graph_factory.__self__
        self.assertTrue(composition.cancel.is_set())
        self.assert_no_source_files()

    def test_expired_qualification_refuses_before_journal_media_or_any_resolution(self):
        self.now = NOW + 31
        with self.assertRaises(ValueError):
            self.direct()
        self.assert_no_source_files()


class PollBudgetStereoMountTests(unittest.TestCase):
    """Source7 reuses actual bundled originals; clock/model I/O is authored."""
    def setUp(self):
        from test_lab_bundled_worker_runtime import _BundledDeskFixture
        from paired_preview_client import sha
        self.source = _BundledDeskFixture(self)
        producer = self.source.producer
        producer.update(schema='home-incumbent-lab-producer-7', version=7)
        producer['limits'] = dict(max_poll_attempts=40, max_capture_originals=4,
                                  max_runtime_s=10, page_limit=128)
        join, _ = runtime._join_and_configuration(producer, self.source.f.session)
        self.source.f.lab_binding_digest = join.binding_digest
        self.inputs = package_fixture.LabStereoSessionPackageTests.inputs.__func__(
            SimpleNamespace(source=self.source))
        original = self.inputs['stereo']
        self.qualified = {key: deepcopy(original[key]) for key in
            ('stereo_policy', 'depth_clock_policy', 'authority_binding', 'association_originals')}
        self.authority = AuthoredQualificationAuthority(self.source, self.qualified['authority_binding'])
        paths = self.inputs['provisioning']
        self.config = dict(schema='home-lab-stereo-source-config-4', version=4,
            commissioning_file=paths['commissioning_file'],
            commissioning_digest=sha(encoded(self.source.commissioning)),
            qualification_file=paths['qualification_file'], qualification_digest=sha(encoded(self.qualified)),
            qualification_authority_ref={'id': self.qualified['authority_binding']['association_method_ref']['id'],
                                         'digest': sha(encoded(self.qualified['authority_binding']))},
            media=deepcopy(original['media']), original_family='existing-api-bundled-rgb-1',
            bundle_input_policy_sha256=producer['desk_policy']['bundle_input_policy_sha256'])
        self.records = {paths['commissioning_file']: self.source.commissioning,
                        paths['qualification_file']: self.qualified}
        self.reads = []

    def read_record(self, filename, *, maximum):
        self.reads.append(str(filename)); value = self.records[str(filename)]
        self.assertLessEqual(len(encoded(value)), maximum)
        return deepcopy(value)

    def compose(self, producer=None, config=None):
        with patch.object(stereo, 'read_private_record', side_effect=self.read_record):
            return stereo.stereo_runtime_composition(producer or self.source.producer, config or self.config,
                store=self.source.f.store, preflight=lambda: True, control_clock=self.source.control_clock,
                qualification_authority=self.authority, incumbent_runtime=self.source.f.runtime,
                clock=lambda: self.source.f.now)

    def test_source7_protected_composition_admits_same_full_cut_then_withdraws_and_retires(self):
        from lab_bundled_rgb_stereo import BundledRgbStereoSource
        from journal import digest
        source = self.source
        composition = self.compose()
        self.assertIsNone(composition.graph)
        self.assertIs(composition.store, source.f.store)
        provider = composition.bundled_source
        self.assertIsInstance(provider, BundledRgbStereoSource)
        self.assertIs(provider, composition.desk_capability.anchor_source.bundled_rgb_source)
        self.assertIs(provider.runtime, source.f.runtime)
        self.assertIs(provider.depth_clock.store, source.f.store)
        self.assertEqual(provider.depth_clock.clock, self.qualified['depth_clock_policy'])
        self.assertFalse(Path(source.producer['original_archive']['filename']).exists())
        composition.monotonic = lambda: source.f.now - NOW
        source.composition = composition
        source.graph = graph = composition.create()
        # Real Journal recording port; no worker/drain claim is authored here.
        recorded = []
        graph.journal.attach_recorder(lambda record: recorded.append(deepcopy(record)))
        result = graph.step()
        self.assertEqual(result['status'], 'admitted_desk', result)
        self.assertEqual(graph.config['version'], 7)
        self.assertEqual(graph.configuration_digest, digest(graph.config))
        self.assertTrue(result['operational_geometry'])
        self.assertEqual(result['desk']['event']['payload']['status'], 'supplied')
        self.assertEqual(len(composition.desk_capability.media_archive.media_refs()), 8)
        self.assertTrue(recorded)
        withdrawal = graph.withdraw()
        self.assertIn('barrier_receipt', withdrawal)
        stopped = graph.step()
        self.assertEqual(stopped['status'], 'stopped')
        final = graph.finalize(drain_receipt=source.drain(withdrawal, recorder='incomplete', worker='unconfirmed'))
        self.assertEqual(final['status'], 'unconfirmed', 'no real worker/recorder drain was run')
        self.assertTrue(graph.finished)
        self.assertEqual(composition.release()['status'], 'released')
        self.assertTrue(composition.cancel.is_set())

    def test_crossed_source_versions_and_policy_refuse_before_files_or_authority_resolution(self):
        for kind in ('old_source', 'old_producer', 'wrong_policy', 'wrong_family'):
            producer, config = deepcopy(self.source.producer), deepcopy(self.config)
            if kind == 'old_source': config.update(schema='home-lab-stereo-source-config-3', version=3)
            elif kind == 'old_producer': producer.update(schema='home-incumbent-lab-producer-6', version=6)
            elif kind == 'wrong_policy': config['bundle_input_policy_sha256'] = 'f' * 64
            else: config['original_family'] = 'existing-api-assessment-1'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.compose(producer, config)
            self.assertEqual(self.reads, [])
            self.assertEqual(self.authority.resolve_calls, [])
            for filename in (self.source.producer['journal_path'],
                             self.source.producer['original_archive']['filename'], self.config['media']['directory']):
                self.assertFalse(Path(filename).exists())

    def test_package6_real_mount7_is_inert_and_owner_stop_retires_same_composition(self):
        value = deepcopy(self.inputs)
        value.update(schema='home-den-lab-session-inputs-6', version=6)
        value['stereo'].update(schema='home-den-stereo-package-inputs-3', version=3)
        value['runtime'].pop('max_cycles')
        value['runtime'].update(max_poll_attempts=40, max_capture_originals=4)
        prepared = package_fixture.package.prepare_stereo_session_package(value, now=self.source.f.now)
        self.assertEqual(prepared['status'], 'prepared', prepared)
        self.assertEqual(prepared['version'], 6)
        provision = prepared['files']['mount.json']
        self.assertEqual((provision['version'], provision['runtime']['version'],
                          provision['runtime']['session']['version']), (7, 2, 2))
        self.assertIsNone(provision['command_capture'])
        tokens = {provision['producer']['publication_token_file']: TOKEN,
                  provision['runtime']['worker_config']['token_file']: 'authored-poll-no-worker-start'}
        async def app(_scope, _receive, _send):
            raise AssertionError('ordinary_asgi_not_called_during_idle_mount')
        app.state = SimpleNamespace(store=self.source.f.store)
        with patch.object(stereo, 'read_private_record', side_effect=self.read_record), \
                patch.object(stereo, 'utc_now', side_effect=lambda: self.source.f.now), \
                patch.object(mount, 'private_token', side_effect=lambda filename: tokens[filename]), \
                patch('subprocess.Popen', side_effect=AssertionError('process_at_idle_mount')), \
                patch('threading.Thread.start', side_effect=AssertionError('thread_at_idle_mount')), \
                patch('http.client.HTTPConnection.connect', side_effect=AssertionError('network_at_idle_mount')):
            mounted = mount._mount_provision(app, provision, incumbent=self.source.f.runtime,
                occupancy=object(), lab_grants=[{'token_sha256': 'e' * 64}],
                lab_store=self.source.f.store, stereo_qualification_authority=self.authority)
        supervisor = mounted.supervisor
        composition = supervisor.graph_factory.__self__
        self.assertIs(composition.store, self.source.f.store)
        self.assertIs(composition.bundled_source.runtime, self.source.f.runtime)
        self.assertIs(composition.bundled_source, composition.desk_capability.anchor_source.bundled_rgb_source)
        self.assertIsNone(supervisor.graph)
        self.assertEqual(supervisor.status()['status'], 'idle')
        self.assertEqual(supervisor.stop(reason='authored_poll_mount_stop')['status'], 'stopped')
        self.assertTrue(composition.cancel.is_set())
        self.assertIsNone(supervisor.graph)
        self.assertEqual(self.source.f.store.get_session(self.source.f.session['id'])['state'], 'recording')
        self.assertEqual(self.authority.resolve_calls, [])
        for filename in (provision['producer']['journal_path'], provision['producer']['original_archive']['filename'],
                         provision['runtime']['archive_file'], provision['stereo_source']['media']['directory']):
            self.assertFalse(Path(filename).exists(), filename)


if __name__ == '__main__':
    unittest.main()
