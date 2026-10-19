"""Attach one protected Lab session after controller boot, never select via HTTP.

The unchanged operator edge authenticates and bounds each command first. Only
Start realizes the fixed local configuration; ordinary Lab routes remain live.
"""
from copy import deepcopy
import re
import threading

from live_detector_app import private_json


class DeferredLabSession:
    BOOTSTRAP = ('home-den-lab-bootstrap-1', 1)
    MOUNT = ('home-managed-den-mount-2', 2)
    KEYS = {'schema', 'version', 'mount_file', 'operator_token_sha256', 'scope'}

    def check_mount(self, frozen):
        return None

    def read_mount(self, filename):
        return private_json(filename)

    def __init__(self, app, *, provision, loader, supervisor_refusal):
        from pathlib import Path
        if (not isinstance(provision, dict) or set(provision) !=
                self.KEYS
                or (provision['schema'], provision['version']) != self.BOOTSTRAP
                or type(provision['version']) is not int
                or not isinstance(provision['mount_file'], str)
                or not Path(provision['mount_file']).is_absolute()
                or not isinstance(provision['operator_token_sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', provision['operator_token_sha256'])
                or not isinstance(provision['scope'], dict)
                or set(provision['scope']) != {'site_id', 'room_id'}
                or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value)
                       for value in provision['scope'].values())
                or not callable(loader)):
            raise ValueError('fixed_den_lab_bootstrap_required')
        self.app, self.provision, self.loader = app, deepcopy(provision), loader
        self.refusal = supervisor_refusal
        self.lock, self.cancel = threading.Lock(), threading.Event()
        self.state_lock = threading.Lock()
        self.binding = None
        self.mounted = self.supervisor = self.shutdown_guard = None

    def control(self, command):
        if (not isinstance(command, dict) or set(command) !=
                {'schema', 'version', 'id', 'operation', 'session_ref', 'configuration_digest'}
                or command['schema'] != 'home-live-session-command-1'
                or type(command['version']) is not int or command['version'] != 1
                or not isinstance(command['id'], str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', command['id'])
                or not isinstance(command['configuration_digest'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', command['configuration_digest'])
                or not isinstance(command['session_ref'], dict)
                or set(command['session_ref']) != {'site_id', 'room_id', 'session_id', 'domain_epoch'}
                or any(command['session_ref'].get(key) != value for key, value in self.provision['scope'].items())
                or any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value)
                       for value in command['session_ref'].values())
                or command['operation'] not in {'start', 'stop', 'status', 'read-results'}):
            raise self.refusal('explicit_operator_command_required')
        operation = command['operation']
        requested = (deepcopy(command['session_ref']), command['configuration_digest'])
        with self.state_lock:
            if self.binding is not None and requested != self.binding:
                raise self.refusal('operator_command_binding_refused')
            supervisor = self.supervisor
            if operation == 'stop':
                if self.binding is None:
                    raise self.refusal('den_binding_not_prepared')
                self.cancel.set()
                if supervisor is None:
                    return {'schema': 'home-den-bootstrap-status-1', 'version': 1,
                            'status': 'cancelled', 'binding_loaded': False, 'physical_authority': False}
        if supervisor is not None:
            return supervisor.control(command)
        if operation != 'start':
            raise self.refusal('den_binding_not_prepared')
        if self.cancel.is_set():
            raise self.refusal('den_binding_cancelled')
        if not self.lock.acquire(False):
            raise self.refusal('den_binding_preparing')
        try:
            if self.supervisor is None:
                with self.state_lock:
                    if self.cancel.is_set():
                        raise self.refusal('den_binding_cancelled')
                    self.binding = requested
                frozen = self.read_mount(self.provision['mount_file'])
                runtime = frozen.get('runtime') if isinstance(frozen, dict) else None
                session = runtime.get('session') if isinstance(runtime, dict) else None
                if (not isinstance(frozen, dict) or not isinstance(session, dict)
                        or (frozen.get('schema'), frozen.get('version')) != self.MOUNT
                        or type(frozen.get('version')) is not int
                        or frozen.get('operator_token_sha256') != self.provision['operator_token_sha256']
                        or session.get('session_ref') != command['session_ref']
                        or session.get('configuration_digest') != command['configuration_digest']):
                    raise self.refusal('den_binding_provision_refused')
                self.check_mount(frozen)
                with self.state_lock:
                    if self.cancel.is_set():
                        raise self.refusal('den_binding_cancelled')
                    self.binding = requested
                mounted = self.loader(frozen)
                supervisor = getattr(mounted.state, 'den_session_supervisor', None)
                guard = getattr(mounted.state, 'den_shutdown_guard', None)
                if supervisor is None or not callable(guard):
                    raise self.refusal('den_binding_incomplete')
                with self.state_lock:
                    self.shutdown_guard = guard
                    cancelled = self.cancel.is_set()
                    if not cancelled:
                        self.mounted, self.supervisor = mounted, supervisor
                if cancelled:
                    guard()
                    raise self.refusal('den_binding_cancelled')
            if self.cancel.is_set():
                self.supervisor.stop(reason='owner_stop_during_binding')
                raise self.refusal('den_binding_cancelled')
            return self.supervisor.control(command)
        except BaseException:
            with self.state_lock:
                if self.supervisor is None and not self.cancel.is_set():
                    self.binding = None
            raise
        finally:
            self.lock.release()

    def shutdown(self):
        with self.state_lock:
            self.cancel.set()
            guard = self.shutdown_guard
        if guard is not None:
            return guard()
        return not self.lock.locked()

    async def dispatch(self, scope, receive, send):
        if self.mounted is not None:
            return await self.mounted(scope, receive, send)
        if scope.get('type') == 'http' and scope.get('path', '').startswith('/api/home-context/'):
            await send({'type': 'http.response.start', 'status': 503,
                        'headers': [(b'content-type', b'application/json'), (b'cache-control', b'no-store')]})
            return await send({'type': 'http.response.body',
                               'body': b'{"reason":"den_binding_not_prepared"}'})
        return await self.app(scope, receive, send)


class DeferredStereoLabSession(DeferredLabSession):
    """Additive bootstrap2; inherits the authenticated, cancelable state machine."""
    BOOTSTRAP = ('home-den-lab-bootstrap-2', 2)
    MOUNT = ('home-managed-den-mount-3', 3)

    def read_mount(self, filename):
        from lab_stereo_mount_source import read_stereo_mount
        return read_stereo_mount(filename)


class DeferredExistingApiLabSession(DeferredLabSession):
    """Explicit bootstrap3/mount4; source5 keeps the same cancelable operator API."""
    BOOTSTRAP = ('home-den-lab-bootstrap-3', 3)
    MOUNT = ('home-managed-den-mount-4', 4)

    def read_mount(self, filename):
        from lab_stereo_mount_source import read_stereo_mount
        return read_stereo_mount(filename, version=4)


class DeferredCommandLabSession(DeferredLabSession):
    """Bootstrap4 pins the closed command declaration before mount5 realizes."""
    BOOTSTRAP = ('home-den-lab-bootstrap-4', 4)
    MOUNT = ('home-managed-den-mount-5', 5)
    KEYS = DeferredLabSession.KEYS | {'command_capture_digest'}

    def __init__(self, app, *, provision, loader, supervisor_refusal):
        if (not isinstance(provision, dict) or not isinstance(provision.get('command_capture_digest'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', provision['command_capture_digest'])):
            raise ValueError('fixed_command_bootstrap_digest_required')
        super().__init__(app, provision=provision, loader=loader, supervisor_refusal=supervisor_refusal)

    def read_mount(self, filename):
        from lab_stereo_mount_source import read_stereo_mount
        return read_stereo_mount(filename, version=5)

    def check_mount(self, frozen):
        from journal import digest
        if digest(frozen.get('command_capture')) != self.provision['command_capture_digest']:
            raise self.refusal('command_declaration_digest_changed')


class DeferredBundledLabSession(DeferredCommandLabSession):
    """Bootstrap5 pins mount6's nullable fixed command declaration."""
    BOOTSTRAP = ('home-den-lab-bootstrap-5', 5)
    MOUNT = ('home-managed-den-mount-6', 6)

    def read_mount(self, filename):
        from lab_stereo_mount_source import read_stereo_mount
        return read_stereo_mount(filename, version=6)


class DeferredPollBudgetBundledLabSession(DeferredBundledLabSession):
    """Bootstrap6 binds source7/mount7; command authority stays unavailable."""
    BOOTSTRAP = ('home-den-lab-bootstrap-6', 6)
    MOUNT = ('home-managed-den-mount-7', 7)

    def __init__(self, app, *, provision, loader, supervisor_refusal):
        from journal import digest
        if not isinstance(provision, dict) or provision.get('command_capture_digest') != digest(None):
            raise ValueError('poll_budget_null_command_digest_required')
        super().__init__(app, provision=provision, loader=loader, supervisor_refusal=supervisor_refusal)

    def read_mount(self, filename):
        from lab_stereo_mount_source import read_stereo_mount
        return read_stereo_mount(filename, version=7)

    def check_mount(self, frozen):
        super().check_mount(frozen)
        producer, runtime, source = (frozen.get(key) for key in ('producer', 'runtime', 'stereo_source'))
        session = runtime.get('session') if isinstance(runtime, dict) else None
        if (frozen.get('command_capture', object()) is not None
                or not isinstance(producer, dict) or producer.get('schema') != 'home-incumbent-lab-producer-7'
                or type(producer.get('version')) is not int or producer['version'] != 7
                or not isinstance(runtime, dict) or runtime.get('schema') != 'home-live-runtime-config-2'
                or type(runtime.get('version')) is not int or runtime['version'] != 2
                or not isinstance(session, dict) or session.get('schema') != 'home-live-session-config-2'
                or type(session.get('version')) is not int or session['version'] != 2
                or not isinstance(source, dict) or source.get('schema') != 'home-lab-stereo-source-config-4'
                or type(source.get('version')) is not int or source['version'] != 4):
            raise self.refusal('exact_poll_budget_mount_source_runtime_required')


class DeferredAssessedCommandLabSession(DeferredBundledLabSession):
    """Bootstrap7 pins source8's required declaration3 before mount8 realizes."""
    BOOTSTRAP = ('home-den-lab-bootstrap-7', 7)
    MOUNT = ('home-managed-den-mount-8', 8)

    def read_mount(self, filename):
        from lab_stereo_mount_source import read_stereo_mount
        return read_stereo_mount(filename, version=8)

    def check_mount(self, frozen):
        super().check_mount(frozen)
        producer, runtime, source = (frozen.get(key) for key in ('producer', 'runtime', 'stereo_source'))
        session = runtime.get('session') if isinstance(runtime, dict) else None
        declaration = frozen.get('command_capture')
        if (not isinstance(declaration, dict)
                or declaration.get('schema') != 'home-den-canonical-command-declaration-3'
                or type(declaration.get('version')) is not int or declaration['version'] != 3
                or not isinstance(producer, dict) or producer.get('schema') != 'home-incumbent-lab-producer-8'
                or type(producer.get('version')) is not int or producer['version'] != 8
                or not isinstance(runtime, dict) or runtime.get('schema') != 'home-live-runtime-config-3'
                or type(runtime.get('version')) is not int or runtime['version'] != 3
                or not isinstance(session, dict) or session.get('schema') != 'home-live-session-config-3'
                or type(session.get('version')) is not int or session['version'] != 3
                or not isinstance(source, dict) or source.get('schema') != 'home-lab-stereo-source-config-5'
                or type(source.get('version')) is not int or source['version'] != 5):
            raise self.refusal('exact_assessed_command_mount_source_runtime_required')
