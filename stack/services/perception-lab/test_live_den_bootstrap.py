"""Deferred Den binding through the real operator ASGI edge; no external I/O."""
import asyncio
from copy import deepcopy
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

import live_den_bootstrap as bootstrap
from live_den_mount import _runtime_module


_, SessionControlEdge = _runtime_module()
from home_den_runtime.supervisor import SupervisorRefusal


class AuthoredSupervisor:
    def __init__(self, session):
        self.session = deepcopy(session)
        self.commands = []
        self.stops = []

    def control(self, command):
        if (command['session_ref'] != self.session['session_ref']
                or command['configuration_digest'] != self.session['configuration_digest']):
            raise SupervisorRefusal('operator_command_binding_refused')
        self.commands.append(deepcopy(command))
        return {'status': 'started' if command['operation'] == 'start' else 'idle',
                'physical_authority': False}

    def stop(self, *, reason):
        self.stops.append(reason)
        return {'status': 'stopped', 'physical_authority': False}


class AuthoredMount:
    def __init__(self, supervisor, guard):
        self.state = SimpleNamespace(den_session_supervisor=supervisor,
                                     den_shutdown_guard=guard)
        self.requests = []

    async def __call__(self, scope, receive, send):
        self.requests.append(scope['path'])
        await send({'type': 'http.response.start', 'status': 200,
                    'headers': [(b'content-type', b'application/json')]})
        await send({'type': 'http.response.body', 'body': b'{"route":"bound"}'})


class DeferredDenBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.token = 'authored-bootstrap-operator-' + 'o' * 40
        self.provision = {'schema': 'home-den-lab-bootstrap-1', 'version': 1,
            'mount_file': str(Path(__file__).resolve().parent / 'authored-den-mount.json'),
            'operator_token_sha256': hashlib.sha256(self.token.encode()).hexdigest(),
            'scope': {'site_id': 'victoria', 'room_id': 'den'}}
        self.session = {'session_ref': {'site_id': 'victoria', 'room_id': 'den',
                         'session_id': 'authored-session', 'domain_epoch': 'authored-epoch'},
                        'configuration_digest': 'c' * 64}
        self.frozen = {'schema': 'home-managed-den-mount-2', 'version': 2,
            'operator_token_sha256': self.provision['operator_token_sha256'],
            'runtime': {'session': deepcopy(self.session)}}
        self.supervisor = AuthoredSupervisor(self.session)
        self.guards = []
        self.mount = AuthoredMount(self.supervisor, self.guard)
        self.loads, self.lab_requests = [], []

        async def lab(scope, receive, send):
            self.lab_requests.append(scope['path'])
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-type', b'application/json')]})
            await send({'type': 'http.response.body', 'body': b'{"route":"lab"}'})
        self.lab = lab
        self.lab.state = SimpleNamespace()
        self.read = patch.object(bootstrap, 'private_json', side_effect=lambda _: deepcopy(self.frozen))
        self.reader = self.read.start()
        self.addCleanup(self.read.stop)

    def guard(self):
        self.guards.append('guard')
        return True

    def loader(self, frozen):
        self.loads.append(deepcopy(frozen))
        return self.mount

    def deferred(self, loader=None):
        return bootstrap.DeferredLabSession(self.lab, provision=self.provision,
            loader=loader or self.loader, supervisor_refusal=SupervisorRefusal)

    def command(self, operation='start', **changes):
        result = {'schema': 'home-live-session-command-1', 'version': 1,
            'id': 'authored-' + operation, 'operation': operation, **deepcopy(self.session)}
        result.update(changes)
        return result

    def request(self, app, method, path, *, token=None, command=None):
        async def run():
            headers = {'Authorization': 'Bearer ' + token} if token else {}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='https://authored-controller.invalid') as client:
                return await client.request(method, path, headers=headers, json=command)
        return asyncio.run(run())

    def blocked_start(self, deferred):
        errors = []
        def start():
            try:
                deferred.control(self.command())
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=start)
        thread.start()
        self.addCleanup(lambda: thread.join(3))
        return thread, errors

    def test_boot_does_not_read_mount_or_load_and_ordinary_lab_stays_available(self):
        deferred = self.deferred()
        edge = SessionControlEdge(deferred.dispatch, supervisor=deferred,
                                  token_sha256=self.provision['operator_token_sha256'])
        response = self.request(edge, 'GET', '/api/perception-lab/v1/capabilities')
        self.assertEqual(response.json(), {'route': 'lab'})
        self.assertIsNone(deferred.supervisor)
        self.assertEqual(self.loads, [])
        self.reader.assert_not_called()

    def test_unbound_context_refuses_without_entering_lab_or_loader(self):
        deferred = self.deferred()
        response = self.request(deferred.dispatch, 'GET', '/api/home-context/v1/snapshot')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['reason'], 'den_binding_not_prepared')
        self.assertEqual(self.lab_requests, [])
        self.assertEqual(self.loads, [])
        self.reader.assert_not_called()

    def test_real_operator_edge_auth_and_route_binding_precede_loading(self):
        deferred = self.deferred()
        edge = SessionControlEdge(deferred.dispatch, supervisor=deferred,
                                  token_sha256=self.provision['operator_token_sha256'])
        for token in (None, 'authored-wrong-operator'):
            with self.subTest(token=token):
                response = self.request(edge, 'POST', '/api/home-live-session/v1/start',
                                        token=token, command=self.command())
                self.assertEqual(response.status_code, 403)
        response = self.request(edge, 'POST', '/api/home-live-session/v1/stop',
                                token=self.token, command=self.command('start'))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.loads, [])
        self.reader.assert_not_called()

    def test_authenticated_start_reads_exact_binding_once_and_uses_same_supervisor(self):
        deferred = self.deferred()
        edge = SessionControlEdge(deferred.dispatch, supervisor=deferred,
                                  token_sha256=self.provision['operator_token_sha256'])
        response = self.request(edge, 'POST', '/api/home-live-session/v1/start',
                                token=self.token, command=self.command())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'started')
        self.assertIs(deferred.supervisor, self.supervisor)
        self.assertEqual(self.loads, [self.frozen])
        self.frozen['runtime']['session']['configuration_digest'] = 'd' * 64
        deferred.control(self.command('status'))
        self.assertEqual(self.reader.call_count, 1)
        self.assertEqual(self.supervisor.commands[-1], self.command('status'))
        response = self.request(edge, 'GET', '/api/home-context/v1/snapshot')
        self.assertEqual(response.json(), {'route': 'bound'})

    def test_changed_provision_binding_is_rejected_before_loader(self):
        for key in ('operator', 'session', 'digest'):
            with self.subTest(key=key):
                value = deepcopy(self.frozen)
                if key == 'operator':
                    value['operator_token_sha256'] = 'f' * 64
                elif key == 'session':
                    value['runtime']['session']['session_ref']['session_id'] = 'different-session'
                else:
                    value['runtime']['session']['configuration_digest'] = 'f' * 64
                self.reader.side_effect = lambda _, value=value: deepcopy(value)
                with self.assertRaisesRegex(SupervisorRefusal, 'den_binding_provision_refused'):
                    self.deferred().control(self.command())
        self.assertEqual(self.loads, [])

    def test_refused_start_does_not_capture_binding_against_a_later_correct_start(self):
        deferred = self.deferred()
        with self.assertRaisesRegex(SupervisorRefusal, 'den_binding_provision_refused'):
            deferred.control(self.command(configuration_digest='f' * 64))
        deferred.control(self.command())
        self.assertEqual(self.supervisor.commands, [self.command()])

    def test_bootstrap_scope_is_copied_independently_of_callers_dictionary(self):
        deferred = self.deferred()
        self.provision['scope']['site_id'] = 'other-site'
        deferred.control(self.command())
        self.assertEqual(self.supervisor.commands, [self.command()])

    def test_stop_before_any_start_refuses_without_cancelling_future_binding(self):
        deferred = self.deferred()
        with self.assertRaisesRegex(SupervisorRefusal, 'den_binding_not_prepared'):
            deferred.control(self.command('stop'))
        self.reader.assert_not_called()
        deferred.control(self.command())
        self.assertEqual(self.supervisor.commands, [self.command()])

    def test_stop_during_private_configuration_read_cancels_before_loader(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def read(_):
            entered.set()
            self.assertTrue(release.wait(3))
            return deepcopy(self.frozen)
        self.reader.side_effect = read
        deferred = self.deferred()
        thread, errors = self.blocked_start(deferred)
        self.assertTrue(entered.wait(2))
        try:
            self.assertEqual(deferred.control(self.command('stop'))['status'], 'cancelled')
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.loads, [])
        self.assertEqual(self.supervisor.commands, [])
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), 'den_binding_cancelled')

    def test_parallel_starts_have_one_loader_and_one_supervisor(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(frozen):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.loader(frozen)
        deferred = self.deferred(load)
        thread, errors = self.blocked_start(deferred)
        self.assertTrue(entered.wait(2))
        try:
            with self.assertRaisesRegex(SupervisorRefusal, 'den_binding_preparing'):
                deferred.control(self.command(id='authored-other-start'))
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(self.loads), 1)
        self.assertEqual(self.supervisor.commands, [self.command()])

    def test_cancel_during_load_never_starts_and_runs_full_late_cleanup(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(frozen):
            entered.set()
            self.assertTrue(release.wait(3), 'authored loader was not released')
            return self.loader(frozen)
        deferred = self.deferred(load)
        thread, errors = self.blocked_start(deferred)
        self.assertTrue(entered.wait(2))
        self.assertEqual(deferred.control(self.command('stop'))['status'], 'cancelled')
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), 'den_binding_cancelled')
        self.assertEqual(self.supervisor.commands, [])
        self.assertTrue(self.guards, 'late binding must invoke full shutdown guard')

    def test_shutdown_during_load_returns_unconfirmed_and_cleans_late_result(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(frozen):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.loader(frozen)
        deferred = self.deferred(load)
        thread, errors = self.blocked_start(deferred)
        self.assertTrue(entered.wait(2))
        self.assertFalse(deferred.shutdown())
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.supervisor.commands, [])
        self.assertEqual(len(errors), 1)
        self.assertTrue(self.guards, 'shutdown must reach a mount returned after cancellation')

    def test_stop_for_other_exact_session_cannot_cancel_the_binding_being_loaded(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def load(frozen):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.loader(frozen)
        deferred = self.deferred(load)
        thread, errors = self.blocked_start(deferred)
        self.assertTrue(entered.wait(2))
        try:
            wrong = self.command('stop')
            wrong['session_ref']['session_id'] = 'other-session'
            with self.assertRaises(SupervisorRefusal):
                deferred.control(wrong)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.supervisor.commands, [self.command()])

    def test_malformed_private_mount_is_typed_refusal_with_no_loader(self):
        self.reader.side_effect = lambda _: ['malformed-local-provision']
        with self.assertRaises(SupervisorRefusal):
            self.deferred().control(self.command())
        self.assertEqual(self.loads, [])

    def test_real_mount_cannot_replace_pending_bootstrap_shutdown_guard(self):
        import live_den_mount as mount_module
        from test_lab_live_mount import IncumbentLabMountTests
        fixture = IncumbentLabMountTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        provision = {'schema': 'home-den-lab-bootstrap-1', 'version': 1,
            'mount_file': str(fixture.root / 'authored-deferred.json'),
            'operator_token_sha256': fixture.provision['operator_token_sha256'],
            'scope': {key: fixture.provision['runtime']['session']['session_ref'][key]
                      for key in ('site_id', 'room_id')}}
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real_mount = mount_module._mount_provision
        def delayed_mount(*args, **kwargs):
            value = real_mount(*args, **kwargs)
            entered.set()
            self.assertTrue(release.wait(3))
            return value
        command = {'schema': 'home-live-session-command-1', 'version': 1,
            'id': 'authored-real-mount-start', 'operation': 'start',
            'session_ref': deepcopy(fixture.provision['runtime']['session']['session_ref']),
            'configuration_digest': fixture.provision['runtime']['session']['configuration_digest']}
        errors = []
        with patch.object(mount_module, '_mount_provision', side_effect=delayed_mount), \
                patch.object(mount_module, 'private_token', side_effect=lambda path: fixture.tokens[path]), \
                patch.object(bootstrap, 'private_json', return_value=deepcopy(fixture.provision)):
            mounted = mount_module._mount_bootstrap(fixture.app, provision,
                incumbent=fixture.incumbent, occupancy=object(), lab_grants=fixture.lab_grants,
                la_perceiver=None, lab_store=fixture.fixture.f.store)
            deferred = mounted.supervisor
            def start():
                try:
                    deferred.control(command)
                except BaseException as error:
                    errors.append(error)
            thread = threading.Thread(target=start)
            thread.start()
            try:
                self.assertTrue(entered.wait(2), 'real local mount did not return: ' + str(errors))
                self.assertFalse(fixture.app.state.den_shutdown_guard(),
                                 'pending bootstrap must own shutdown until loader is settled')
            finally:
                release.set()
                thread.join(3)
                # Even a failing regression must not leave a preparation thread.
                deferred.shutdown()
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertRegex(str(errors[0]), 'den_binding_cancelled')


class PollBudgetDenBootstrapTests(unittest.TestCase):
    """Actual authenticated edge, fixed loader port double, no source starts."""
    def setUp(self):
        from journal import digest
        import lab_stereo_mount_source
        self.base = base = DeferredDenBootstrapTests()
        base.setUp(); self.addCleanup(base.doCleanups)
        base.provision.update(schema='home-den-lab-bootstrap-6', version=6,
                              command_capture_digest=digest(None))
        base.frozen.update(schema='home-managed-den-mount-7', version=7,
            command_capture=None, producer={'schema': 'home-incumbent-lab-producer-7', 'version': 7},
            stereo_source={'schema': 'home-lab-stereo-source-config-4', 'version': 4})
        base.frozen['runtime'].update(schema='home-live-runtime-config-2', version=2)
        base.frozen['runtime']['session'].update(schema='home-live-session-config-2', version=2)
        reader = patch.object(lab_stereo_mount_source, 'read_private_record',
            side_effect=lambda *_args, **_kwargs: deepcopy(base.frozen))
        self.reader = reader.start(); self.addCleanup(reader.stop)

    def deferred(self, provision=None):
        return bootstrap.DeferredPollBudgetBundledLabSession(self.base.lab,
            provision=provision or self.base.provision, loader=self.base.loader,
            supervisor_refusal=SupervisorRefusal)

    def test_bootstrap6_authenticates_binds_once_and_stop_keeps_same_owner(self):
        base = self.base; deferred = self.deferred()
        edge = SessionControlEdge(deferred.dispatch, supervisor=deferred,
                                  token_sha256=base.provision['operator_token_sha256'])
        self.reader.assert_not_called()
        for token in (None, 'authored-wrong-operator'):
            self.assertEqual(base.request(edge, 'POST', '/api/home-live-session/v1/start',
                token=token, command=base.command()).status_code, 403)
        self.reader.assert_not_called(); self.assertEqual(base.loads, [])
        response = base.request(edge, 'POST', '/api/home-live-session/v1/start',
                                token=base.token, command=base.command())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(deferred.supervisor, base.supervisor)
        self.assertEqual(base.loads, [base.frozen])
        response = base.request(edge, 'POST', '/api/home-live-session/v1/stop',
                                token=base.token, command=base.command('stop'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(base.supervisor.commands[-1], base.command('stop'))
        self.assertIs(deferred.supervisor, base.supervisor)
        self.assertEqual(self.reader.call_count, 1)

    def test_bootstrap6_refuses_command_digest_and_crossed_mount_before_loader(self):
        bad = deepcopy(self.base.provision); bad['command_capture_digest'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'poll_budget_null_command_digest_required'):
            self.deferred(bad)
        self.reader.assert_not_called()
        original = deepcopy(self.base.frozen)
        mutations = [
            lambda p: p.update(command_capture={}), lambda p: p.pop('command_capture'),
            lambda p: p['producer'].update(schema='home-incumbent-lab-producer-6', version=6),
            lambda p: p['runtime'].update(schema='home-live-runtime-config-1', version=1),
            lambda p: p['runtime']['session'].update(schema='home-live-session-config-1', version=1),
            lambda p: p['stereo_source'].update(schema='home-lab-stereo-source-config-3', version=3),
        ]
        for index, mutate in enumerate(mutations):
            self.base.frozen = deepcopy(original); mutate(self.base.frozen)
            with self.subTest(index=index), self.assertRaises(SupervisorRefusal):
                self.deferred().control(self.base.command())
            self.assertEqual(self.base.loads, [])


if __name__ == '__main__':
    unittest.main()
