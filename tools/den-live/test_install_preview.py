"""Exercise the owner preview install packet with a fake transport and no private data."""

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install_la as install
from body_head import HeadProfile

SITE = "synthetic-den"


class PreviewInstallTest(unittest.TestCase):
    def test_preview_geometry_is_only_sent_as_a_separate_credential(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            geometry = root / "export.json"
            geometry.write_text(json.dumps({
                "schema": "lighting-brain-geometry-1", "home": SITE, "room": "den",
                "physical_commands": False,
                "fixtures": [{"fixture_id": "test-fixture", "aim_origin_m": [0.1234, 0, 1]}],
            }), encoding="utf-8")
            token = root / "token.txt"
            model = root / "model.jsonl"
            receipt = root / "receipt.json"
            tarball = root / "tarball"
            for path in (token, model, receipt, tarball):
                path.write_bytes(b"synthetic")
            receipt.write_text(json.dumps({"fit": {"result": "registered", "transform_id": "test-transform"},
                                           "scan_revision": "test-scan", "source_correction": {
                                               "calibrations": ["a" * 64], "factors": [1.0], "frames": 1, "applied": 1,
                                           }}), encoding="utf-8")
            profile = {"qualification_id": "fixture-profile", "clock_evidence_id": "fixture-clock",
                       "transform_id": "test-transform", "scan_revision": "test-scan", "min_valid_depth_fraction": 0.2,
                       "head": asdict(HeadProfile(0.13, 0.055, 0.35, 1.0, 0.006, 0.5, 0.085))}
            (root / install.BODY_PROFILE_FILE).write_text(json.dumps(profile), encoding="utf-8")
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
                patch.object(install.rl, "den_site", return_value=SITE),
                patch.object(install.release, "build", return_value=bundle),
                patch.object(install.release, "report"),
                patch.object(install, "say"),
            ):
                self.assertEqual(install.run(root=root, host="fake-host", camera_url="http://192.0.2.8:8090",
                                             token_file=token, transport=transport, preview_geometry=geometry), 0)
            settings = json.loads(transport.copied["settings.json"])
            self.assertEqual(settings["coordinator"],
                             {"preview": True, "transform_id": "test-transform", "hue_overlay": False})
            self.assertEqual(settings["body_rgb"], profile)
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
                "schema": "lighting-brain-geometry-1", "home": SITE, "room": "den",
                "physical_commands": True, "fixtures": [{"fixture_id": "test-fixture"}],
            }), encoding="utf-8")
            with (patch.object(install.rl, "den_site", return_value=SITE),
                  patch.object(install.sc, "archive") as archive, self.assertRaises(install.Stop)):
                install.run(root=root, host="fake-host", camera_url="http://192.0.2.8:8090",
                            token_file=root / "fake-token.txt", transport=None, preview_geometry=geometry)
            archive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
