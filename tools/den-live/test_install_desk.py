"""The desk switch in the installer (plan 05, for step D-1): install_la.py --desk, install.sh and the unit's drop-in.

No private data and no remote: install_la runs against a fake transport, write_config on scratch folders, and
install.sh's own desk blocks under bash.
"""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install_la as install
import run_service as rs
import run_local as rl
import test_service
from body_head import HeadProfile

SITE = "synthetic-den"
INSTALL_SH = test_service.INSTALL_SH
PROFILE = {"label": "light-1-measured-for-tests", "fictional": False,
           "axes": [{"v_max": 30.0, "a_max": 100.0, "j_max": 400.0}, {"v_max": 30.0, "a_max": 100.0, "j_max": 400.0}],
           "range": {"pan": [-175.0, 175.0], "tilt": [-85.0, 85.0]}, "lit_motion": True, "dim_motion": True,
           "retarget_mid_move": False, "path_margin_cdeg": 50, "settle_s": 0.2, "beam_fwhm_deg": 20.0, "lumens": 450.0}
DEVICE = "fake-device-light-1"


def desk_doc(**changes):
    return {"schema": "den-c12-desk-1", "fixture_id": "test-fixture", "device": DEVICE, "light": False,
            "profile": PROFILE, **changes}


class InstallLaDesk(unittest.TestCase):
    def packet(self, *, desk=None, geometry=True, ha=True, bom=False):
        """install_la.run on a fake transport: (exit code, the files it copied, its lines, the release builder)."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        kw = {}
        if geometry:
            kw["preview_geometry"] = root / "export.json"
            kw["preview_geometry"].write_text(json.dumps({
                "schema": "lighting-brain-geometry-1", "home": SITE, "room": "den", "physical_commands": False,
                "fixtures": [{"fixture_id": "test-fixture", "aim_origin_m": [0.1234, 0, 1]}]}), encoding="utf-8")
        if desk is not None:
            kw["desk_file"] = root / "desk.json"
            kw["desk_file"].write_bytes((b"\xef\xbb\xbf" if bom else b"") + json.dumps(desk).encode("utf-8"))
        names = ("token.txt", "model.jsonl", "receipt.json", "tarball")
        token, model, receipt, tarball = (root / name for name in names)
        for path in (token, model, tarball):
            path.write_bytes(b"synthetic")
        receipt.write_text(json.dumps({"fit": {"result": "registered", "transform_id": "test-transform"},
                                       "scan_revision": "test-scan", "source_correction": {
                                           "calibrations": ["a" * 64], "factors": [1.0], "frames": 1, "applied": 1}}),
                           encoding="utf-8")
        profile = {"qualification_id": "fixture-profile", "clock_evidence_id": "fixture-clock",
                   "transform_id": "test-transform", "scan_revision": "test-scan", "min_valid_depth_fraction": 0.2,
                   "head": asdict(HeadProfile(0.13, 0.055, 0.35, 1.0, 0.006, 0.5, 0.085))}
        (root / install.BODY_PROFILE_FILE).write_text(json.dumps(profile), encoding="utf-8")
        if ha:
            (root / install.HA_TOKEN_FILE).write_text("fake-ha-token\n", encoding="utf-8")
            (root / install.HA_URL_FILE).write_text("https://192.0.2.9:8123\n", encoding="utf-8")
        transport = SimpleNamespace(shell=lambda *args, **kwargs: 0, copied={})

        def copy(paths, remote):
            transport.copied = {path.name: path.read_bytes() for path in paths}
            return 0
        transport.copy = copy
        bundle = {"tarball": tarball, "sha256": "a" * 64, "contents": {install.INSTALL_SH.as_posix(): b"stub"}}
        said = []
        with (patch.object(install.sc, "archive", return_value=root) as archive,
              patch.object(install.perceive, "Tools"),
              patch.object(install, "registration", return_value=(model, receipt, "test-transform")),
              patch.object(install.rl, "den_take", return_value=SimpleNamespace(read_token=lambda _: "fake")),
              patch.object(install.rl, "den_site", return_value=SITE),
              patch.object(install.release, "build", return_value=bundle) as build,
              patch.object(install.release, "report"),
              patch.object(install, "say", said.append)):
            try:
                code = install.run(root=root, host="fake-host", camera_url="http://192.0.2.8:8090", token_file=token,
                                   transport=transport, **kw)
            except install.Stop as exc:
                code = str(exc)
        return code, transport.copied, said, archive, build

    def test_the_desk_goes_as_its_own_credential_and_says_how_to_turn_it_off(self):
        code, copied, said, _archive, _build = self.packet(desk=desk_doc(), bom=True)
        self.assertEqual(code, 0)
        settings = json.loads(copied["settings.json"])
        self.assertIs(settings["desk"], True)
        self.assertEqual(json.loads(copied[rs.DESK_FILE].decode("utf-8-sig"))["device"], DEVICE)
        self.assertNotIn(DEVICE.encode(), copied["settings.json"])
        self.assertIn("The desk: test-fixture's commands go to its board through Home Assistant, dark "
                      "(light false: the spot stays at level 0).", said)
        self.assertIn("To turn the desk off: paste install_la.py again without --desk.", said)
        self.assertFalse(any(DEVICE in line for line in said), "the device reference is never printed")
        held = test_service.scratch("den-live-desk-creds-")   # what the service reads back, byte-order mark and all
        (held / rs.DESK_FILE).write_bytes(copied[rs.DESK_FILE])
        self.assertEqual(rs.desk.load(held, {"test-fixture": {}})["light"], False)

    def test_without_desk_nothing_is_brought_and_it_says_so(self):
        code, copied, said, _archive, _build = self.packet()
        self.assertEqual(code, 0)
        self.assertNotIn(rs.DESK_FILE, copied)
        self.assertNotIn("desk", json.loads(copied["settings.json"]))
        self.assertIn("The desk: off. den-live stays virtual, and LA's old desk file, if any, goes.", said)

    def test_a_desk_is_refused_before_anything_is_built_or_copied(self):
        for kwargs, words, built in (({"geometry": False}, "needs --preview-geometry", "archive"),
                                     ({"desk": desk_doc(fixture_id="F9")}, "doesn't register", "archive"),
                                     ({"desk": desk_doc(profile=dict(PROFILE, fictional=True))}, "measured profile",
                                      "archive"),
                                     ({"ha": False}, "go through Home Assistant", "build")):
            with self.subTest(words=words):
                code, copied, _said, archive, build = self.packet(**{"desk": desk_doc(), **kwargs})
                self.assertIn(words, code)
                self.assertEqual(copied, {})
                (archive if built == "archive" else build).assert_not_called()


class WriteConfigDesk(unittest.TestCase):
    def write(self, **settings):
        base = test_service.scratch("den-live-desk-install-")
        packs = test_service.make_packs([[0, 0], [1, 0], [1, 1]])
        path = base / "settings.json"
        path.write_text(json.dumps({"schema": rs.SETTINGS_SCHEMA, "camera_url": "http://192.0.2.5:8090",
                                    "site": rl.den_site(), "content_packs": str(packs), **settings}), encoding="utf-8")
        rs.write_config(settings_path=path, gateway_pid=0, venv_python="/v", models="/m", out=base / "c.json",
                        dropin=base / "d.conf")
        return (base / "d.conf").read_text(encoding="utf-8")

    def test_the_drop_in_loads_desk_json_only_with_its_preview_and_home_assistant(self):
        line = "LoadCredential=desk.json:/etc/den-live/private/desk.json"
        asks = {"ha_url": "https://192.0.2.20:8123", "ha_entity": rs.FRIGATE_ENTITY}
        preview = {"preview": True, "transform_id": "test-transform", "hue_overlay": False}
        self.assertIn(line, self.write(asks=asks, coordinator=preview, desk=True))
        self.assertNotIn(line, self.write(asks=asks, coordinator=preview))
        for settings in ({"coordinator": preview, "desk": True}, {"asks": asks, "desk": True}):
            with self.subTest(settings=list(settings)), self.assertRaisesRegex(rs.Refusal, "needs this install's"):
                self.write(**settings)


class InstallShDesk(unittest.TestCase):
    def setUp(self):
        if not shutil.which("bash"):
            self.skipTest("no bash")
        self.text = INSTALL_SH.read_text(encoding="utf-8")

    def block(self, start, end):
        return self.text.split(start, 1)[1].split(end, 1)[0]

    def run_block(self, block, folder, extra=""):
        python = Path(sys.executable).as_posix()
        tail = 'here="%s"\npython="%s"\n%s%s\nsay "passed"\n' % (folder.as_posix(), python, extra, block)
        part = folder / "part.sh"
        part.write_text(self.text.split("# ---- 1. what was copied", 1)[0] + tail, encoding="utf-8", newline="\n")
        return subprocess.run([shutil.which("bash"), str(part), "0" * 64, "some-host"], capture_output=True, text=True)

    def test_the_desk_file_and_its_settings_come_together(self):
        check = "# The desk (plan 05, step D-1)" + self.block("# The desk (plan 05, step D-1)", 'work="$(mktemp -d)"')
        cases = (({"desk": True}, ("desk.json", "virtual-fixtures.json", "ha-token.txt"), "passed"),
                 ({"desk": True}, ("virtual-fixtures.json", "ha-token.txt"), "desk.json isn't beside install.sh"),
                 ({"desk": True}, ("desk.json", "ha-token.txt"), "needs the preview's geometry"),
                 ({}, ("desk.json",), "settings.json doesn't map the desk"),
                 ({}, (), "passed"))
        for settings, files, words in cases:
            with self.subTest(settings=settings, files=files):
                folder = test_service.scratch("den-live-sh-desk-")
                (folder / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
                for name in files:
                    (folder / name).write_text("x", encoding="utf-8")
                done = self.run_block(check, folder)
                self.assertIn(words, done.stdout, done.stdout + done.stderr)
                if words != "passed":
                    self.assertEqual(done.returncode, 2)

    def test_an_install_without_the_desk_removes_the_old_one(self):
        step = "# The desk: brought this time, or none." + self.block("# The desk: brought this time, or none.",
                                                                     "\n# ---- 7.")
        folder = test_service.scratch("den-live-sh-desk-")
        etc = folder / "etc"
        (etc / "private").mkdir(parents=True)
        (etc / "private" / "desk.json").write_text("old", encoding="utf-8")
        done = self.run_block(step, folder, 'etc="%s"\n' % etc.as_posix())
        self.assertIn("passed", done.stdout, done.stdout + done.stderr)
        self.assertFalse((etc / "private" / "desk.json").exists())


if __name__ == "__main__":
    unittest.main()
