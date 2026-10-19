"""Windows file fixtures; mocked protection calls do NOT prove Linux permissions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from home_shared import vllm_launch_config as loader
from home_shared.vllm_startup import StartupConfig
from test_admission import manifest_fixture


class LaunchConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "launch.json"
        self.value = dict(version=1, manifest_file="manifest.json", key_file="signer.key",
                          ledger_file="engine.db", engine_epoch="epoch-1",
                          rpc_timeout_seconds=5, max_database_bytes=64 * 1024 * 1024)
        self.write()
        (self.root / "manifest.json").write_text(json.dumps(manifest_fixture()), encoding="utf-8")
        self.key = b"\x00\xff" * 16
        (self.root / "signer.key").write_bytes(self.key)
        (self.root / "engine.db").write_bytes(b"preexisting ledger left untouched")
        self.calls = []

        def protected(path, *, secret, enforce_linux):
            self.calls.append((Path(path), secret, enforce_linux))
            path = Path(path).absolute()
            if not path.is_file():
                raise ValueError("fixture requires existing regular file")
            return path
        mock = patch.object(loader, "protected_file", side_effect=protected)
        mock.start()
        self.addCleanup(mock.stop)

    def write(self):
        self.path.write_text(json.dumps(self.value), encoding="utf-8")

    def test_relative_descriptor_rejected_before_file_access(self):
        with self.assertRaises(loader.LaunchConfigError):
            loader.load_launch_config('launch.json')
        self.assertFalse(self.calls)

    def test_load_checks_every_protected_file_and_does_not_mutate(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        config = loader.load_launch_config(self.path)
        self.assertIsInstance(config, StartupConfig)
        self.assertEqual(config.signer_key, self.key)
        self.assertEqual(config.engine_epoch, "epoch-1")
        self.assertEqual(config.ledger_path, str(self.root / "engine.db"))
        self.assertEqual(config.rpc_timeout_seconds, 5)
        self.assertEqual(self.calls, [(self.path, False, True), (self.root / "manifest.json", False, True),
                                     (self.root / "signer.key", True, True), (self.root / "engine.db", True, True)])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.assertNotIn(repr(self.key), repr(config))

    def test_no_creation_for_missing_inputs(self):
        for name in ("engine.db", "signer.key", "manifest.json", "launch.json"):
            path = self.root / name
            saved = path.read_bytes()
            path.unlink()
            with self.assertRaises(loader.LaunchConfigError):
                loader.load_launch_config(self.path)
            self.assertFalse(path.exists())
            path.write_bytes(saved)

    def test_path_traversal_urls_absolute_and_inline_keys_rejected(self):
        for name in ("../key", "sub/key", "sub\\key", "/root/key", "C:\\key", "https://host/key",
                     ".", "..", "", " key", "key\n", "x" * 129, 42):
            self.value["key_file"] = name
            self.write()
            with self.subTest(name=name), self.assertRaises(loader.LaunchConfigError):
                loader.load_launch_config(self.path)
        self.value["key_file"] = "signer.key"
        self.value["signer_key"] = "do-not-echo-secret"
        self.write()
        with self.assertRaises(loader.LaunchConfigError) as caught:
            loader.load_launch_config(self.path)
        self.assertNotIn("do-not-echo-secret", str(caught.exception))

    def test_exact_schema_and_nonaliased_files(self):
        original = dict(self.value)
        for change in ({"version": True}, {"version": 2}, {"extra": "x"},
                       {"key_file": "manifest.json"}, {"ledger_file": "launch.json"}):
            self.value = {**original, **change}
            self.write()
            with self.assertRaises(loader.LaunchConfigError):
                loader.load_launch_config(self.path)
        self.value = original
        del self.value["engine_epoch"]
        self.write()
        with self.assertRaises(loader.LaunchConfigError):
            loader.load_launch_config(self.path)

    def test_bounded_key_is_raw_not_trimmed_or_decoded(self):
        for size in (0, 31, 4097):
            (self.root / "signer.key").write_bytes(b"k" * size)
            with self.assertRaises(loader.LaunchConfigError):
                loader.load_launch_config(self.path)
        for key in (b"\n" * 32, b"k" * 4096):
            (self.root / "signer.key").write_bytes(key)
            self.assertEqual(loader.load_launch_config(self.path).signer_key, key)

    def test_bounded_descriptor_manifest_and_strict_json(self):
        for name, limit in (("launch.json", loader.MAX_DESCRIPTOR_BYTES),
                            ("manifest.json", loader.MAX_MANIFEST_BYTES)):
            path = self.root / name
            original = path.read_bytes()
            for raw in (b" " * (limit + 1), b"", b'{} trailing', b"\xff", b'{"x":1,"x":2}',
                        b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}',
                        b'[' * 2000 + b'0' + b']' * 2000):
                path.write_bytes(raw)
                with self.subTest(name=name, size=len(raw)), self.assertRaises(loader.LaunchConfigError):
                    loader.load_launch_config(self.path)
            path.write_bytes(original)

    def test_protection_rejection_is_fatal(self):
        with patch.object(loader, "protected_file", side_effect=ValueError("unprotected")) as check:
            with self.assertRaises(loader.LaunchConfigError):
                loader.load_launch_config(self.path)
        check.assert_called_once_with(self.path, secret=False, enforce_linux=True)

    def test_startup_numeric_and_epoch_validation_preserved(self):
        original = dict(self.value)
        for change in ({"rpc_timeout_seconds": 0}, {"rpc_timeout_seconds": 16},
                       {"rpc_timeout_seconds": True}, {"rpc_timeout_seconds": 1e999},
                       {"max_database_bytes": 65535}, {"max_database_bytes": 2**30 + 1},
                       {"max_database_bytes": True}, {"engine_epoch": "../bad epoch"}):
            self.value = {**original, **change}
            self.write()
            with self.subTest(change=change), self.assertRaises(loader.LaunchConfigError):
                loader.load_launch_config(self.path)


if __name__ == "__main__":
    unittest.main()
