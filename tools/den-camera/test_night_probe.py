"""night_probe.py against a fake depthai: the takes it keeps, the settings it sends, and how it ends.

    python -I -B tools/den-camera/test_night_probe.py
"""
import io
import json
import sys
import tempfile
import types
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import night_probe  # noqa: E402

RELEASE = HERE.parent / "fixtures" / "installed-oak-capture-20261114.py"


class Frame:
    def __init__(self, name, seq):
        self.name, self.seq = name, seq
        shape = {"rgb": (540, 640), "depth": (400, 640)}.get(name, (400, 640))
        self.array = np.full(shape, 1000 if name == "depth" else 50, np.uint16 if name == "depth" else np.uint8)

    def getFrame(self):
        return self.array

    def getSequenceNum(self):
        return self.seq

    def getTimestampDevice(self):
        return timedelta(seconds=10 + self.seq / 15)

    def getTimestamp(self):
        return timedelta(seconds=20 + self.seq / 15)

    def getWidth(self):
        return 640

    def getHeight(self):
        return 360 if self.name == "rgb" else 400

    def getType(self):
        return "NV12" if self.name == "rgb" else "RAW8"

    def getExposureTime(self):
        return timedelta(milliseconds=30)

    def getSensitivity(self):
        return 1600

    def getTransformation(self):
        return types.SimpleNamespace(isValid=lambda: False)


class Queue:
    def __init__(self):
        self.seq, self.sent = 0, []

    def tryGet(self):
        self.seq += 1
        return {name: Frame(name, self.seq) for name in ("rgb", "depth", "left", "right")}

    def send(self, message):
        self.sent.append(message)


class Anything:
    """Every node, output and config the pipeline builder touches."""

    def __init__(self, fake):
        self.fake = fake
        self.inputs = mock.MagicMock()
        self.initialConfig = mock.MagicMock()

    def build(self, *a, **k):
        return self

    def requestOutput(self, *a, **k):
        return mock.MagicMock()

    @property
    def inputControl(self):
        return types.SimpleNamespace(createInputQueue=lambda: self.fake.controls)

    @property
    def out(self):
        return types.SimpleNamespace(createOutputQueue=lambda **k: self.fake.groups)

    def __getattr__(self, name):
        return mock.MagicMock()


class Control:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, a))


def fake_depthai():
    fake = types.SimpleNamespace(groups=Queue(), controls=Queue(), drivers=[])

    class Device:
        def readCalibration(self):
            return types.SimpleNamespace(eepromToJson=lambda: {"batchName": "fake"})

        def setIrLaserDotProjectorIntensity(self, value):
            fake.drivers.append(("dots", value))
            return True

        def setIrFloodLightIntensity(self, value):
            fake.drivers.append(("flood", value))
            return True

    class Pipeline:
        AutoCalibrationMode = types.SimpleNamespace(OFF="off")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def setAutoCalibrationMode(self, mode):
            pass

        def getDefaultDevice(self):
            return Device()

        def create(self, node):
            return Anything(fake)

        def start(self):
            pass

    module = types.SimpleNamespace(
        __version__="3.10.0", Pipeline=Pipeline, CameraControl=Control,
        node=types.SimpleNamespace(Camera="camera", StereoDepth=types.SimpleNamespace(PresetMode=mock.MagicMock()),
                                   Sync="sync"),
        CameraBoardSocket=mock.MagicMock(), ImgFrame=mock.MagicMock(), ImgResizeMode=mock.MagicMock())
    return module, fake


class NightProbeTest(unittest.TestCase):
    def run_probe(self, lights="low,off,reading,on"):
        module, fake = fake_depthai()
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp) / "pilot-test"
            release.mkdir()
            (release / "capture.py").write_bytes(RELEASE.read_bytes())
            out = Path(tmp) / "takes"
            asked, said = [], []
            args = types.SimpleNamespace(release=str(release), out=str(out), lights=lights, fps=30)
            with mock.patch.dict(sys.modules, {"depthai": module}), \
                    mock.patch.object(night_probe, "TAKE_EVERY_S", 0.0), mock.patch.object(night_probe, "SETTLE_S", 0.0):
                code = night_probe.run(args, ask=asked.append, say=said.append)
            files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.npz"))
            bundles = {}
            for name in files:
                with np.load(out / name, allow_pickle=False) as z:
                    bundles[name] = ({k: z[k] for k in z.files if k != "metadata_json"},
                                     json.loads(bytes(z["metadata_json"]).decode()))
            calibration = json.loads((out / "calibration.json").read_text(encoding="utf-8"))
        return code, fake, asked, said, files, bundles, calibration

    def test_every_setting_keeps_its_frames_as_the_release_builds_them(self):
        code, fake, asked, said, files, bundles, calibration = self.run_probe()
        self.assertEqual(code, 0)
        self.assertEqual(len(asked), 4)   # one Enter per lighting state
        per_lighting = {"low": 8, "off": 8, "reading": 3, "on": 8}
        self.assertEqual(len(files), sum(per_lighting.values()) * night_probe.TAKE_N)
        arrays, meta = bundles["off/flood-100/001.npz"]
        self.assertEqual(sorted(arrays), ["depth_mm", "left", "rgb_nv12", "right"])
        self.assertEqual(arrays["depth_mm"].dtype, np.uint16)
        self.assertEqual(meta["schema"], 1)
        self.assertEqual(meta["probe"]["setting"], "flood-100")
        self.assertEqual((meta["probe"]["dots"], meta["probe"]["flood"]), (0.0, 1.0))
        self.assertEqual(meta["frames"]["left"]["sensitivity_iso"], 1600)
        self.assertIn("synchronization", meta)
        # The fake calibration isn't the measured one, so the release's rule leaves the depth as it came
        self.assertFalse(meta["depth_correction"]["applied"])
        self.assertEqual(int(arrays["depth_mm"][0, 0]), 1000)
        self.assertEqual(calibration["eeprom"], {"batchName": "fake"})
        self.assertEqual(sorted({name.split("/")[1] for name in files if name.startswith("reading/")}),
                         sorted(night_probe.READING))

    def test_it_ends_with_both_ir_drivers_off_and_rgb_on_auto(self):
        _code, fake, *_ = self.run_probe("off")
        self.assertEqual(fake.drivers[-2:], [("dots", 0.0), ("flood", 0.0)])
        self.assertIn(("setAutoExposureEnable", ()), fake.controls.sent[-1].calls)
        manual = [m.calls for m in fake.controls.sent if any(c[0] == "setManualExposure" for c in m.calls)]
        self.assertEqual([c for calls in manual for c in calls if c[0] == "setLumaDenoise"],
                         [("setLumaDenoise", (1,)), ("setLumaDenoise", (4,))])

    def test_main_refuses_while_oak_den_runs_and_unknown_lights(self):
        with mock.patch.object(night_probe, "service_active", return_value=True), \
                mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(night_probe.main(["--release", "r", "--out", "o"]), 2)
        self.assertIn("oak-den is running", err.getvalue())
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            night_probe.main(["--release", "r", "--out", "o", "--lights", "low,dusk"])


if __name__ == "__main__":
    unittest.main()
