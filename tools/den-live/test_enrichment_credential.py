"""Credential mount contracts, with fake POSIX descriptors; no systemd or models."""
import copy
from pathlib import PurePosixPath
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import enrichment_link as link


class WorkerCredentialTests(unittest.TestCase):
    def setup_fake(self, path='/run/credentials/den-live.service/model-worker-token.txt'):
        info=SimpleNamespace(st_mode=stat.S_IFREG|0o440,st_uid=0,st_gid=0,st_size=65,st_dev=8,st_ino=11)
        directory=SimpleNamespace(st_mode=stat.S_IFDIR|0o550,st_uid=0,st_gid=0,st_size=0,st_dev=8,st_ino=12)
        parent=SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0,st_gid=0,st_size=0,st_dev=8,st_ino=13)
        state={'file':info,'fd_file':info,'named':info,'directory':directory,'readonly':1,'raw':b'a'*64+b'\n',
               'parents':{'/run/credentials':copy.copy(parent),'/run':copy.copy(parent),'/':copy.copy(parent)}}
        class FakePath(PurePosixPath):
            def lstat(self):
                if str(self)==path:return state['file']
                if str(self)=='/run/credentials/den-live.service':return state['directory']
                return state['parents'][str(self)]
        fake=SimpleNamespace(name='posix',O_RDONLY=0,O_NOFOLLOW=1,O_DIRECTORY=2,O_CLOEXEC=4,O_NONBLOCK=8,O_BINARY=0,ST_RDONLY=1,
            environ={'CREDENTIALS_DIRECTORY':'/run/credentials/den-live.service'},getuid=lambda:62295,
            open=Mock(side_effect=lambda p,flags:12 if str(p)=='/run/credentials/den-live.service' else 11),
            fstat=Mock(side_effect=lambda fd:state['directory'] if fd==12 else state['fd_file']),
            stat=Mock(side_effect=lambda *a,**k:state['named']),
            fstatvfs=Mock(side_effect=lambda fd:SimpleNamespace(f_flag=state['readonly'])),
            read=Mock(side_effect=lambda fd,n:state['raw']),close=Mock())
        return state,fake,FakePath

    def read(self,state,fake,path_type,path='/run/credentials/den-live.service/model-worker-token.txt',env=None):
        with patch.object(link,'os',fake),patch.object(link,'Path',path_type):
            return link.worker_token(path,env=env)

    def test_actual_root_acl_credential_on_readonly_mount(self):
        state,fake,path_type=self.setup_fake()
        self.assertEqual(self.read(state,fake,path_type),'a'*64)
        self.assertTrue(all(call.args[1]&fake.O_NOFOLLOW for call in fake.open.call_args_list))
        fake.stat.assert_called_once_with('model-worker-token.txt',dir_fd=12,follow_symlinks=False)
        fake.read.assert_called_once_with(11,257)
        self.assertEqual({call.args[0] for call in fake.fstatvfs.call_args_list},{11,12})
        self.assertEqual({call.args[0] for call in fake.close.call_args_list},{11,12})

    def test_ordinary_private_owner_files_keep_existing_policy(self):
        for mode in (0o400,0o600,0o700):
            path='/private/token'
            state,fake,path_type=self.setup_fake(path)
            state['file'].st_uid=62295;state['file'].st_mode=stat.S_IFREG|mode
            with self.subTest(mode=mode):self.assertEqual(self.read(state,fake,path_type,path,env={}),'a'*64)
            fake.fstatvfs.assert_not_called()

    def test_ordinary_wrong_owner_or_group_world_access_still_refused(self):
        for uid,mode in ((0,0o600),(62295,0o640),(62295,0o604)):
            path='/private/token';state,fake,path_type=self.setup_fake(path)
            state['file'].st_uid=uid;state['file'].st_mode=stat.S_IFREG|mode
            with self.subTest(uid=uid,mode=mode),self.assertRaisesRegex(ValueError,'^private_worker_token_required$'):
                self.read(state,fake,path_type,path,env={})
            fake.read.assert_not_called()

    def test_spoofed_missing_outside_noncanonical_environment_refused(self):
        for folder in (None,'/tmp/credentials/den-live.service','/run/credentials/other.service',
                       '/run/credentials/den-live.service/','/run/credentials/den-live.service/../den-live.service'):
            state,fake,path_type=self.setup_fake()
            env={} if folder is None else {'CREDENTIALS_DIRECTORY':folder}
            with self.subTest(folder=folder),self.assertRaisesRegex(ValueError,'^private_worker_token_required$'):
                self.read(state,fake,path_type,env=env)
            fake.read.assert_not_called()

    def test_credential_filename_is_exact(self):
        path='/run/credentials/den-live.service/another-token.txt';state,fake,path_type=self.setup_fake(path)
        with self.assertRaises(ValueError):self.read(state,fake,path_type,path)

    def test_writable_credential_mount_refused(self):
        state,fake,path_type=self.setup_fake();state['readonly']=0
        with self.assertRaises(ValueError):self.read(state,fake,path_type)
        fake.read.assert_not_called()

    def test_root_credential_requires_exact_uid_gid_and_mode(self):
        for change in ({'st_uid':1},{'st_gid':978},{'st_mode':stat.S_IFREG|0o400},{'st_mode':stat.S_IFREG|0o444}):
            state,fake,path_type=self.setup_fake()
            for key,value in change.items():setattr(state['file'],key,value)
            with self.subTest(change=change),self.assertRaises(ValueError):self.read(state,fake,path_type)
            fake.read.assert_not_called()

    def test_credential_directory_requires_exact_metadata(self):
        for change in ({'st_uid':62295},{'st_gid':978},{'st_mode':stat.S_IFDIR|0o750},
                       {'st_mode':stat.S_IFLNK|0o550},{'st_dev':9}):
            state,fake,path_type=self.setup_fake()
            for key,value in change.items():setattr(state['directory'],key,value)
            with self.subTest(change=change),self.assertRaises(ValueError):self.read(state,fake,path_type)
            fake.read.assert_not_called()

    def test_ancestors_must_be_protected_root_directories(self):
        for change in ({'st_uid':1},{'st_gid':978},{'st_mode':stat.S_IFDIR|0o775},{'st_mode':stat.S_IFLNK|0o755}):
            state,fake,path_type=self.setup_fake()
            for key,value in change.items():setattr(state['parents']['/run'],key,value)
            with self.subTest(change=change),self.assertRaises(ValueError):self.read(state,fake,path_type)
            fake.read.assert_not_called()

    def test_symlink_or_replaced_file_is_refused_before_read(self):
        for variant in ('symlink','replaced','named_symlink'):
            state,fake,path_type=self.setup_fake()
            if variant=='symlink':state['file'].st_mode=stat.S_IFLNK|0o440
            if variant=='replaced':state['fd_file']=copy.copy(state['file']);state['fd_file'].st_ino=99
            if variant=='named_symlink':state['named']=copy.copy(state['file']);state['named'].st_mode=stat.S_IFLNK|0o440
            with self.subTest(variant=variant),self.assertRaises(ValueError):self.read(state,fake,path_type)
            fake.read.assert_not_called()

    def test_size_and_ascii_hex_bounds_apply_to_both_branches(self):
        for raw in (b'a'*257,b'a'*63,b'g'*64,b'\xff'*64,b''):
            state,fake,path_type=self.setup_fake();state['raw']=raw
            with self.subTest(raw_size=len(raw)),self.assertRaisesRegex(ValueError,'^private_worker_token_required$'):
                self.read(state,fake,path_type)

    def test_oversized_fstat_refuses_before_read(self):
        state,fake,path_type=self.setup_fake();state['fd_file']=copy.copy(state['file']);state['fd_file'].st_size=257
        with self.assertRaises(ValueError):self.read(state,fake,path_type)
        fake.read.assert_not_called()


if __name__=='__main__':unittest.main()
