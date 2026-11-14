#!/usr/bin/env python3
"""Night probe: the den OAK's IR flood, dot projector and RGB exposure, one setting at a time, kept as private takes.

It needs the camera to itself, so it runs only while oak-den is stopped, as the service account, with the camera's
venv. The owner pastes one line that stops oak-den, runs this, and starts oak-den again however it ends:

    ssh -t <account>@<camera host> "sudo bash ~/victoria-camera-pilot/night-probe-run.sh <release>"

It loads the release's own capture.py by path and builds that release's pipeline (the same cameras, sizes, rates,
stereo settings and sync), and keeps every bundle as the release builds /frame.npz (encode_bundle: the depth
corrected exactly as served, left, right, rgb_nv12 and the metadata), plus a probe record naming the setting and each
plane's sensor sensitivity. For each lighting state it asks the owner to set (and waits for Enter), it steps through
SETTINGS: it sets the IR drivers and the RGB control, waits SETTLE_S for the auto exposure, then keeps TAKE_N synced
groups about TAKE_EVERY_S apart. It prints counts and one line per setting, never a picture. The IR drivers end at 0
and the RGB back on auto, as the release leaves them.
"""
import argparse
import importlib.util
import json
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

PROBE_SCHEMA = "den-night-probe-1"
SETTLE_S, TAKE_N, TAKE_EVERY_S = 1.5, 10, 0.3
# (name, dot projector 0-1, flood 0-1, RGB control or None for auto). The projector is 0-1200 mA, the flood 0-1500 mA.
SETTINGS = (
    ("ir-off", 0.0, 0.0, None),
    ("flood-50", 0.0, 0.5, None),
    ("flood-100", 0.0, 1.0, None),
    ("dots-50", 0.5, 0.0, None),
    ("dots-100", 1.0, 0.0, None),
    ("dots-50-flood-100", 0.5, 1.0, None),
    ("rgb-33ms-iso1600", 0.0, 0.0, {"exposure_us": 33000, "iso": 1600, "luma_denoise": 1}),
    ("rgb-33ms-iso1600-dn4", 0.0, 0.0, {"exposure_us": 33000, "iso": 1600, "luma_denoise": 4}),
)
READING = ("ir-off", "flood-100", "dots-50-flood-100")
LIGHTS = {
    "low": "Leave the den's lights as they are now.",
    "off": "Turn every light in the den off (a screen can stay as it is). Stay where you are.",
    "reading": "Lights still off: hold a book open in front of you, as if reading, until the next prompt.",
    "on": "Put the book down, and turn the den's lights on as for reading.",
}


def load_release(release):
    """The release's capture.py as a module: its encode_bundle, frame_metadata and depth correction."""
    path = Path(release) / "capture.py"
    spec = importlib.util.spec_from_file_location("release_capture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan(lights):
    """[(lighting, [setting, ...])] in the order the owner is asked."""
    out = []
    for light in lights:
        names = READING if light == "reading" else tuple(s[0] for s in SETTINGS)
        out.append((light, [s for s in SETTINGS if s[0] in names]))
    return out


def rgb_control(dai, control):
    message = dai.CameraControl()
    if control is None:
        message.setAutoExposureEnable()
        message.setLumaDenoise(1)
    else:
        message.setManualExposure(control["exposure_us"], control["iso"])
        message.setLumaDenoise(control["luma_denoise"])
    return message


def sensitivity(frame):
    try:
        return int(frame.getSensitivity())
    except Exception:
        return None


def probe_record(lighting, setting, n, applied):
    name, dots, flood, control = setting
    return {"schema": PROBE_SCHEMA, "lighting": lighting, "setting": name, "dots": dots, "flood": flood,
            "rgb_control": control or "auto", "n": n, "drivers_applied": applied}


def service_active():
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", "oak-den"]).returncode == 0
    except OSError:
        return False


def build(dai, pipeline, fps):
    """The release's pipeline (capture_session), without the RTSP publisher and the causal script: (synced groups,
    RGB control). As in the release, the 1080p RGB output feeds an on-device H.264 encoder, so the sensor and the ISP
    run as they do under oak-den; here its bitstream is dropped. depthai refuses to start with a requested camera
    output that nothing links or queues, so every one is consumed."""
    rgb_cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A, sensorResolution=(1920, 1080),
                                                     sensorFps=fps)
    rgb_video = rgb_cam.requestOutput((1920, 1080), type=dai.ImgFrame.Type.NV12, resizeMode=dai.ImgResizeMode.CROP,
                                      fps=fps)
    rgb_preview = rgb_cam.requestOutput((640, 360), type=dai.ImgFrame.Type.NV12, resizeMode=dai.ImgResizeMode.CROP,
                                        fps=fps)
    depth_fps = min(15, fps)
    left = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B, sensorFps=depth_fps)
    right = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C, sensorFps=depth_fps)
    left_out = left.requestOutput((640, 400), type=dai.ImgFrame.Type.GRAY8, fps=depth_fps)
    right_out = right.requestOutput((640, 400), type=dai.ImgFrame.Type.GRAY8, fps=depth_fps)
    stereo = pipeline.create(dai.node.StereoDepth)
    stereo.build(left_out, right_out, dai.node.StereoDepth.PresetMode.DEFAULT)
    stereo.setDepthAlign(dai.CameraBoardSocket.CAM_B)
    stereo.setLeftRightCheck(True)
    stereo.initialConfig.postProcessing.temporalFilter.enable = False
    stereo.initialConfig.postProcessing.decimationFilter.decimationFactor = 1
    sync = pipeline.create(dai.node.Sync)
    sync.setSyncThreshold(timedelta(milliseconds=20))
    sync.setSyncAttempts(-1)
    for name, output in [("rgb", rgb_preview), ("depth", stereo.depth),
                         ("left", stereo.rectifiedLeft), ("right", stereo.rectifiedRight)]:
        sync.inputs[name].setBlocking(False)
        sync.inputs[name].setMaxSize(2)
        output.link(sync.inputs[name])
    encoder = pipeline.create(dai.node.VideoEncoder)
    encoder.setDefaultProfilePreset(fps, dai.VideoEncoderProperties.Profile.H264_MAIN)
    encoder.setBitrateKbps(4000)
    encoder.setKeyframeFrequency(fps)
    encoder.setNumBFrames(0)
    rgb_video.link(encoder.input)
    encoder.bitstream.createOutputQueue(maxSize=4, blocking=False)   # dropped: nothing reads it
    return sync.out.createOutputQueue(maxSize=1, blocking=False), rgb_cam.inputControl.createInputQueue()


def run(args, ask=input, say=print):
    import depthai as dai
    capture = load_release(args.release)
    out = Path(args.out)
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    steps = plan(args.lights.split(","))
    session = "night-probe-%d" % time.time()
    with dai.Pipeline() as pipeline:
        pipeline.setAutoCalibrationMode(dai.Pipeline.AutoCalibrationMode.OFF)
        device = pipeline.getDefaultDevice()
        calibration = device.readCalibration()
        eeprom = calibration.eepromToJson()
        correction = capture.depth_correction_for(eeprom)
        (out / "calibration.json").write_text(json.dumps({"eeprom": eeprom, "depth_correction": correction,
                                                          "depthai": dai.__version__}), encoding="utf-8")
        groups, controls = build(dai, pipeline, args.fps)
        pipeline.start()
        try:
            for lighting, settings in steps:
                say("\n" + LIGHTS.get(lighting, lighting))
                ask("Press Enter when ready: ")
                for setting in settings:
                    name, dots, flood, control = setting
                    applied = [bool(device.setIrLaserDotProjectorIntensity(dots)),
                               bool(device.setIrFloodLightIntensity(flood))]
                    controls.send(rgb_control(dai, control))
                    settle = time.monotonic() + SETTLE_S
                    while time.monotonic() < settle:
                        groups.tryGet()
                        time.sleep(0.01)
                    folder = out / lighting / name
                    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                    kept, last, deadline = 0, 0.0, time.monotonic() + TAKE_N * TAKE_EVERY_S * 3 + 2.0
                    while kept < TAKE_N and time.monotonic() < deadline:
                        group = groups.tryGet()
                        now = time.monotonic()
                        if group is None or now - last < TAKE_EVERY_S:
                            time.sleep(0.005)
                            continue
                        frames = {k: group[k] for k in ("rgb", "depth", "left", "right")}
                        metadata = {"schema": 1, "session": session, "captured_unix_ns": time.time_ns(),
                                    "captured_unix_ns_semantics": "bundle_packaging_time_not_sensor_exposure",
                                    "depth_alignment": "rectified_left", "depth_units": "millimetres", "invalid_depth": 0,
                                    "depth_correction": correction,
                                    "frames": {k: capture.frame_metadata(f) for k, f in frames.items()}}
                        metadata["synchronization"] = capture.synchronization_metadata(metadata["frames"])
                        for k, f in frames.items():
                            metadata["frames"][k]["sensitivity_iso"] = sensitivity(f)
                        kept += 1
                        metadata["probe"] = probe_record(lighting, setting, kept, applied)
                        arrays = {"depth_mm": capture.correct_depth_mm(frames["depth"].getFrame(), correction),
                                  "left": frames["left"].getFrame(), "right": frames["right"].getFrame(),
                                  "rgb_nv12": frames["rgb"].getFrame()}
                        (folder / ("%03d.npz" % kept)).write_bytes(capture.encode_bundle(arrays, metadata))
                        last = now
                    say("  %-22s %2d frames" % (name, kept))
        finally:
            device.setIrLaserDotProjectorIntensity(0.0)
            device.setIrFloodLightIntensity(0.0)
            controls.send(rgb_control(dai, None))
    say("\nDone. oak-den starts again now.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", help="the running release's folder, whose capture.py it loads")
    parser.add_argument("--out", help="a private folder for the takes")
    parser.add_argument("--lights", default="low,off,reading,on", help="lighting states, in the order asked")
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args(argv)
    if not args.release or not args.out:
        parser.error("--release and --out are required")
    unknown = [light for light in args.lights.split(",") if light not in LIGHTS]
    if unknown:
        parser.error("unknown lighting state(s): %s" % ", ".join(unknown))
    if service_active():
        print("oak-den is running: it owns the camera. Stop it first (the paste line does).", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
