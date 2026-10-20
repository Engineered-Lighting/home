"""Protected assessed-command tuple tests; no native or external model I/O."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import live_den_bootstrap as bootstrap
import live_den_mount as mount
import lab_stereo_mount_source as stereo
from journal import digest


def minimal_mount():
    """Shape-only negative fixture; never accepted as an operational graph."""
    return dict(schema='home-managed-den-mount-8', version=8,
        producer=dict(schema='home-incumbent-lab-producer-8', version=8),
        runtime=dict(schema='home-live-runtime-config-3', version=3,
            session=dict(schema='home-live-session-config-3', version=3)),
        stereo_source=dict(schema='home-lab-stereo-source-config-5', version=5),
        command_capture=dict(schema='home-den-canonical-command-declaration-3', version=3),
        preflight_file=str(Path(__file__).resolve().parent / 'authored-assessed-preflight.json'),
        context_token_sha256='a' * 64, operator_token_sha256='b' * 64)


class AssessedCommandMountBoundaryTests(unittest.TestCase):
    def test_mount8_crossed_tuples_and_factory_override_refuse_before_effects(self):
        import lab_live_runtime
        mutations = (
            lambda p: p.update(command_capture=None),
            lambda p: p.pop('command_capture'),
            lambda p: p['command_capture'].update(schema='home-den-canonical-command-declaration-2', version=2),
            lambda p: p['producer'].update(schema='home-incumbent-lab-producer-7', version=7),
            lambda p: p['runtime'].update(schema='home-live-runtime-config-2', version=2),
            lambda p: p['runtime']['session'].update(schema='home-live-session-config-2', version=2),
            lambda p: p['stereo_source'].update(schema='home-lab-stereo-source-config-4', version=4))
        for index, mutate in enumerate(mutations):
            value = minimal_mount(); mutate(value)
            with self.subTest(index=index), patch.object(mount, 'LocalDenPreflight') as preflight, \
                    patch.object(mount, 'private_token') as token, \
                    patch.object(lab_live_runtime, 'lab_graph_configuration') as configuration:
                with self.assertRaises(ValueError):
                    mount._mount_provision(SimpleNamespace(state=SimpleNamespace()), value,
                        incumbent=object(), occupancy=object(), lab_grants=[])
                preflight.assert_not_called(); token.assert_not_called(); configuration.assert_not_called()
        with patch.object(lab_live_runtime, 'lab_graph_configuration') as configuration:
            with self.assertRaisesRegex(ValueError, 'fixed_mount8_factory_cannot_be_overridden'):
                mount._mount_provision(SimpleNamespace(state=SimpleNamespace()), minimal_mount(),
                    incumbent=object(), occupancy=object(), lab_grants=[], command_factory=lambda *_: None)
            configuration.assert_not_called()
        # The preceding poll-only mount keeps its deliberately strict refusal.
        predecessor = minimal_mount()
        predecessor.update(schema='home-managed-den-mount-7', version=7, command_capture=None)
        predecessor['producer'].update(schema='home-incumbent-lab-producer-7', version=7)
        predecessor['runtime'].update(schema='home-live-runtime-config-2', version=2)
        predecessor['runtime']['session'].update(schema='home-live-session-config-2', version=2)
        predecessor['stereo_source'].update(schema='home-lab-stereo-source-config-4', version=4)
        for offered in (None, lambda *_: None):
            value = deepcopy(predecessor)
            if offered is None: value['command_capture'] = minimal_mount()['command_capture']
            with self.assertRaisesRegex(ValueError, 'poll_budget_commands_unavailable'):
                mount._mount_provision(SimpleNamespace(state=SimpleNamespace()), value,
                    incumbent=object(), occupancy=object(), lab_grants=[], command_factory=offered)

    def test_source_config5_refuses_crossed_producer_or_family_before_original_read(self):
        source = {key: None for key in stereo.BUNDLED_SOURCE_KEYS}
        source.update(schema='home-lab-stereo-source-config-5', version=5,
            original_family='existing-api-bundled-rgb-1', bundle_input_policy_sha256='a' * 64)
        producer = dict(schema='home-incumbent-lab-producer-8', version=8,
            desk_policy=dict(version=4, original_family='existing-api-bundled-rgb-1', bundle_input_policy_sha256='a' * 64))
        authority = SimpleNamespace(binding=lambda: {}, resolve=lambda _: None)
        for changed in ('producer', 'source', 'family'):
            p, s = deepcopy(producer), deepcopy(source)
            if changed == 'producer': p.update(schema='home-incumbent-lab-producer-7', version=7)
            elif changed == 'source': s.update(schema='home-lab-stereo-source-config-4', version=4)
            else: s['original_family'] = 'existing-api-assessment-1'
            with self.subTest(changed=changed), patch.object(stereo, 'read_private_record') as reader:
                with self.assertRaises(ValueError):
                    stereo.stereo_runtime_composition(p, s, store=object(), preflight=lambda: True,
                        control_clock=object(), qualification_authority=authority, incumbent_runtime=object())
                reader.assert_not_called()

    def test_bootstrap7_authenticated_edge_binds_once_and_stop_keeps_same_owner(self):
        import test_live_den_bootstrap as old
        base = old.DeferredDenBootstrapTests(); base.setUp(); self.addCleanup(base.doCleanups)
        declaration = dict(schema='home-den-canonical-command-declaration-3', version=3)
        base.provision.update(schema='home-den-lab-bootstrap-7', version=7,
            command_capture_digest=digest(declaration))
        base.frozen.update(minimal_mount())
        base.frozen['operator_token_sha256'] = base.provision['operator_token_sha256']
        base.frozen['runtime']['session'].update(deepcopy(base.session))
        # Exact trusted private file boundary double; loader/supervisor remain
        # explicit doubles, so this is authentication/realization, not dispatch.
        with patch.object(stereo, 'read_private_record', side_effect=lambda *_a, **_k: deepcopy(base.frozen)) as reader:
            deferred = bootstrap.DeferredAssessedCommandLabSession(base.lab,
                provision=base.provision, loader=base.loader, supervisor_refusal=old.SupervisorRefusal)
            edge = old.SessionControlEdge(deferred.dispatch, supervisor=deferred,
                token_sha256=base.provision['operator_token_sha256'])
            self.assertEqual(base.request(edge, 'POST', '/api/home-live-session/v1/start',
                token='wrong', command=base.command()).status_code, 403)
            reader.assert_not_called(); self.assertEqual(base.loads, [])
            self.assertEqual(base.request(edge, 'POST', '/api/home-live-session/v1/start',
                token=base.token, command=base.command()).status_code, 200)
            self.assertIs(deferred.supervisor, base.supervisor)
            self.assertEqual(base.loads, [base.frozen])
            self.assertEqual(base.request(edge, 'POST', '/api/home-live-session/v1/stop',
                token=base.token, command=base.command('stop')).status_code, 200)
            self.assertIs(deferred.supervisor, base.supervisor)
            self.assertEqual(reader.call_count, 1)
        # An immutable declaration digest change refuses before a loader call.
        base.loads.clear(); base.frozen['command_capture']['changed'] = True
        with patch.object(stereo, 'read_private_record', return_value=deepcopy(base.frozen)):
            deferred = bootstrap.DeferredAssessedCommandLabSession(base.lab,
                provision=base.provision, loader=base.loader, supervisor_refusal=old.SupervisorRefusal)
            with self.assertRaisesRegex(old.SupervisorRefusal, 'command_declaration_digest_changed'):
                deferred.control(base.command())
        self.assertEqual(base.loads, [])


class AssessedCommandPackageMountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_lab_assessed_command_runtime import assessed_bundled_fixture
        from test_room_assessed_command_factory import authored_declaration
        import room_command_factory
        import hashlib
        cls.fixture = assessed_bundled_fixture(SimpleNamespace(addCleanup=cls.addClassCleanup))
        cls.value = deepcopy(cls.fixture.inputs)
        cls.declaration = authored_declaration()
        root = Path(__file__).resolve().parents[3]
        cls.declaration.update(source_bundle_digest=cls.value['runtime']['source_bundle_digest'],
            original_family=cls.value['producer']['desk_policy']['original_family'],
            bundle_input_policy_sha256=cls.value['producer']['desk_policy']['bundle_input_policy_sha256'],
            source_pins={name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                         for name in room_command_factory.ASSESSED_SOURCE_INVENTORY})
        cls.declaration['limits']['max_runtime_s'] = cls.value['runtime']['max_runtime_s']
        for key, basename in (('native_file', 'native-command.jsonl'),
                ('canonical_file', 'canonical-command.jsonl'), ('provenance_file', 'command-provenance.json')):
            cls.declaration['recording'][key] = str(cls.fixture.source.path / basename)
        cls.value['command_capture'] = deepcopy(cls.declaration)

    def prepare(self, value=None):
        from home_den_runtime.lab_stereo_session_package import prepare_stereo_session_package
        return prepare_stereo_session_package(self.value if value is None else value, now=self.fixture.source.f.now)

    def test_package7_is_inert_exact_and_keeps_commit_budget_independent_of_poll_capture(self):
        fail = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('package_effect'))
        with patch('subprocess.Popen', side_effect=fail), patch('subprocess.run', side_effect=fail), \
                patch('threading.Thread.start', side_effect=fail), patch('socket.socket', side_effect=fail), \
                patch('lab_live_desk_anchor.LabLiveDeskAnchor', side_effect=fail):
            result = self.prepare()
        self.assertEqual(result['status'], 'prepared', result)
        self.assertFalse(result['capability_constructed']); self.assertFalse(result['activation_ready'])
        self.assertEqual(result['version'], 7)
        provision = result['files']['mount.json']
        self.assertEqual((provision['version'], provision['producer']['version'],
            provision['runtime']['version'], provision['runtime']['session']['version'],
            provision['stereo_source']['version'], result['files']['bootstrap.json']['version']), (8, 8, 3, 3, 5, 7))
        self.assertEqual(provision['command_capture'], self.declaration)
        self.assertEqual(self.declaration['limits']['max_commits'], 256)
        self.assertNotIn('max_cycles', provision['runtime']['session'])
        self.assertEqual(result['files']['bootstrap.json']['command_capture_digest'], digest(self.declaration))
        self.assertIn('spotlight_semantic_CCT_mapping_unavailable', result['activation_blockers'])
        self.assertEqual(result, self.prepare())
        # This is the package's exact Home binding check, not a claim that the
        # separate complete JS/asset validation or browser adoption has run.
        value = deepcopy(self.value)
        live, scope = value['producer']['live_config'], result['bindings']['scope']
        value['home_entry'] = dict(schema='home-live-lab-entry-1', version=1,
            operator=deepcopy(result['files']['operator.json']),
            options=dict(qualification='live_shadow_unqualified', **deepcopy(scope),
                resident_binding=dict(schema='home-live-resident-binding-12', version=12,
                    manifest_ref=deepcopy(live['manifest_ref']), live_manifest=deepcopy(live['manifest']))),
            bridge=dict(schema='home-live-context-bridge-3',
                binding=dict(scope=deepcopy(scope), manifest_ref=deepcopy(live['manifest_ref']))))
        with_home = self.prepare(value)
        self.assertEqual(with_home['status'], 'prepared', with_home)
        self.assertEqual(with_home['files']['home-entry.json'], value['home_entry'])
        value['home_entry']['options']['resident_binding'].update(schema='home-live-resident-binding-11', version=11)
        refused = self.prepare(value)
        self.assertEqual(refused['status'], 'refused', refused)
        self.assertEqual(refused['missing_dependencies'], ['exact_existing_home_bridge_operator_binding_required'])
        for edit in (
                lambda v: v.update(command_capture=None),
                lambda v: v['command_capture']['task_profile_ref'].update(digest='f' * 64),
                lambda v: v['command_capture']['limits'].update(max_runtime_s=11),
                lambda v: v.update(schema='home-den-lab-session-inputs-6', version=6)):
            value = deepcopy(self.value); edit(value)
            rejected = self.prepare(value)
            self.assertEqual(rejected['status'], 'refused', rejected)
            self.assertEqual(rejected['files'], {})

    def test_real_mount8_borrows_same_store_provider_and_fixed_factory_without_starting(self):
        import test_lab_stereo_mount as old
        from home_den_runtime.room_command_factory import RoomCommandFactory
        from lab_bundled_rgb_stereo import BundledRgbStereoSource
        f = self.fixture
        prepared = self.prepare()
        self.assertEqual(prepared['status'], 'prepared', prepared)
        provision = prepared['files']['mount.json']
        tokens = {provision['producer']['publication_token_file']: old.TOKEN,
                  provision['runtime']['worker_config']['token_file']: 'authored-stereo-worker-token'}
        async def app(_scope, _receive, _send): raise AssertionError('ordinary_app_not_called_at_idle_mount')
        app.state = SimpleNamespace(store=f.source.f.store)
        with patch.object(stereo, 'read_private_record', side_effect=f.read_record), \
                patch.object(stereo, 'utc_now', side_effect=lambda: f.source.f.now), \
                patch.object(mount, 'private_token', side_effect=lambda filename: tokens[filename]), \
                patch('subprocess.Popen', side_effect=AssertionError('no_idle_native_or_worker')), \
                patch('threading.Thread.start', side_effect=AssertionError('no_idle_thread')), \
                patch('http.client.HTTPConnection.connect', side_effect=AssertionError('no_idle_network')):
            mounted = mount._mount_provision(app, provision, incumbent=f.source.f.runtime,
                occupancy=object(), lab_grants=[{'token_sha256': 'e' * 64}], lab_store=f.source.f.store,
                stereo_qualification_authority=f.authority)
        supervisor = mounted.supervisor
        composition = supervisor.graph_factory.__self__
        self.assertIs(composition.store, f.source.f.store)
        self.assertIsInstance(composition.bundled_source, BundledRgbStereoSource)
        self.assertIs(composition.bundled_source.runtime, f.source.f.runtime)
        self.assertIs(composition.bundled_source, composition.desk_capability.anchor_source.bundled_rgb_source)
        self.assertIs(type(supervisor.command_factory), RoomCommandFactory)
        self.assertIs(supervisor.command_factory.composition, composition)
        self.assertEqual(supervisor.command_factory.declaration, self.declaration)
        self.assertIsNone(supervisor.graph); self.assertIsNone(supervisor.command_owner)
        self.assertEqual(supervisor.status()['status'], 'idle')
        self.assertEqual(supervisor.stop(reason='authored_idle_assessed_mount_stop')['status'], 'stopped')
        self.assertTrue(composition.cancel.is_set())
        self.assertEqual(f.authority.resolve_calls, [])
        self.assertEqual(f.source.f.store.get_session(f.source.f.session['id'])['state'], 'recording')
        for path in (provision['producer']['journal_path'], provision['producer']['original_archive']['filename'],
                provision['runtime']['archive_file'], provision['stereo_source']['media']['directory'],
                *[self.declaration['recording'][k] for k in ('native_file', 'canonical_file', 'provenance_file')]):
            self.assertFalse(Path(path).exists(), path)


if __name__ == '__main__':
    unittest.main()
