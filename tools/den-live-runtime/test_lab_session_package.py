"""Authored preparation through real config/profile/grant validators; no hosts."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / 'stack/services/home-context', ROOT / 'stack/services/perception-lab'):
    if str(directory) not in sys.path:
        sys.path.append(str(directory))

import lab_session_package as package
from journal import digest
from lab_live_runtime import lab_graph_configuration
import test_lab_live_semantic_runtime as semantic_runtime_fixture
import test_live_den_preflight as preflight_fixture


class LabSessionPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Setup uses the existing authored Store/model-manifest fixture only.
        # The prepare operation below is forbidden to open Store or any IO port.
        cls.base = semantic_runtime_fixture.LabLiveSemanticRuntimeTests()
        cls.base.setUp()
        cls.addClassCleanup(cls.base.doCleanups)
        cls.directory = Path(cls.base.f.path)
        cls.template = cls.inputs()

    @classmethod
    def inputs(cls):
        producer, worker = deepcopy(cls.base.producer), deepcopy(cls.base.worker_config)
        worker.pop('token', None)
        worker['token_file'] = str(cls.directory / 'worker.secret')
        context_hash = producer['journal_grants'][2]['token_sha256']
        provisioning = {k: str(cls.directory / name) for k, name in
                        [('mount_file', 'mount.json'), ('bootstrap_file', 'bootstrap.json'),
                         ('operator_file', 'operator.json'), ('preflight_file', 'preflight.json')]}
        provisioning.update(context_token_sha256=context_hash,
                            operator_token_sha256=hashlib.sha256(b'authored-operator').hexdigest(),
                            publication_token_sha256=producer['journal_grants'][0]['token_sha256'],
                            worker_token_sha256=producer['journal_grants'][1]['token_sha256'],
                            lab_token_sha256=['e' * 64])
        f = preflight_fixture.LocalDenPreflightTests()
        f.setUp()
        graph = lab_graph_configuration(producer, session=cls.base.f.session)
        f.receipt.update(configuration_digest=digest(graph),
                         control_clock={'policy': producer['live_config']['manifest']['sources'][producer['source_id']]['control_clock'],
                                        'uncertainty_s': .001})
        return {'schema': 'home-den-lab-session-inputs-1', 'version': 1,
                'session': deepcopy(cls.base.f.session),
                'producer': producer, 'worker_config': worker,
                'runtime': {'node_runtime': str(Path(sys.executable).parent / 'node.exe'),
                            'archive_file': str(cls.directory / 'session.jsonl'),
                            'archive_limits': {'max_records': 1024, 'max_bytes': 8 * 1024**2, 'min_free_bytes': 1},
                            'source_bundle_digest': 'b' * 64, 'max_cycles': 4, 'max_runtime_s': 10,
                            'interval_s': .5, 'drain_timeout_s': 3},
                'operator': {'upstream': 'https://authored-victoria.invalid:8443',
                             'ca_file': str(cls.directory / 'ca.pem'), 'token_file': str(cls.directory / 'operator.secret'), 'timeout_s': 5},
                'provisioning': provisioning, 'preflight': f.receipt,
                'evidence': {'registration': deepcopy(worker['room_policy']['registration']),
                             'equipment': {'authored': 'equipment-export-not-a-qualified-survey'},
                             'collision': {'authored': 'canonical-collision-not-inspected'},
                             'fixtures': deepcopy(worker['room_policy']['fixtures']),
                             'examples': deepcopy(worker['scene_policy']['catalog']),
                             'qualifications': {'authored': 'mechanism-test-only'},
                             'clocks': {'authored': 'independent-clock-input'}}}

    def setUp(self):
        self.value = deepcopy(self.template)
        self.now = 1800000000

    def prepare(self, value=None):
        return package.prepare_session_package(self.value if value is None else value, now=self.now)

    def assert_refused(self):
        result = self.prepare()
        self.assertEqual(result['status'], 'refused', result)
        self.assertFalse(result['activation_ready'])
        self.assertEqual(result['files'], {})
        self.assertTrue(result['missing_dependencies'])
        return result

    def test_preparation_preserves_exact_sources_and_generates_existing_interfaces(self):
        before = deepcopy(self.value)
        result = self.prepare()
        self.assertEqual(result['status'], 'prepared', result)
        self.assertEqual(self.value, before)
        self.assertFalse(result['activation_ready'])
        self.assertFalse(result['physical_authority'])
        mount, bootstrap, operator = (result['files'][k] for k in ('mount.json', 'bootstrap.json', 'operator.json'))
        self.assertEqual(mount['schema'], 'home-managed-den-mount-2')
        self.assertEqual(mount['producer'], before['producer'])
        self.assertEqual(mount['runtime']['worker_config'], before['worker_config'])
        self.assertEqual(mount['runtime']['session']['mode'], 'live_shadow_unqualified')
        self.assertEqual(bootstrap['schema'], 'home-den-lab-bootstrap-1')
        self.assertEqual(operator['schema'], 'home-live-operator-config-1')
        self.assertEqual(operator['configuration_digest'], digest(lab_graph_configuration(before['producer'], session=before['session'])))
        self.assertEqual(mount['runtime']['session']['session_ref'], before['producer']['live_config']['manifest']['identity'])
        self.assertIn('live_home_entry_successor_unavailable', result['activation_blockers'])
        self.assertIn('retired_session_rebinding_policy_unavailable', result['activation_blockers'])
        self.assertFalse(result['files']['preflight.json']['qualification_current'])
        self.assertNotIn('controlled', result['files'])
        self.assertEqual(result, self.prepare())
        self.value['evidence']['equipment']['authored'] = 'new export'
        self.assertNotEqual(result['package_digest'], self.prepare()['package_digest'])
        self.assertEqual(result['files']['evidence.json'], before['evidence'])
        self.assertEqual(result['bindings']['preflight_validation'], 'supplied_record_shape_only_no_resource_probe')

    def test_prepare_and_import_do_not_start_io_or_create_runtime_resources(self):
        fail = mock.Mock(side_effect=AssertionError('prepare attempted IO/start'))
        with ExitStack() as stack:
            for name in ('socket.socket', 'subprocess.run', 'threading.Thread.start',
                         'sqlite3.connect', 'urllib.request.urlopen',
                         'lab_live_runtime.private_token', 'live_den_preflight.local_probe',
                         'pathlib.Path.write_text', 'pathlib.Path.mkdir'):
                stack.enter_context(mock.patch(name, fail))
            result = self.prepare()
            spec = importlib.util.spec_from_file_location('den_package_import_guard', package.__file__)
            spec.loader.exec_module(importlib.util.module_from_spec(spec))
        self.assertEqual(result['status'], 'prepared', result)
        fail.assert_not_called()

    def test_absent_inputs_and_evidence_return_named_dependencies(self):
        self.assertEqual(package.prepare_session_package({}, now=self.now)['missing_dependencies'][0], 'input:evidence')
        for role in package.EVIDENCE:
            with self.subTest(role=role):
                self.value = deepcopy(self.template)
                self.value['evidence'][role] = None
                self.assertIn('evidence:' + role, self.assert_refused()['missing_dependencies'])

    def test_malformed_records_refuse_without_credential_echo(self):
        for field in ('session', 'producer', 'worker_config', 'runtime', 'operator', 'provisioning', 'preflight'):
            with self.subTest(field=field):
                self.value = deepcopy(self.template)
                self.value[field] = None
                self.assert_refused()

    def test_documented_safe_path_entry_runs_without_operator_shadow(self):
        run = subprocess.run([sys.executable, '-P', str(Path(package.__file__)), '--help'], cwd=ROOT,
                             text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('prepare', run.stdout)

    def test_tampered_original_scope_generation_profile_and_schema_refuse(self):
        mutations = [
            lambda v: v['session'].update(site_id='la'),
            lambda v: v['session'].update(generation=999),
            lambda v: v['session'].update(state='stopped'),
            lambda v: v['producer']['lab_binding'].update(model_generation=0),
            lambda v: v['producer']['lab_binding']['lab_profile'].update(artifact_sha256='f' * 64),
            lambda v: v['producer']['live_config']['manifest']['identity'].update(session_id='other'),
            lambda v: v['producer']['live_config'].update(version=9),
            lambda v: v['producer']['semantic_policy'].update(schema='home-lab-semantic-admission-policy-1'),
            lambda v: v['producer']['live_config']['manifest']['worker']['source_pins'].update(forged='f' * 64),
            lambda v: v['producer']['journal_grants'][0]['live_bindings'][0]['manifest_ref'].update(digest='f' * 64),
            lambda v: v['worker_config'].update(semantic_interval_s=30),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.value = deepcopy(self.template)
                mutate(self.value)
                self.assert_refused()

    def test_private_transport_is_fixed_disjoint_and_secret_free(self):
        mutations = [
            lambda v: v['operator'].update(upstream='http://authored.invalid'),
            lambda v: v['operator'].update(upstream='https://user:password@authored.invalid'),
            lambda v: v['operator'].update(upstream='https://authored.invalid/arbitrary'),
            lambda v: v['operator'].update(upstream='https://authored.invalid?url=elsewhere'),
            lambda v: v['provisioning'].update(operator_token_sha256=v['provisioning']['context_token_sha256']),
            lambda v: v['provisioning'].update(lab_token_sha256=[v['provisioning']['publication_token_sha256']]),
            lambda v: v['worker_config'].update(token='must-never-be-echoed'),
            lambda v: v['operator'].update(token_file=v['producer']['publication_token_file']),
            lambda v: v['producer']['journal_grants'][2].update(operations=['read', 'event', 'intent']),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.value = deepcopy(self.template)
                mutate(self.value)
                result = self.assert_refused()
                self.assertNotIn('must-never-be-echoed', json.dumps(result))

    def test_exact_evidence_path_and_resource_bounds(self):
        mutations = [
            lambda v: v['evidence']['registration'].update(geometry_digest='f' * 64),
            lambda v: v['evidence']['fixtures'][0].update(mount_id='wrong-mount'),
            lambda v: v['runtime'].update(max_cycles=25),
            lambda v: v['runtime'].update(max_runtime_s=121),
            lambda v: v['runtime'].update(source_bundle_digest='not-a-hash'),
            lambda v: v['runtime'].update(archive_file=v['producer']['journal_path']),
            lambda v: v['provisioning'].update(mount_file='relative.json'),
            lambda v: v['runtime']['archive_limits'].update(max_bytes=1024**4),
            lambda v: v['preflight'].update(configuration_digest='f' * 64),
            lambda v: v['preflight'].update(valid_until=1800000000),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.value = deepcopy(self.template)
                mutate(self.value)
                self.assert_refused()

    def test_cli_writes_only_new_package_and_never_overwrites_or_activates(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source = directory / 'inputs'
            source.mkdir()
            (source / 'inputs.json').write_text(json.dumps(self.value), encoding='utf-8')
            if sys.platform != 'win32':
                (source / 'inputs.json').chmod(0o600)
            output = directory / 'prepared'
            with redirect_stdout(io.StringIO()) as printed:
                code = package.main(['prepare', '--inputs', str(source), '--output', str(output), '--now', str(self.now)])
            self.assertEqual(code, 0, printed.getvalue())
            receipt = json.loads((output / 'package.json').read_text())
            self.assertFalse(receipt['activation_ready'])
            self.assertEqual(receipt['status'], 'prepared')
            self.assertEqual({p.name for p in output.iterdir()}, {'mount.json', 'bootstrap.json', 'operator.json', 'lab-session.json', 'preflight.json', 'evidence.json', 'package.json'})
            # The semantic binding hashes the complete original Store session.
            # A smaller reconstructed session is not an interchangeable input.
            saved_session = json.loads((output / 'lab-session.json').read_text())
            saved_producer = json.loads((output / 'mount.json').read_text())['producer']
            self.assertEqual(saved_session, self.value['session'])
            self.assertEqual(receipt['bindings']['file_digests']['lab-session.json'], digest(saved_session))
            self.assertEqual(digest(lab_graph_configuration(saved_producer, session=saved_session)),
                             receipt['bindings']['graph_configuration_digest'])
            with self.assertRaises(FileExistsError):
                package.main(['prepare', '--inputs', str(source), '--output', str(output), '--now', str(self.now)])


if __name__ == '__main__':
    unittest.main()
