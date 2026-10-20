"""Fixed canonical public client and bounded process launch; no physical port.

The client constructor is inert. Linux resource setup uses pinned executable
wrappers and Popen's native session/umask options, never a Python preexec_fn in
the threaded Lab. The existing command owner supplies the finite wall deadline.
"""
from copy import deepcopy
import builtins
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import ModuleType

from fixture_execution_validation import require
from room_canonical_provider import (LINUX_ARM64_PROVIDER, WINDOWS_PROVIDER,
    _read, selected_provider, verified_provider_export)

CLIENT_SOURCE_PINS = (
    ('tools/c12_fake_plant.py', '5d7f9e7866db56f66ed87bfc4f45a88e138f753c853f80b44f5ce5fa1d603299'),
    ('tools/c12_canonical_capture.py', 'ccf070dc25150295a88a1fbbf331bb093281cac674bf20c7c268f2400b87d977'),
)
LINUX_TOOLS = (
    ('/usr/bin/prlimit', '82e5c20b4f84f0d4b2076f34225252691a36cacfb5e1fa9db18609122ac238aa', 23200),
    ('/usr/bin/nice', '98d20ebf22f49471e25ff8a119196d7e450f85166c2f3abfbe4970ed6c53dc80', 26984),
    ('/usr/bin/taskset', 'ed27fe46a8bde002de8a97e4f63ae2d4fa428beada8f2ea5e3dcbc8befd4bdeb', 18592),
    ('/usr/bin/setpriv', 'cfd58dac200b7ec73a75175a51c5cb4320be2a24079117d6e2ad6c916941cfd5', 34984),
)
MAX_LAUNCHES = 4
_CLIENT = None
_LOAD_LOCK = threading.Lock()


def _client_originals():
    profile, manifest, _ = verified_provider_export()
    originals = {}
    for relative, expected in CLIENT_SOURCE_PINS:
        filename = profile.build / 'source' / relative
        raw = _read(filename, 256 * 1024)
        require(manifest['source']['files'][relative] == expected
            and hashlib.sha256(raw).hexdigest() == expected, 'fixed canonical client source changed')
        originals[Path(relative).stem] = (filename, raw)
    return profile, originals


def load_canonical_client():
    """Return the actual isolated, pinned SDK class; no process is created."""
    global _CLIENT
    with _LOAD_LOCK:
        profile, originals = _client_originals()
        if _CLIENT is not None:
            require(_CLIENT[0] == profile.provider_pins and _CLIENT[1] == dict(CLIENT_SOURCE_PINS),
                    'cached canonical client provider differs')
            return _CLIENT[2]
        imported = {}
        created = []
        def fixed_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name.startswith('c12_'):
                require(name in imported, 'client dependency outside fixed closure')
                return imported[name]
            return builtins.__import__(name, globals, locals, fromlist, level)
        try:
            for name, (filename, raw) in originals.items():
                unique = '_home_room_canonical_client_' + profile.manifest_sha256[:16] + '_' + name
                require(unique not in sys.modules, 'fixed client module identity already occupied')
                module = ModuleType(unique)
                module.__file__ = str(filename)
                module.__dict__['__builtins__'] = {**vars(builtins), '__import__': fixed_import}
                sys.modules[unique] = module
                created.append(unique)
                exec(compile(raw, str(filename), 'exec'), module.__dict__)
                imported[name] = module
        except BaseException:
            for unique in created:
                sys.modules.pop(unique, None)
            raise
        result = imported['c12_canonical_capture'].CanonicalCaptureProcess
        _CLIENT = (profile.provider_pins, dict(CLIENT_SOURCE_PINS), result)
        return result


def _launch_policy(profile, cpu):
    common = dict(schema='home-canonical-client-launch-policy-1', version=1,
        platform=profile.name, native_launches=MAX_LAUNCHES, physical_permission=False)
    if profile == WINDOWS_PROVIDER:
        return dict(common, backend='existing_windows_popen', tools={}, limits=None,
            resource_owner='external_windows_job_guard_not_installed_here')
    require(profile == LINUX_ARM64_PROVIDER and type(cpu) is int and cpu >= 0,
            'fixed canonical Linux launch profile required')
    return dict(common, backend='fixed_posix_exec_chain',
        tools={path: dict(sha256=pin, bytes=size) for path, pin, size in LINUX_TOOLS},
        limits=dict(cpu_seconds=30, address_space_bytes=256 * 1024**2,
            file_size_bytes=16 * 1024**2, open_files=128, cpu_affinity=[cpu],
            nice_increment=10, minimum_nice=10, no_new_privileges=True),
        start_new_session=True, resource_owner='finite_parent_command_session_maximum_120_seconds')


def _verify_linux_tools():
    for name, expected, size in LINUX_TOOLS:
        filename = Path(name)
        raw = _read(filename, size)
        require(filename.resolve() == filename and len(raw) == size
            and hashlib.sha256(raw).hexdigest() == expected and os.access(filename, os.X_OK),
            'fixed canonical launch utility changed')


class _OwnedGroupProcess:
    """Popen surface consumed by the SDK, with owned group cleanup."""
    def __init__(self, process):
        self._process = process
        self.pid = process.pid
        require(type(self.pid) is int and self.pid > 0 and self.pid != os.getpgrp(),
                'owned canonical child group required')
        self.stdin, self.stdout, self.stderr = process.stdin, process.stdout, process.stderr
        self.args = process.args

    def _alive(self):
        try:
            os.killpg(self.pid, 0)
        except ProcessLookupError:
            return False
        return True

    def poll(self):
        return self._process.poll()

    @property
    def returncode(self):
        return self._process.returncode

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        result = self._process.wait(timeout=timeout)
        while self._alive():
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(.01)
        return result

    def terminate(self):
        if self._alive():
            os.killpg(self.pid, signal.SIGTERM)

    def kill(self):
        if self._alive():
            os.killpg(self.pid, signal.SIGKILL)


class CanonicalClientFactory:
    """Inert fixed constructor passed directly to RoomCanonicalPlant.

    binding() describes the fixed policy, not runtime readiness or qualification.
    At most four starts are attempted by this capability; it never reopens one.
    """
    def __init__(self):
        self._profile = selected_provider()
        self._cpu = None
        if self._profile == LINUX_ARM64_PROVIDER:
            affinity = os.sched_getaffinity(0)
            require(bool(affinity), 'canonical launch CPU unavailable')
            self._cpu = min(affinity)
        self._binding = dict(provider_pins=self._profile.provider_pins,
            client_source_pins=dict(CLIENT_SOURCE_PINS), launch_policy=_launch_policy(self._profile, self._cpu))
        self._lock = threading.Lock()
        self._launches = 0

    def binding(self):
        return deepcopy(self._binding)

    def __call__(self, manifest_path, expected_manifest_sha256, *, raw_observer):
        require(selected_provider() == self._profile
            and Path(manifest_path) == self._profile.build / 'manifest.json'
            and expected_manifest_sha256 == self._profile.manifest_sha256 and callable(raw_observer),
            'fixed canonical client binding required')
        client_type = load_canonical_client()
        return client_type(manifest_path, expected_manifest_sha256,
            process_factory=self.process_factory, timeout=2.0, raw_observer=raw_observer)

    def process_factory(self, argv, **options):
        """Closed SDK Popen port; callers cannot select another command/options."""
        expected = [str(self._profile.build / self._profile.executable_name)]
        require(type(argv) is list and argv == expected and set(options) == {'stdin', 'stdout', 'stderr', 'text', 'bufsize'}
            and all(options[k] == subprocess.PIPE for k in ('stdin', 'stdout', 'stderr'))
            and options['text'] is False and type(options['bufsize']) is int and options['bufsize'] == 0,
            'closed canonical SDK process arguments required')
        require(selected_provider() == self._profile, 'fixed canonical launch platform changed')
        # Re-read immutable originals before reserving an irreversible launch.
        _client_originals()
        if self._profile == LINUX_ARM64_PROVIDER:
            _verify_linux_tools()
            require(self._cpu in os.sched_getaffinity(0) and os.getpriority(os.PRIO_PROCESS, 0) >= 0,
                    'fixed canonical CPU or priority unavailable')
        with self._lock:
            require(self._launches < MAX_LAUNCHES, 'canonical launch budget exhausted')
            self._launches += 1
        if self._profile == WINDOWS_PROVIDER:
            # The already installed Windows test/host guard owns its Job policy.
            return subprocess.Popen(argv, **options)
        command = ['/usr/bin/prlimit', '--cpu=30:30', '--as=268435456:268435456',
            '--fsize=16777216:16777216', '--nofile=128:128', '--',
            '/usr/bin/nice', '-n', '10', '/usr/bin/taskset', '--cpu-list', str(self._cpu),
            '/usr/bin/setpriv', '--no-new-privs', '--', expected[0]]
        process = subprocess.Popen(command, **options, close_fds=True, start_new_session=True,
            umask=0o077, cwd=str(self._profile.build),
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        return _OwnedGroupProcess(process)
