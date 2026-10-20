"""Authenticated source5 bootstrap with a missing installed command capability.

Camera/model transport, qualification and Windows private-file/fsync ports are
authored. The actual source5 graph and READY Recorder must reach the trusted
factory; its deliberate refusal proves cleanup, not positive native commands.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_lab_existing_api_desk_runtime as source_fixture
import test_lab_stereo_mount as historical_mount
import test_lab_stereo_session_package as package_fixture
import test_live_den_preflight as preflight_fixture
import live_den_mount as mount
import lab_stereo_mount_source as stereo
import live_den_preflight as preflight
from journal import Journal
from paired_preview_client import encoded

mount._runtime_module()
from home_den_runtime.archive import Recorder
from home_den_runtime.lab_stereo_session_package import prepare_stereo_session_package


class ExistingApiMountTests(unittest.TestCase):
    def setUp(self):
        self.source = source_fixture.ExistingApiDeskRuntimeTests.make_source()
        self.addCleanup(self.source.doCleanups)
        self.source.setUp()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.mounted = None
        self.addCleanup(self.cleanup_mount)
        self.inputs = package_fixture.LabStereoSessionPackageTests.inputs.__func__(
            SimpleNamespace(source=self.source))
        self.inputs.update(schema='home-den-lab-session-inputs-3', version=3)
        self.inputs['stereo'].update(schema='home-den-stereo-package-inputs-2', version=2)
        node = shutil.which('node')
        self.assertIsNotNone(node, 'existing workstation Node runtime required')
        self.inputs['runtime']['node_runtime'] = str(Path(node).resolve())
        self.prepared = prepare_stereo_session_package(self.inputs, now=self.source.f.now)
        self.assertEqual(self.prepared['status'], 'prepared', self.prepared)
        self.files, self.paths = self.prepared['files'], self.inputs['provisioning']
        self.provision = self.files['mount.json']
        self.records = {
            self.paths['mount_file']: self.provision,
            self.paths['commissioning_file']: self.files['stereo-commissioning.json'],
            self.paths['qualification_file']: self.files['stereo-qualification.json'],
        }
        self.reads, self.factory_calls = [], []
        self.factory_entered = threading.Event()
        self.authority = historical_mount.AuthoredQualificationAuthority(
            self.source, self.inputs['stereo']['authority_binding'])
        self.operator_token = 'authored-stereo-operator'
        self.tokens = {
            self.provision['producer']['publication_token_file']: 'synthetic-publication-token',
            self.provision['runtime']['worker_config']['token_file']: 'authored-no-worker-start',
        }
        # Preserve the actual fixture-owned publication secret without allowing
        # caller-selected credentials or weakening the mount's token separation.
        publication = Path(self.provision['producer']['publication_token_file'])
        self.tokens[str(publication)] = publication.read_bytes().decode('utf8')
        self.stack.enter_context(patch.object(mount, 'private_token', side_effect=self.read_token))
        self.stack.enter_context(patch.object(mount, 'private_json', return_value=self.files['bootstrap.json']))
        self.stack.enter_context(patch.object(stereo, 'read_private_record', side_effect=self.read_record))
        self.stack.enter_context(patch.object(stereo, 'utc_now', side_effect=lambda: self.source.f.now))
        self.stack.enter_context(patch.object(mount, 'LocalDenPreflight', side_effect=self.local_preflight))
        self.stack.enter_context(patch.object(preflight, 'time', SimpleNamespace(
            time_ns=lambda: int(Decimal(str(self.source.f.now)) * Decimal(1000000000)))))

        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', b'application/json')]})
            await send({'type': 'http.response.body', 'body': b'{"route":"ordinary-lab"}'})
        app.state = SimpleNamespace(store=self.source.f.store)
        self.app = app

    def read_token(self, filename):
        self.assertIn(str(filename), self.tokens)
        return self.tokens[str(filename)]

    def read_record(self, filename, *, maximum):
        self.reads.append(str(filename))
        value = self.records[str(filename)]
        self.assertLessEqual(len(encoded(value)), maximum)
        return deepcopy(value)

    def local_preflight(self, filename, **kwargs):
        self.assertEqual(filename, self.paths['preflight_file'])
        external = preflight_fixture.LocalDenPreflightTests()
        external.setUp()
        receipt = deepcopy(self.inputs['preflight'])
        return preflight.LocalDenPreflight(filename, **kwargs,
            clock=lambda: self.source.f.now, monotonic=time.monotonic,
            reader=lambda name: deepcopy(receipt),
            probe=lambda *_args: deepcopy(external.probe))

    def command(self, operation):
        session = self.provision['runtime']['session']
        return dict(schema='home-live-session-command-1', version=1,
            id='authored-source5-' + operation, operation=operation,
            session_ref=deepcopy(session['session_ref']),
            configuration_digest=session['configuration_digest'])

    def request(self, operation, *, token=None):
        return historical_mount.LabStereoMountTests.request(self.mounted, 'POST',
            '/api/home-live-session/v1/' + operation, token=token, command=self.command(operation))

    def wait_terminal(self, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            response = self.request('status', token=self.operator_token)
            self.assertEqual(response.status_code, 200, response.text)
            value = response.json()
            if value['status'] in {'stopped', 'incomplete', 'refused'}:
                return value
            time.sleep(.02)
        self.fail('bounded public source5 startup/cleanup did not settle')

    def cleanup_mount(self):
        if self.mounted is None:
            return
        self.request('stop', token=self.operator_token)
        self.wait_terminal()
        supervisor = self.mounted.supervisor.supervisor
        if supervisor is not None and supervisor.thread is not None:
            supervisor.thread.join(2)
            self.assertFalse(supervisor.thread.is_alive(), 'owned preparation must settle before fixture cleanup')

    def test_authenticated_source5_start_reaches_ready_recorder_then_missing_command_refuses(self):
        def missing_canonical(graph, recorder):
            self.factory_calls.append((graph, recorder, recorder.status()))
            self.factory_entered.set()
            raise RuntimeError('authored_missing_installed_canonical_dependency')

        self.assertEqual(self.provision['schema'], 'home-managed-den-mount-4')
        self.assertEqual(self.files['bootstrap.json']['schema'], 'home-den-lab-bootstrap-3')
        self.mounted = mount.mount_den_session(self.app, self.paths['bootstrap_file'],
            incumbent=self.source.f.runtime, occupancy=object(), lab_grants=[{'token_sha256': 'e' * 64}],
            lab_store=self.source.f.store, stereo_qualification_authority=self.authority,
            command_factory=missing_canonical)
        self.assertIsNone(self.mounted.supervisor.supervisor)
        self.assertEqual(self.factory_calls, [])
        self.assertEqual(self.reads, [])
        denied = self.request('start', token='authored-wrong-token')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.factory_calls, [])
        self.assertEqual(self.reads, [])
        started = self.request('start', token=self.operator_token)
        self.assertEqual(started.status_code, 200, started.text)
        terminal = self.wait_terminal()
        self.assertTrue(self.factory_entered.is_set(), terminal)
        self.assertEqual(len(self.factory_calls), 1)
        self.assertEqual(terminal['status'], 'incomplete', terminal)
        self.assertEqual(terminal['reason'], 'session_start_unconfirmed')
        self.assertFalse(terminal['physical_authority'])
        self.assertEqual(terminal['commands']['status'], 'unavailable')
        self.assertEqual(terminal['drain']['recorder_status'], 'incomplete')
        supervisor = self.mounted.supervisor.supervisor
        graph, recorder, entry_recording = self.factory_calls[0]
        self.assertIs(type(graph.journal), Journal)
        self.assertIs(type(recorder), Recorder)
        self.assertEqual(entry_recording['status'], 'ready')
        self.assertEqual(graph.config['version'], 5)
        provider = graph.desk_capability.anchor_source.existing_api_source
        self.assertIs(provider.store, self.source.f.store)
        self.assertIs(provider.tap, self.source.f.runtime.existing_api_assessment)
        self.assertIsNone(graph.journal._recorder)
        self.assertIs(supervisor.graph, graph)
        self.assertIs(supervisor.recorder, recorder)
        self.assertIsNone(supervisor.worker)
        self.assertEqual(supervisor.cycles, 0)
        self.assertTrue(graph.finished, 'missing command dependency must retire the borrowed graph')
        self.assertEqual(self.reads.count(self.paths['mount_file']), 1)
        stopped = self.request('stop', token=self.operator_token)
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json(), terminal)
        self.assertEqual(self.wait_terminal(), terminal)


if __name__ == '__main__':
    unittest.main()
