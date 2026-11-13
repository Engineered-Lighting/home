"""Exercise the owner preview install packet with a fake transport and no private data."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install_la as install


class PreviewInstallTest(unittest.TestCase):
    def test_preview_geometry_is_only_sent_as_a_separate_credential(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            geometry = root / "export.json"
            geometry.write_text(json.dumps({
                "schema": "lighting-brain-geometry-1", "home": "victoria", "room": "den",
                "physical_commands": False,
                "fixtures": [{"fixture_id": "test-fixture", "aim_origin_m": [0.1234, 0, 1]}],
            }), encoding="utf-8")
            token = root / "token.txt"
            model = root / "model.jsonl"
            receipt = root / "receipt.json"
            tarball = root / "tarball"
            for path in (token, model, receipt, tarball):
                path.write_bytes(b"synthetic")
            transport = SimpleNamespace(shell=lambda *args, **kwargs: 0, copied={})

            def copy(paths, remote):
                transport.copied = {path.name: path.read_bytes() for path in paths}
                return 0

            transport.copy = copy
            bundle = {"tarball": tarball, "sha256": "a" * 64, "contents": {install.INSTALL_SH.as_posix(): b"stub"}}
            with (
                patch.object(install.sc, "archive", return_value=root),
                patch.object(install.perceive, "Tools"),
                patch.object(install, "registration", return_value=(model, receipt, "test-transform")),
                patch.object(install.rl, "den_take", return_value=SimpleNamespace(read_token=lambda _: "fake")),
                patch.object(install.rl, "den_site", return_value="synthetic-den"),
                patch.object(install.release, "build", return_value=bundle),
                patch.object(install.release, "report"),
                patch.object(install, "say"),
            ):
                self.assertEqual(install.run(root=root, host="fake-host", camera_url="http://192.0.2.8:8090",
                                             token_file=token, transport=transport, preview_geometry=geometry), 0)
            settings = json.loads(transport.copied["settings.json"])
            self.assertEqual(settings["coordinator"],
                             {"preview": True, "transform_id": "test-transform", "hue_overlay": False})
            self.assertNotIn(b"0.1234", transport.copied["settings.json"])
            self.assertEqual(transport.copied[install.PREVIEW_GEOMETRY], geometry.read_bytes())
            self.assertFalse((root / "private" / "release" / "outbox").exists())
            dropin = install.rs.dropin_text("192.0.2.8", Path("/opt/home-content"), preview_geometry=True)
            self.assertIn("LoadCredential=virtual-fixtures.json:/etc/den-live/private/virtual-fixtures.json", dropin)
            self.assertNotIn("ha-token.txt", dropin)

    def test_physical_geometry_is_refused_before_archiving_or_transfer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            geometry = root / "export.json"
            geometry.write_text(json.dumps({
                "schema": "lighting-brain-geometry-1", "home": "victoria", "room": "den",
                "physical_commands": True, "fixtures": [{"fixture_id": "test-fixture"}],
            }), encoding="utf-8")
            with patch.object(install.sc, "archive") as archive, self.assertRaises(install.Stop):
                install.run(root=root, host="fake-host", camera_url="http://192.0.2.8:8090",
                            token_file=root / "fake-token.txt", transport=None, preview_geometry=geometry)
            archive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
