"""Pure fixed-client/launch contracts; no native process or network is opened."""
from pathlib import Path
from contextlib import ExitStack
import signal
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'stack/services/home-context'))

import room_canonical_client as client
import room_canonical_provider as provider
from room_canonical_provider import LINUX_ARM64_PROVIDER, WINDOWS_PROVIDER


def sdk_pipes():
    return dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, bufsize=0)


class ProcessPort:
    """Authored external Popen result; no OS process or stream thread exists."""
    def __init__(self, args, **kwargs):
        self.args, self.pid, self.returncode = args, 54321, None
        self.stdin, self.stdout, self.stderr = object(), object(), object()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


class CanonicalClientTests(unittest.TestCase):
    def setUp(self):
        # Fixed OS metadata is an authored external port, as in provider tests.
        for name, value in (('system', 'Windows'), ('machine', 'AMD64')):
            port = patch.object(provider.platform, name, return_value=value)
            port.start()
            self.addCleanup(port.stop)

    def test_fixed_client_construction_is_inert_and_uses_actual_isolated_sdk(self):
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('unexpected native launch')) as spawn:
            factory = client.CanonicalClientFactory()
            member = factory(WINDOWS_PROVIDER.build / 'manifest.json', WINDOWS_PROVIDER.manifest_sha256,
                             raw_observer=lambda original: None)
            self.assertIs(type(member), client.load_canonical_client())
            self.assertEqual(member.manifest['executable']['sha256'], WINDOWS_PROVIDER.executable_sha256)
            self.assertEqual(factory.binding()['provider_pins'], WINDOWS_PROVIDER.provider_pins)
            self.assertFalse(member.recording_status['complete'])
            spawn.assert_not_called()
        self.assertNotIn('c12_canonical_capture', sys.modules)
        self.assertNotIn('c12_fake_plant', sys.modules)

    def test_binding_is_detached_and_foreign_constructor_inputs_refuse_before_launch(self):
        factory = client.CanonicalClientFactory()
        body = factory.binding()
        self.assertEqual(set(body), {'provider_pins', 'client_source_pins', 'launch_policy'})
        self.assertEqual(body['client_source_pins'], {
            'tools/c12_fake_plant.py': '5d7f9e7866db56f66ed87bfc4f45a88e138f753c853f80b44f5ce5fa1d603299',
            'tools/c12_canonical_capture.py': 'ccf070dc25150295a88a1fbbf331bb093281cac674bf20c7c268f2400b87d977'})
        body['provider_pins']['manifest_sha256'] = 'f' * 64
        body['launch_policy']['tools']['caller'] = True
        self.assertEqual(factory.binding()['provider_pins'], WINDOWS_PROVIDER.provider_pins)
        self.assertEqual(factory.binding()['launch_policy']['tools'], {})
        self.assertEqual(factory.binding()['launch_policy']['backend'], 'existing_windows_popen')
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('unexpected launch')) as spawn:
            for filename, pin, observer in (
                (Path('caller-selected.json'), WINDOWS_PROVIDER.manifest_sha256, lambda row: None),
                (WINDOWS_PROVIDER.build / 'manifest.json', 'f' * 64, lambda row: None),
                (WINDOWS_PROVIDER.build / 'manifest.json', WINDOWS_PROVIDER.manifest_sha256, None)):
                with self.subTest(filename=filename, pin=pin), self.assertRaises(ValueError):
                    factory(filename, pin, raw_observer=observer)
            spawn.assert_not_called()

    def test_cached_client_rechecks_both_extra_originals_and_uses_no_global_alias(self):
        original_type = client.load_canonical_client()
        self.assertTrue(original_type.__module__.startswith('_home_room_canonical_client_'))
        self.assertTrue(original_type.__mro__[1].__module__.startswith('_home_room_canonical_client_'))
        read = client._read  # Fixed file-I/O port; actual pinned Windows originals are read.
        for relative, _ in client.CLIENT_SOURCE_PINS:
            target = WINDOWS_PROVIDER.build / 'source' / relative
            def changed(filename, maximum):
                raw = read(filename, maximum)
                return raw[:-1] + bytes((raw[-1] ^ 1,)) if filename == target else raw
            with self.subTest(relative=relative), patch.object(client, '_read', side_effect=changed):
                with self.assertRaisesRegex(ValueError, 'fixed canonical client source changed'):
                    client.load_canonical_client()
        foreign = SimpleNamespace(CanonicalCaptureProcess=object, FakePlantProcess=object)
        with patch.dict(sys.modules, {'c12_canonical_capture': foreign, 'c12_fake_plant': foreign}):
            self.assertIs(client.load_canonical_client(), original_type)

    def test_windows_delegates_exact_sdk_options_and_fifth_attempt_is_refused(self):
        factory = client.CanonicalClientFactory()
        argv = [str(WINDOWS_PROVIDER.build / WINDOWS_PROVIDER.executable_name)]
        with patch.object(subprocess, 'Popen', side_effect=ProcessPort) as spawn:
            for _ in range(4):
                returned = factory.process_factory(argv, **sdk_pipes())
                self.assertIsInstance(returned, ProcessPort)
            with self.assertRaisesRegex(ValueError, 'canonical launch budget exhausted'):
                factory.process_factory(argv, **sdk_pipes())
            self.assertEqual(spawn.call_count, 4)
            self.assertEqual(spawn.call_args.args, (argv,))
            self.assertEqual(spawn.call_args.kwargs, sdk_pipes())

    def test_caller_process_options_and_commands_refuse_before_external_io(self):
        factory = client.CanonicalClientFactory()
        argv = [str(WINDOWS_PROVIDER.build / WINDOWS_PROVIDER.executable_name)]
        cases = [(argv + ['--caller'], sdk_pipes()), (argv, dict(sdk_pipes(), shell=True)),
            (argv, dict(sdk_pipes(), preexec_fn=lambda: None)), (argv, dict(sdk_pipes(), text=True)),
            (argv, dict(sdk_pipes(), env={})), (argv, dict(sdk_pipes(), stdin=subprocess.DEVNULL))]
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('unexpected launch')) as spawn:
            for command, options in cases:
                with self.subTest(command=command, options=set(options)), self.assertRaises(ValueError):
                    factory.process_factory(command, **options)
            spawn.assert_not_called()

    def linux_ports(self, stack):
        # Authored external OS/filesystem metadata only; no Linux utility runs.
        stack.enter_context(patch.object(client, 'selected_provider', return_value=LINUX_ARM64_PROVIDER))
        stack.enter_context(patch.object(client.os, 'sched_getaffinity', return_value={3, 1}, create=True))
        stack.enter_context(patch.object(client.os, 'getpriority', return_value=0, create=True))
        stack.enter_context(patch.object(client.os, 'PRIO_PROCESS', 0, create=True))
        stack.enter_context(patch.object(client.os, 'getpgrp', return_value=100, create=True))
        stack.enter_context(patch.object(client.signal, 'SIGKILL', 9, create=True))
        stack.enter_context(patch.object(client, '_client_originals', return_value=(LINUX_ARM64_PROVIDER, {})))

    def test_linux_fixed_exec_chain_uses_native_session_options_without_preexec(self):
        with ExitStack() as stack:
            self.linux_ports(stack)
            # The already retained target metadata is authored at this I/O port;
            # actual utility hashing/execution remains the coordinator's gate.
            checked = stack.enter_context(patch.object(client, '_verify_linux_tools'))
            spawn = stack.enter_context(patch.object(subprocess, 'Popen', side_effect=ProcessPort))
            factory = client.CanonicalClientFactory()
            executable = str(LINUX_ARM64_PROVIDER.build / LINUX_ARM64_PROVIDER.executable_name)
            process = factory.process_factory([executable], **sdk_pipes())
            self.assertEqual(process.pid, 54321)
            checked.assert_called_once_with()
            self.assertEqual(spawn.call_args.args, ([
                '/usr/bin/prlimit', '--cpu=30:30', '--as=268435456:268435456',
                '--fsize=16777216:16777216', '--nofile=128:128', '--',
                '/usr/bin/nice', '-n', '10', '/usr/bin/taskset', '--cpu-list', '1',
                '/usr/bin/setpriv', '--no-new-privs', '--', executable],))
            options = spawn.call_args.kwargs
            self.assertNotIn('preexec_fn', options)
            self.assertEqual(options['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
            self.assertTrue(options['start_new_session'])
            self.assertTrue(options['close_fds'])
            self.assertEqual(options['umask'], 0o077)
            self.assertEqual(factory.binding()['launch_policy']['limits']['cpu_affinity'], [1])
            self.assertEqual(factory.binding()['launch_policy']['limits']['address_space_bytes'], 268435456)

    def test_changed_linux_tool_or_lost_affinity_refuses_before_spawn(self):
        with ExitStack() as stack:
            self.linux_ports(stack)
            spawn = stack.enter_context(patch.object(subprocess, 'Popen', side_effect=AssertionError('unexpected launch')))
            factory = client.CanonicalClientFactory()
            argv = [str(LINUX_ARM64_PROVIDER.build / LINUX_ARM64_PROVIDER.executable_name)]
            with patch.object(client, '_read', return_value=b'x' * 23200), \
                    patch.object(client.Path, 'resolve', lambda filename: filename), \
                    patch.object(client.os, 'access', return_value=True):
                with self.assertRaisesRegex(ValueError, 'launch utility changed'):
                    factory.process_factory(argv, **sdk_pipes())
            with patch.object(client, '_verify_linux_tools'), patch.object(client.os, 'sched_getaffinity', return_value={3}):
                with self.assertRaisesRegex(ValueError, 'CPU or priority unavailable'):
                    factory.process_factory(argv, **sdk_pipes())
            spawn.assert_not_called()

    def test_group_wait_refuses_surviving_descendant_and_kill_targets_owned_group(self):
        with ExitStack() as stack:
            self.linux_ports(stack)
            stack.enter_context(patch.object(client, '_verify_linux_tools'))
            stack.enter_context(patch.object(subprocess, 'Popen', side_effect=ProcessPort))
            factory = client.CanonicalClientFactory()
            process = factory.process_factory([str(LINUX_ARM64_PROVIDER.build / LINUX_ARM64_PROVIDER.executable_name)],
                                              **sdk_pipes())
            group_alive = [True]
            signals = []
            def group(pid, signum):
                self.assertEqual(pid, 54321)
                if not group_alive[0]:
                    raise ProcessLookupError()
                if signum:
                    signals.append(signum)
                    if signum == signal.SIGKILL:
                        group_alive[0] = False
            stack.enter_context(patch.object(client.os, 'killpg', side_effect=group, create=True))
            with self.assertRaises(subprocess.TimeoutExpired):
                process.wait(timeout=0)
            process.terminate()
            process.kill()
            self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
            self.assertEqual(process.wait(timeout=.1), 0)


if __name__ == '__main__':
    unittest.main()
