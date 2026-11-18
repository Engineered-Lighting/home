"""Optional installer packet and read-only Linux provisioning checks. No host work."""
import copy
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import install_la as install


def block(**changes):
    return dict(enabled=True, port=18199, profile_file="/opt/den-models/profiles/intent.json",
                frame_root="/srv/den-model-inputs/frames", dependencies_file="/srv/den-model-inputs/source/source.json",
                duration_s=4, frame_access="private_group", frame_group=1234, **changes)


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_target_linux_paths_validate_on_the_workstation_without_local_existence_checks(self):
        value = block()
        self.assertEqual(install.enrichment_block(value), value)
        for change in ({"frame_root": "C:/private/frames"}, {"profile_file": "relative"},
                       {"frame_root": "/srv/private/../frames"}, {"port": True}, {"frame_group": True},
                       {"frame_root": "/var/lib/den-live/shared"}, {"frame_access": "owner"},
                       {"token_file": "/etc/ha/secret"}, {"duration_s": 2},
                       {"dependencies_file": "/srv/den-model-inputs/frames/source.json"}):
            with self.subTest(change=change), self.assertRaises(install.Stop):
                install.enrichment_block(value | change)

    def test_only_enabled_block_brings_separate_token_and_never_reuses_ha(self):
        settings = self.write("enrichment.json", block())
        token = self.root / "worker.txt"
        token.write_text("b" * 64 + "\n", encoding="utf-8")
        self.assertEqual(install.enrichment_packet(settings, token), (block(), "b" * 64))
        with self.assertRaises(install.Stop): install.enrichment_packet(settings, None)
        with self.assertRaises(install.Stop): install.enrichment_packet(settings, token, other_tokens=(token,))
        with self.assertRaises(install.Stop): install.enrichment_packet(None, token)
        disabled = self.write("disabled.json", {"enabled": False})
        self.assertEqual(install.enrichment_packet(disabled, None), ({"enabled": False}, None))
        with self.assertRaises(install.Stop): install.enrichment_packet(disabled, token)
        for value in ("a" * 63, "A" * 64, "not-a-token", "a" * 64 + "\nextra"):
            token.write_text(value)
            with self.assertRaises(install.Stop): install.model_worker_token(token)

    def test_private_json_errors_are_fixed_and_omit_paths_and_values(self):
        path = self.root / "private-secret-filename.json"
        path.write_text('{"enabled":true,"enabled":"private-secret-value"}')
        with self.assertRaises(install.Stop) as error:
            install._private_json(path)
        self.assertNotIn("private-secret", str(error.exception))

    def test_walking_needs_preview_and_uses_actual_validator(self):
        value = self.write("walking.json", {"mode": "off"})
        with self.assertRaises(install.Stop): install.walking_packet(value, None)
        self.assertEqual(install.walking_packet(value, b"geometry"), {"mode": "off"})
        value.write_text(json.dumps({"mode": "physical"}))
        with self.assertRaises(install.Stop): install.walking_packet(value, b"geometry")

    def test_walking_floor_cannot_rebind_to_another_registration_scan_or_calibration(self):
        receipt = self.write("receipt.json", {"scan_revision": "scan", "source_correction": {"calibrations": ["c" * 64]}})
        geometry = {"binding": {"content_revision": "scan"}}
        floor = dict(transform_id="transform", scan_revision="scan", depth_calibration_sha256="c" * 64)
        install.walking_registration({"mode": "shadow", "floor": floor}, geometry, receipt, "transform")
        for change in ({"transform_id": "other"}, {"scan_revision": "other"}, {"depth_calibration_sha256": "d" * 64}):
            with self.subTest(change=change), self.assertRaises(install.Stop):
                install.walking_registration({"mode": "shadow", "floor": floor | change}, geometry, receipt, "transform")

    def test_invalid_optional_packet_refuses_before_archive_build_or_transfer(self):
        value = self.write("invalid.json", block() | {"port": True})
        with (patch.object(install.rl, "den_site", return_value="synthetic-den"),
              patch.object(install.sc, "archive") as archive, patch.object(install.release, "build") as build,
              self.assertRaises(install.Stop)):
            install.run(root=self.root, host="unused", camera_url="http://192.0.2.8:8090", token_file=self.root / "camera",
                        transport=None, enrichment_settings_file=value)
        archive.assert_not_called()
        build.assert_not_called()

    def test_staged_packet_keeps_worker_credential_private_and_omission_disables(self):
        token, model, receipt, tarball = (self.root / name for name in ("camera", "model", "receipt", "tarball"))
        for path in (token, model, receipt, tarball): path.write_text("synthetic")
        settings = self.write("enrichment.json", block())
        worker = self.root / "worker-token"
        worker.write_text("b" * 64)
        geometry = self.write("geometry.json", dict(schema="lighting-brain-geometry-1", home="synthetic-den", room="den",
                              physical_commands=False, fixtures=[{"fixture_id": "test-fixture"}]))
        walking = self.write("walking.json", {"mode": "off"})
        copied, said = {}, []
        def copy(paths, remote):
            copied.clear()
            copied.update({p.name: p.read_bytes() for p in paths})
            return 0
        transport = NS(shell=lambda *args, **kwargs: 0, copy=copy)
        built = dict(tarball=tarball, sha256="a" * 64, contents={install.INSTALL_SH.as_posix(): b"stub"})
        with (patch.object(install.rl, "den_site", return_value="synthetic-den"),
              patch.object(install.sc, "archive", return_value=self.root), patch.object(install.perceive, "Tools"),
              patch.object(install, "registration", return_value=(model, receipt, "synthetic-transform")),
              patch.object(install.rl, "den_take", return_value=NS(read_token=lambda path: "camera-token")),
              patch.object(install, "ha_inputs", return_value=("ha-token", "https://192.0.2.9:8123")),
              patch.object(install, "body_profile", return_value=None), patch.object(install, "labels_seed", return_value=None),
              patch.object(install.release, "build", return_value=built), patch.object(install.release, "report"),
              patch.object(install, "say", said.append)):
            kwargs = dict(root=self.root, host="fake", camera_url="http://192.0.2.8:8090", token_file=token,
                          transport=transport, preview_geometry=geometry)
            self.assertEqual(install.run(**kwargs, enrichment_settings_file=settings, model_worker_token_file=worker,
                                        walking_config_file=walking), 0)
            packet = json.loads(copied["settings.json"])
            self.assertEqual(packet["enrichment"], block())
            self.assertEqual(packet["coordinator"]["walking"], {"mode": "off"})
            self.assertEqual(copied[install.rs.MODEL_TOKEN], b"b" * 64 + b"\n")
            self.assertNotIn("b" * 64, json.dumps(packet))
            self.assertNotIn("b" * 64, "\n".join(said))
            self.assertNotIn("/srv/den-model-inputs", "\n".join(said))
            self.assertFalse((self.root / "private" / "release" / "outbox").exists())
            self.assertEqual(install.run(**kwargs), 0)
            packet = json.loads(copied["settings.json"])
            self.assertNotIn("enrichment", packet)
            self.assertNotIn("walking", packet["coordinator"])
            self.assertNotIn(install.rs.MODEL_TOKEN, copied)


class TargetProvisioningTests(unittest.TestCase):
    def metadata(self, mode, uid=0, gid=0, directory=False):
        return NS(st_mode=(stat.S_IFDIR if directory else stat.S_IFREG) | mode, st_uid=uid, st_gid=gid)

    def check(self, changes=None, *, group="den-model-inputs", profile=None):
        value = block()
        entries = {
            value["frame_root"]: self.metadata(0o2770, gid=1234, directory=True),
            str(PurePosixPath(value["dependencies_file"]).parent): self.metadata(0o2770, gid=1234, directory=True),
            value["profile_file"]: self.metadata(0o640, gid=1234),
        }
        entries.update(changes or {})
        def inspect(path):
            name = str(path)
            if name == value["dependencies_file"] and name not in entries: raise FileNotFoundError
            metadata = entries.get(name, self.metadata(0o755, directory=True))
            if metadata is None: raise FileNotFoundError
            return metadata
        install.target_enrichment(value, inspect=inspect, group_lookup=lambda gid: NS(gr_name=group),
            profile_reader=lambda path: profile or {"schema": "den.model-profile/v1", "id": "vjepa-lm-qwen3-8b"})

    def test_preprovisioned_root_setgid_dirs_and_readonly_profile_are_accepted(self):
        self.check()
        self.check({block()["dependencies_file"]: self.metadata(0o640, uid=43210, gid=1234)})

    def test_no_permission_repair_or_broad_group_is_attempted(self):
        value = block()
        bad = ({value["frame_root"]: self.metadata(0o2770, uid=1000, gid=1234, directory=True)},
               {value["frame_root"]: self.metadata(0o2777, gid=1234, directory=True)},
               {value["frame_root"]: self.metadata(0o770, gid=1234, directory=True)},
               {value["profile_file"]: self.metadata(0o660, gid=1234)},
               {str(PurePosixPath(value["profile_file"]).parent): self.metadata(0o775, directory=True)},
               {"/srv/den-model-inputs": NS(st_mode=stat.S_IFLNK | 0o777, st_uid=0, st_gid=0)},
               {value["dependencies_file"]: self.metadata(0o664, gid=1234)})
        for change in bad:
            with self.subTest(change=list(change)), self.assertRaises(install.Stop): self.check(change)
        with self.assertRaises(install.Stop): self.check(group="users")
        with self.assertRaises(install.Stop): self.check(profile={"schema": "wrong", "id": "secret-value"})


class ShellCredentialTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux" and shutil.which("bash"), "target shell contracts run on Linux CI")
    def test_token_install_and_omission_remove_only_the_own_credential(self):
        script = (Path(__file__).resolve().parents[2] / "stack/services/den-live/install.sh").read_text()
        selected = '# This is a separate credential from Home Assistant' + script.split(
            '# This is a separate credential from Home Assistant', 1)[1].split('# Your labels from the PC', 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            here, etc = root / "staging", root / "etc"
            here.mkdir()
            (etc / "private").mkdir(parents=True)
            old = etc / "private" / install.rs.MODEL_TOKEN
            old.write_text("old")
            ha = etc / "private" / "ha-token.txt"
            ha.write_text("keep")
            part = root / "part.sh"
            prefix = 'set -eu\nhere="%s"\netc="%s"\nsay() { :; }\n' % (here, etc)
            part.write_text(prefix + selected)
            subprocess.run([shutil.which("bash"), str(part)], check=True, capture_output=True)
            self.assertFalse(old.exists())
            self.assertEqual(ha.read_text(), "keep")
            (here / install.rs.MODEL_TOKEN).write_text("b" * 64)
            # The unit contract checks the privileged ownership arguments, then
            # copies only into the scratch tree without requiring root in CI.
            fake_install = '''install() {
  [[ "$1" == -m && "$2" == 0600 && "$3" == -o && "$4" == root && "$5" == -g && "$6" == root && "$7" == -- ]]
  cp -- "$8" "$9"
  chmod 0600 -- "$9"
}
'''
            part.write_text(prefix + fake_install + selected)
            subprocess.run([shutil.which("bash"), str(part)], check=True, capture_output=True)
            self.assertEqual(old.read_text(), "b" * 64)
            self.assertEqual(stat.S_IMODE(old.stat().st_mode), 0o600)
            self.assertEqual(ha.read_text(), "keep")

    def test_remote_optional_preflight_precedes_model_or_service_work(self):
        script = (Path(__file__).resolve().parents[2] / "stack/services/den-live/install.sh").read_text()
        self.assertLess(script.index("install_la.target_optional_inputs"), script.index("# ---- 3. the models"))
        self.assertLess(script.index("install_la.target_optional_inputs"), script.index("--write-config"))
        self.assertLess(script.index("install_la.target_optional_inputs"), script.index("systemctl restart den-live.service"))


if __name__ == "__main__":
    unittest.main()
