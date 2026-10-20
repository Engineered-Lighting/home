"""Portable validation plus mocked descriptor contracts; native tests need Linux.

Windows passing mocks is not acceptance of real POSIX filesystem behavior.
No SSH, repository source capture, device or production host access occurs here.
"""
import base64
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from home_shared import preservation as p


def record(path='src/main.py', data=b"print('safe')\r\n"):
    return {'path': path, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'content_base64': base64.b64encode(data).decode(),
            'source_stat': {'device': 1, 'inode': 2, 'mode': 0o755, 'mtime_ns': 20, 'ctime_ns': 30}}


def snapshot(*records):
    return {'version': 1, 'kind': 'explicit-source-capture', 'files': list(records or (record(),)),
            'content_policy': p.CONTENT_POLICY, 'stability': 'observed-two-pass',
            'complete_deployed_baseline': False, 'deployment_authorized': False}


def source_stat(**changes):
    values = dict(st_dev=1, st_ino=2, st_mode=stat.S_IFREG | 0o644,
                  st_size=4, st_mtime_ns=20, st_ctime_ns=30, st_nlink=1)
    values.update(changes)
    return SimpleNamespace(**values)


@contextmanager
def root_descriptor(_):
    yield 1


class PolicyTests(unittest.TestCase):
    def test_paths_reject_traversal_aliases_windows_names_and_runtime_data(self):
        for value in ('../main.py', '/main.py', 'src//main.py', 'src/./main.py', 'src/../main.py',
                      'C:/main.py', r'src\main.py', 'main.py:secret', 'NUL.py', 'CON .py', 'src/COM1.js',
                      'src /main.py', 'src./main.py', '.git/file.py', 'app/data/memory.py',
                      'config.yaml', 'key.pem', 'data.sqlite', 'image.jpg', 'video.mp4',
                      'src\n/main.py', 'src/\x00main.py', 'src/maín.py', {}, None):
            with self.subTest(path=value), self.assertRaises(p.PreservationError):
                p.source_path(value)
        for value in ('AGENTS.md', 'src/main.py', 'docs/Release Notes.md'):
            self.assertEqual(str(p.source_path(value)), value)

    def test_explicit_bounded_unique_list_and_case_collisions(self):
        for value in ([], 'main.py', ['a.py', 'a.py'], ['A.py', 'a.py'], [{}], ['a.py'] * 257):
            with self.subTest(value=value), self.assertRaises(p.PreservationError):
                p.snapshot_sources('/repo', value)

    def test_fixed_sensitive_policy_never_echoes_matches(self):
        examples = (b'PASSWORD="private-value"', b'{"api_key": "private-value"}',
                    b"const accessToken = 'private-value';", b'Authorization: Bearer private-value',
                    b'-----BEGIN OPENSSH PRIVATE KEY-----', b'rtsp://user:private-value@camera/',
                    b'export CLIENT_SECRET=private-value', b'ghp_123456789012345678901234',
                    b'\x00binary', b'\xff')
        for value in examples:
            with self.subTest(value=value), self.assertRaises(p.PreservationError) as error:
                p.validate_source_content(value)
            self.assertNotIn('private-value', str(error.exception))
        for value in (b'', b'API_KEY = os.environ["API_KEY"]', b'const token = response.token;',
                      b"print('safe')\r\n", b'password = ""'):
            p.validate_source_content(value)

    def test_reason_categories_never_forward_exception_content(self):
        for error, expected in ((p.PreservationError('sensitive_content'), 'sensitive_content'),
                                (FileNotFoundError('private detail'), 'missing_source'),
                                (PermissionError('private detail'), 'unreadable_source'),
                                (OSError(errno.ELOOP, 'private detail'), 'symlink_or_non_directory'),
                                (OSError('private detail'), 'capture_unavailable')):
            self.assertEqual(p.rejection_reason(error), expected)

    def test_capture_has_no_windows_pathname_fallback(self):
        with patch.object(p.sys, 'platform', 'win32'), self.assertRaises(p.PreservationError) as error:
            p.snapshot_sources('/repo', ['main.py'])
        self.assertEqual(error.exception.code, 'unsupported_capture_platform')


class CaptureContractTests(unittest.TestCase):
    """Mocks validate flags/state transitions, not the native OS implementation."""
    def test_absolute_root_components_are_also_descriptor_relative_nofollow(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(p, '_require_linux'))
            for key, value in (('O_NOFOLLOW', 0x10000), ('O_DIRECTORY', 0x20000), ('O_CLOEXEC', 0x40000)):
                stack.enter_context(patch.object(p.os, key, value, create=True))
            opened = stack.enter_context(patch.object(p.os, 'open', side_effect=[10, 11, 12]))
            closed = stack.enter_context(patch.object(p.os, 'close'))
            with p._root_descriptor('/home/source') as descriptor:
                self.assertEqual(descriptor, 12)
            self.assertEqual([call.args[0] for call in opened.call_args_list], ['/', 'home', 'source'])
            self.assertTrue(all(call.args[1] & p.os.O_NOFOLLOW for call in opened.call_args_list))
            self.assertEqual(opened.call_args_list[1].kwargs, {'dir_fd': 10})
            self.assertEqual(opened.call_args_list[2].kwargs, {'dir_fd': 11})
            self.assertEqual([call.args[0] for call in closed.call_args_list], [10, 11, 12])

    def descriptors(self, before=None, after=None, named=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for key, value in (('O_NOFOLLOW', 0x10000), ('O_DIRECTORY', 0x20000),
                           ('O_CLOEXEC', 0x40000), ('O_NONBLOCK', 0x80000)):
            stack.enter_context(patch.object(p.os, key, value, create=True))
        stack.enter_context(patch.object(p.os, 'dup', return_value=10))
        opened = stack.enter_context(patch.object(p.os, 'open', side_effect=[11, 12]))
        closed = stack.enter_context(patch.object(p.os, 'close'))
        read = stack.enter_context(patch.object(p.os, 'read', side_effect=[b'safe', b'']))
        stack.enter_context(patch.object(p.os, 'fstat', side_effect=[before or source_stat(), after or source_stat()]))
        named_stat = stack.enter_context(patch.object(p.os, 'stat', return_value=named or source_stat()))
        return opened, closed, read, named_stat

    def test_every_component_nofollow_and_final_open_nonblocking(self):
        opened, closed, _, named = self.descriptors()
        data, metadata = p._read(1, 'src/main.py')
        self.assertEqual(data, b'safe')
        self.assertEqual(metadata['mode'], 0o644)
        directory, final = opened.call_args_list
        self.assertTrue(directory.args[1] & p.os.O_DIRECTORY)
        self.assertTrue(directory.args[1] & p.os.O_NOFOLLOW)
        self.assertEqual(directory.kwargs, {'dir_fd': 10})
        self.assertTrue(final.args[1] & p.os.O_NOFOLLOW)
        self.assertTrue(final.args[1] & p.os.O_NONBLOCK)
        self.assertEqual(final.kwargs, {'dir_fd': 11})
        self.assertEqual(named.call_args.kwargs, {'dir_fd': 11, 'follow_symlinks': False})
        self.assertEqual([call.args[0] for call in closed.call_args_list], [10, 12, 11])

    def test_fifo_device_directory_and_hardlink_rejected_before_read(self):
        for changes in ({'st_mode': stat.S_IFIFO}, {'st_mode': stat.S_IFBLK},
                        {'st_mode': stat.S_IFDIR}, {'st_nlink': 2}):
            with self.subTest(changes=changes):
                opened, _, read, _ = self.descriptors(before=source_stat(**changes))
                with self.assertRaises(p.PreservationError):
                    p._read(1, 'src/main.py')
                self.assertTrue(opened.call_args.args[1] & p.os.O_NONBLOCK)
                read.assert_not_called()

    def test_parent_and_final_symlink_failure_never_reads(self):
        for failures in ([OSError(errno.ELOOP, 'symlink')], [11, OSError(errno.ELOOP, 'symlink')]):
            with self.subTest(failures=failures):
                opened, _, read, _ = self.descriptors()
                opened.side_effect = failures
                with self.assertRaises(OSError):
                    p._read(1, 'src/main.py')
                read.assert_not_called()

    def test_same_size_mtime_restored_ctime_change_and_inode_swap_fail(self):
        for changes in ({'st_ctime_ns': 31}, {'st_ino': 3}, {'st_mode': stat.S_IFLNK | 0o777}):
            with self.subTest(changes=changes):
                self.descriptors(named=source_stat(**changes))
                with self.assertRaises(p.PreservationError) as error:
                    p._read(1, 'src/main.py')
                self.assertEqual(error.exception.code, 'unstable_source')

    def test_oversized_initial_and_growing_file(self):
        _, _, read, _ = self.descriptors(before=source_stat(st_size=p.MAX_FILE_BYTES + 1))
        with self.assertRaises(p.PreservationError):
            p._read(1, 'src/main.py')
        read.assert_not_called()
        _, _, read, _ = self.descriptors()
        read.side_effect = [b'toolarge']
        with patch.object(p, 'MAX_FILE_BYTES', 4), self.assertRaises(p.PreservationError):
            p._read(1, 'src/main.py')

    def test_two_pass_checks_bytes_and_stat_and_root_identity(self):
        metadata = record()['source_stat']
        for reads in ([(b'first', metadata), (b'other', metadata)],
                      [(b'same', metadata), (b'same', dict(metadata, inode=99))]):
            with self.subTest(reads=reads), patch.object(p, '_root_descriptor', root_descriptor), \
                    patch.object(p.os, 'fstat', return_value=source_stat()), \
                    patch.object(p, '_read', side_effect=reads), self.assertRaises(p.PreservationError):
                p.snapshot_sources('/repo', ['a.py'])
        with patch.object(p, '_root_descriptor', root_descriptor), \
                patch.object(p.os, 'fstat', side_effect=[source_stat(), source_stat(st_ino=9)]), \
                patch.object(p, '_read', return_value=(b'safe', metadata)), self.assertRaises(p.PreservationError) as error:
            p.snapshot_sources('/repo', ['a.py'])
        self.assertEqual(error.exception.code, 'unstable_root')

    def test_two_pass_success_is_explicitly_partial_and_total_bounded(self):
        metadata = record()['source_stat']
        with patch.object(p, '_root_descriptor', root_descriptor), \
                patch.object(p.os, 'fstat', return_value=source_stat()), \
                patch.object(p, '_read', return_value=(b'safe', metadata)) as reader:
            value = p.snapshot_sources('/repo', ['b.py', 'a.py'])
            self.assertEqual(reader.call_count, 4)
            self.assertEqual([item['path'] for item in value['files']], ['a.py', 'b.py'])
            self.assertFalse(value['complete_deployed_baseline'])
            self.assertFalse(value['deployment_authorized'])
            with patch.object(p, 'MAX_TOTAL_BYTES', 7), self.assertRaises(p.PreservationError):
                p.snapshot_sources('/repo', ['a.py', 'b.py'])


class SerializerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name) / 'capture'

    def test_exact_bytes_inert_names_deduplication_and_manifest(self):
        value = snapshot(record('AGENTS.md'), record('src/main.py'))
        manifest = p.save_snapshot(value, self.destination)
        self.assertFalse((self.destination / 'AGENTS.md').exists())
        self.assertFalse((self.destination / 'src').exists())
        self.assertEqual(len(list((self.destination / 'blobs').iterdir())), 1)
        blob = self.destination / manifest['files'][0]['blob']
        self.assertEqual(blob.read_bytes(), b"print('safe')\r\n")
        self.assertEqual(json.loads((self.destination / 'manifest.json').read_bytes()), manifest)
        self.assertTrue(manifest['private_source_review_required'])
        self.assertFalse((self.destination / '.manifest.pending').exists())
        self.assertNotIn('content_base64', (self.destination / 'manifest.json').read_text())

    def test_invalid_schema_checksum_size_base64_metadata_and_collisions_write_nothing(self):
        cases = []
        for changes in ({'version': True}, {'extra': 1}, {'content_policy': 'unchecked'},
                        {'deployment_authorized': True}, {'files': []}):
            cases.append(dict(snapshot(), **changes))
        for changes in ({'sha256': '0' * 64}, {'bytes': True}, {'bytes': 1}, {'extra': 1},
                        {'content_base64': '%%%'}, {'content_base64': 'c2FmZR=='},
                        {'source_stat': {'mode': 1}}, {'path': '../secret.py'}):
            cases.append(snapshot(dict(record(), **changes)))
        cases.append(snapshot(record('A.py'), record('a.py')))
        cases.append(snapshot(record(data=b'PASSWORD="private-value"')))
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.save_snapshot(value, self.destination)
            self.assertFalse(self.destination.exists())

    def test_file_count_and_total_caps_write_nothing(self):
        with patch.object(p, 'MAX_FILES', 1), self.assertRaises(p.PreservationError):
            p.save_snapshot(snapshot(record('a.py'), record('b.py')), self.destination)
        with patch.object(p, 'MAX_TOTAL_BYTES', 2), self.assertRaises(p.PreservationError):
            p.save_snapshot(snapshot(), self.destination)
        self.assertFalse(self.destination.exists())

    def test_existing_destination_never_overwritten(self):
        self.destination.mkdir()
        sentinel = self.destination / 'sentinel'
        sentinel.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            p.save_snapshot(snapshot(), self.destination)
        self.assertEqual(sentinel.read_bytes(), b'original')

    def test_blob_or_manifest_failure_never_publishes_final_manifest(self):
        original = p._write_private
        for fail_name in ('blob', 'manifest'):
            destination = self.destination / fail_name
            def failure(path, data):
                if fail_name == 'blob' or path.name == '.manifest.pending':
                    path.write_bytes(data[:5])
                    raise OSError('synthetic write failure')
                original(path, data)
            with patch.object(p, '_write_private', side_effect=failure), self.assertRaises(OSError):
                p.save_snapshot(snapshot(), destination)
            self.assertFalse((destination / 'manifest.json').exists())
            with self.assertRaises(FileExistsError):
                p.save_snapshot(snapshot(), destination)

    def test_manifest_exclusive_link_failure_does_not_announce_completion(self):
        with patch.object(p.os, 'link', side_effect=OSError('synthetic publish failure')), self.assertRaises(OSError):
            p.save_snapshot(snapshot(), self.destination)
        self.assertFalse((self.destination / 'manifest.json').exists())
        self.assertTrue((self.destination / '.manifest.pending').exists())


@unittest.skipUnless(sys.platform.startswith('linux'), 'native dirfd/symlink/FIFO acceptance requires Linux')
class NativeLinuxTests(unittest.TestCase):
    def test_actual_regular_symlink_fifo_and_parent_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_bytes(b'print(1)\n')
            self.assertEqual(p.snapshot_sources(root, ['main.py'])['files'][0]['bytes'], 9)
            (root / 'link.py').symlink_to(root / 'main.py')
            os.mkfifo(root / 'fifo.py')
            (root / 'alias').symlink_to(root, target_is_directory=True)
            for path in ('link.py', 'fifo.py', 'alias/main.py'):
                with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                    p.snapshot_sources(root, [path])


if __name__ == '__main__':
    unittest.main()
