"""Owner-local replay and blind annotations in the native rectified-left image."""

import base64
import hashlib
import json
import math
import os
import re
import struct
import sys
import zlib
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from body import HEIGHT, WIDTH, MIN_TORSO_SEEDS, head_exclusion
from body_head import head_evidence
from body_rgb import RgbBodyInput, TORSO
from contracts import DEPTH_SCALE_RANGE
from perceive import DARK_LUMA, FRESH_S, Tools
from register_now import METHOD_FILE, den_register
from sofa_check import archive, detect
from source import Bundle


ANNOTATION_SCHEMA = "den-sofa-mask-annotations-1"
POSE_CUES = ("book", "turn-left", "turn-right", "recline", "reach", "stand", "walk")
EMPTY_CUE = "empty"   # the room with nobody in it: annotated empty, never a mask
CAMERA_TRIAL_VERSION = "camera-relative-trial"
CAMERA_TRIAL_TRANSFORM = "den-oak@" + CAMERA_TRIAL_VERSION


def frame_key(value):
    if (type(value) is not list or len(value) != 3 or not isinstance(value[0], str)
            or not value[0] or any(type(part) is not int or not 0 <= part < 2**53 for part in value[1:])):
        raise ValueError("invalid frame key")
    return tuple(value)


def source_frames(session, rows, registration_tools):
    seen = set()
    session = Path(session).resolve()
    skew_limit = registration_tools.method.load(METHOD_FILE)["max_left_depth_skew_s"]
    for row in rows:
        key, name = frame_key(row["key"]), row["file"]
        if key in seen or not re.fullmatch(r"frames/[0-9]+(?:-[0-9]+)?\.npz", name):
            raise ValueError("duplicate frame or invalid frame file")
        seen.add(key)
        file = session / name
        if not file.resolve().is_relative_to(session):
            raise ValueError("frame file is outside the session")
        data = file.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if row.get("sha256", digest) != digest:
            raise ValueError("frame file does not match the session")
        try:
            scene = registration_tools.scene.read_bundle(data)
            registration_tools.scene.frame_depth_skew(scene, skew_limit)
        except registration_tools.scene.Refused as exc:
            raise ValueError("unusable rectified-left annotation frame (%s)" % exc.reason) from None
        meta = scene["meta"]
        if key != (meta["session"], meta["rgb_seq"], scene["depth_frame"]["sequence"]):
            raise ValueError("bundle does not match the session frame key")
        yield row["cue"], key, data, digest, scene


def gray_png(pixels):
    """Encode the camera's native single-channel 640x400 plane without a model venv."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    scanlines = b"".join(b"\0" + pixels[start:start + WIDTH] for start in range(0, len(pixels), WIDTH))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))


def annotation_html(session, rows, *, tools_root):
    """Return a static local annotator with no model outputs or network calls."""
    registration_tools = den_register(archive(tools_root, "register"))
    frames = [dict(cue=cue, key=key, sha256=digest, png=base64.b64encode(gray_png(scene["left"])).decode("ascii"))
              for cue, key, _, digest, scene in source_frames(session, rows, registration_tools)]
    if not frames:
        raise ValueError("no mask frames to annotate")
    payload = json.dumps(frames, separators=(",", ":")).replace("<", "\\u003c")
    return ANNOTATOR.replace("__SCHEMA__", ANNOTATION_SCHEMA).replace("__FRAMES__", payload)


@dataclass(frozen=True)
class PreparedFrame:
    cue: str
    key: tuple
    sha256: str
    pose: dict | None
    status: str


def pose_for_mask(bundle, meta, depth, detector, tools, camera, settings, provider):
    """Apply the runtime image/depth checks, then use the pinned per-frame stereo lift."""
    camera.check_frame(meta.get("intrinsics"))
    if not provider._depth_qualified(bundle, meta, depth):
        return None, "unusable_depth"
    np = detector.np
    _, nv12 = detector.check.read_bundle(bundle.data, with_rgb=True)
    plane = np.frombuffer(nv12, np.uint8).reshape(detect.HEIGHT * 3 // 2, detect.WIDTH)
    if float(plane[:detect.HEIGHT].mean()) < DARK_LUMA:
        return None, "unusable_rgb"
    rgb = detector.decode(plane, detect.WIDTH, detect.HEIGHT)
    result, _, compute_s = detector.run(np.ascontiguousarray(rgb[:, :, ::-1]), "rgb-%d" % bundle.rgb_seq)
    row = detector.row_of(result, capture_ts=bundle.t_host, available_ts=bundle.t_host + compute_s,
                          rgb_seq=bundle.rgb_seq, width=detect.WIDTH, height=detect.HEIGHT,
                          boxes_version="yolo26s-8.4.0", keypoints_version=detector.pose.manifest["model_version"])
    observations = tools.observe.observe(row, meta, depth, camera, settings, offset_s=0)
    people = [obs for obs in observations if obs["kind"] in ("keypoints", "person_box")]
    if len(people) != 1:
        return None, "multiple_people" if people else "no_detection"
    pose = people[0]
    fresh = pose["freshness"]
    if (pose["kind"] != "keypoints" or pose["validity"] != "valid" or pose["visibility"] != "visible"
            or fresh["status"] != "fresh" or fresh["depth"] != "used"):
        return None, "unsupported_pose"
    stereo_torso = {joint["j"] for joint in pose["joints_room"]
                    if joint["status"] == "supported" and joint["lift"] == "stereo"} & TORSO
    if len(stereo_torso) < MIN_TORSO_SEEDS:
        return None, "unsupported_pose"
    return pose, "supported_pose"


def camera_relative_trial(roommodel, registration_tools, calibration_path, scene, camera_id):
    """Use the pinned camera rig with the rectified-left frame as the coordinate origin."""
    try:
        geometry = registration_tools.rig.read_calibration(calibration_path)
        intrinsics = registration_tools.rig.entry_intrinsics(scene, geometry)
    except registration_tools.scene.Refused as exc:
        raise ValueError("camera-relative geometry is unusable (%s)" % exc.reason) from None
    # Observe's interface calls the output coordinates "room". For this trial
    # only, they stay in the depth camera's frame, where body_head projects.
    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    entry = {"camera_id": camera_id, "version": CAMERA_TRIAL_VERSION, "intrinsics": intrinsics,
             "error_bound_m": 0.0, "pose": {"camera_frame": "rectified_left", "room_from_camera": identity}}
    transform = f"{camera_id}@{CAMERA_TRIAL_VERSION}"
    return roommodel.Camera({"cameras": [entry]}, transform, max_error_bound_m=0.0)


def prepare_session(session, rows, *, tools_root, profile, room_model=None, registration=None,
                    camera_id="den-oak", models=None):
    """Load the production detector and geometry once in the owner's detector venv."""
    session, tools_root = Path(session).resolve(), Path(tools_root).resolve()
    models = Path(models or Path(sys.prefix) / "models").resolve()
    observe, track, register = (archive(tools_root, key) for key in ("observe", "track", "register"))
    registration_tools = den_register(register)
    tools = Tools(observe, track)
    method = registration_tools.method.load(METHOD_FILE)
    sources = source_frames(session, rows, registration_tools)
    try:
        if room_model is None:
            first = next(sources, None)
            if first is None:
                raise ValueError("no camera-relative mask frames to score")
            camera = camera_relative_trial(tools.roommodel, registration_tools,
                                           session / "camera-calibration.json", first[-1], camera_id)
            sources = chain((first,), sources)
        else:
            room = tools.roommodel.load(room_model)
            transform_id = tools.roommodel.current_transform_id(room, camera_id)
            fit = registration["fit"]
            if fit["transform_id"] != transform_id or fit["result"] != "registered":
                raise ValueError("registration receipt does not match the current room registration")
            camera = tools.roommodel.Camera(room, transform_id, max_error_bound_m=method["gate_m"])
    except tools.roommodel.RegistrationRefused as exc:
        raise ValueError("camera geometry is unusable") from exc
    if camera.transform_id != profile.transform_id:
        raise ValueError("mask profile does not match the session's camera geometry")
    settings = tools.observe.Settings(tools.observe_method.load(observe / "tools" / "den-observe" / "METHOD.md"))
    settings.max_age_s = FRESH_S
    provider = RgbBodyInput(profile, camera)
    # Step 2's depth scale, before any lift, as perceive.py applies it live. A camera-relative trial has none.
    scale = 1.0 if registration is None else registration.get("depth_scale", 1.0)
    if type(scale) not in (int, float) or not DEPTH_SCALE_RANGE[0] <= scale <= DEPTH_SCALE_RANGE[1]:
        raise ValueError("the registration's depth scale is out of range")
    table = None if scale == 1.0 else array("H", [0] + [min(65535, max(1, round(v * scale))) for v in range(1, 65536)])

    detect.guard()
    pins = detect.lock()
    if detect.unlocked(pins["versions"], detect.installed()):
        raise ValueError("detector venv does not match requirements.lock")
    frames = []
    original_cwd = Path.cwd()
    try:
        detect.contain(session / "detector-state")
        detector = detect.Detector(observe, models, pins["weights"])
        for cue, key, data, digest, scene in sources:
            if room_model is None:
                try:
                    rgb_geometry = registration_tools.scene.rgb_geometry(scene)
                except registration_tools.scene.Refused as exc:
                    raise ValueError("camera-relative RGB geometry is unusable (%s)" % exc.reason) from None
                if (rgb_geometry["rgb_distortion"] != camera.rig.rgb_d
                        or tuple(rgb_geometry["rgb_size"]) != camera.rig.rgb_size):
                    raise ValueError("camera-relative RGB geometry changed during capture")
            meta, depth = tools.takes.read_frame(data)
            if table is not None:
                depth = tools.takes.Depth(depth.width, depth.height, array("H", map(table.__getitem__, depth.mm)),
                                          depth.t_host, depth.sequence)
            bundle = Bundle(data, meta["session"], meta["rgb_seq"], meta["t_host"], 0, 0)
            try:
                pose, status = pose_for_mask(bundle, meta, depth, detector, tools, camera, settings, provider)
            except tools.roommodel.RegistrationRefused as exc:
                raise ValueError("camera bundle does not match the room registration") from exc
            frames.append(PreparedFrame(cue, key, digest, pose, status))
    except (detect.Refused, tools.takes.BundleError) as exc:
        raise ValueError("detector or saved bundle could not be read") from exc
    finally:
        # detect.contain switches the working directory for the native model.
        # The CLI still has to read the owner's relative annotation path.
        os.chdir(original_cwd)
    return frames, camera


def bounds(value, width, height):
    if type(value) is not list or len(value) != 4 or not all(type(part) is int for part in value):
        raise ValueError("annotation bounds must be four integer pixels")
    left, top, right, bottom = value
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("annotation bounds leave the native image")
    return value


def truth_by_frame(frames, annotations, width, height):
    if (not isinstance(annotations, dict) or annotations.get("schema") != ANNOTATION_SCHEMA
            or type(annotations.get("frames")) is not list):
        raise ValueError("mask annotations have the wrong format")
    expected = {frame.key: frame.sha256 for frame in frames}
    truth = {}
    for annotation in annotations["frames"]:
        key = frame_key(annotation["key"])
        if key in truth or key not in expected or expected[key] != annotation.get("sha256"):
            raise ValueError("duplicate annotation or annotation does not match the captured frame")
        status = annotation.get("status")
        if status == "visible":
            regions = [bounds(annotation.get(name), width, height) for name in ("head_bounds_px", "neck_bounds_px")]
        elif status in ("unobservable", "empty"):
            if annotation.get("head_bounds_px") is not None or annotation.get("neck_bounds_px") is not None:
                raise ValueError("withheld annotation cannot include regions")
            regions = []
        else:
            raise ValueError("annotation status is missing or unknown")
        truth[key] = (status, regions)
    if truth.keys() != expected.keys():
        raise ValueError("complete annotations for every sampled frame")
    return truth


def contains(outer, inner):
    return (outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3])


def score(frames, annotations, camera, *, profile, separations=None):
    """Score the body lane's actual exclusion. Report counts only, by cue."""
    truth = truth_by_frame(frames, annotations, *camera.rig.depth_size)
    counts = defaultdict(Counter)
    timing_missing = 0
    binding = {"transform_id": camera.transform_id}
    for frame in frames:
        tally = counts[frame.cue]
        timing = None if separations is None else separations.get(frame.key)
        if type(timing) not in (float, int) or not 0 <= timing < math.inf:
            timing_missing += 1
            timing = profile.max_separation_s
        key = list(frame.key)
        evidence = head_evidence(frame.pose, camera, frame_key=key, binding=binding,
                                 profile=profile, separation_s=timing) if frame.pose is not None else None
        mask = head_exclusion(evidence, frame_key=key, binding=binding)
        status, regions = truth[frame.key]
        if status != "visible":
            tally["unsafe_mask" if mask else f"{status}_withheld"] += 1
        elif mask is None:
            tally["visible_withheld"] += 1
            tally[frame.status if frame.pose is None else "head_evidence_withheld"] += 1
        else:
            covers_head, covers_neck = (contains(mask, region) for region in regions)
            tally["covered" if covers_head and covers_neck else "undercovered"] += 1
            if not covers_head:
                tally["head_undercovered"] += 1
            if not covers_neck:
                tally["upper_neck_undercovered"] += 1
    unsafe = any(tally["unsafe_mask"] or tally["undercovered"] for tally in counts.values())
    covered = all(counts.get(cue, {}).get("covered", 0) for cue in POSE_CUES)
    verdict = "pass" if covered else "incomplete"
    if unsafe:
        verdict = "fail"
    return {"verdict": verdict, "basis": "candidate_separation" if timing_missing else "measured_separation",
            "timing_unavailable_frames": timing_missing, "sampled_frames": len(frames),
            "counts_by_cue": {cue: dict(tally) for cue, tally in counts.items()}}


ANNOTATOR = """<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Den sofa mask annotations</title>
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'">
<style>
body {max-width: 800px; margin: 24px auto; padding: 0 12px; font: 16px system-ui}
canvas {display: block; width: 640px; max-width: 100%; border: 1px solid #444; touch-action: none; cursor: crosshair}
button {margin: 0 6px 8px 0; padding: 6px 10px} p {max-width: 70ch}
</style>
<h1>Den sofa mask annotations</h1>
<p>On each rectified-left image, drag the smallest rectangle around the complete head including hair, ears and chin.
Drag a second rectangle around the upper neck from chin to shoulder line. Include the full visible width of both.
Choose “Cannot bound” if either region is cropped, hidden or uncertain, or another person overlaps the subject.
Choose “Empty frame” only when nobody is visible. Predictions are hidden.</p>
<h2 id="frame"></h2><p id="prompt"></p>
<canvas id="image" width="640" height="400"></canvas>
<p><button id="reset">Redraw</button><button id="unknown">Cannot bound</button><button id="empty">Empty frame</button></p>
<p><button id="previous">Previous</button><button id="next">Next</button><button id="save">Download annotations</button></p>
<p id="progress" role="status"></p>
<script>
const frames = __FRAMES__;
const edits = frames.map(() => ({}));
const el = id => document.getElementById(id);
const canvas = el('image');
const context = canvas.getContext('2d');
let index = 0, image, start = null, current = null;
const labels = {head_bounds_px: 'Draw the complete head.', neck_bounds_px: 'Draw the full upper neck.'};
const part = () => edits[index].head_bounds_px ? 'neck_bounds_px' : 'head_bounds_px';
function draw() {
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (image.naturalWidth) context.drawImage(image, 0, 0);
  for (const [rect, color] of [[edits[index].head_bounds_px, '#ffe000'], [edits[index].neck_bounds_px, '#00dfff'], [current, '#fff']]) {
    if (!rect) continue;
    const [x0, y0, x1, y1] = rect;
    context.strokeStyle = color;
    context.lineWidth = 1;
    context.strokeRect(x0, y0, x1 - x0, y1 - y0);
  }
}
function refresh() {
  const item = edits[index];
  el('prompt').textContent = item.status ? {visible: 'Both regions marked.', unobservable: 'Cannot bound.', empty: 'Empty frame.'}[item.status] : labels[part()];
  const done = edits.filter(edit => edit.status).length;
  el('progress').textContent = `${done} of ${frames.length} frames marked.`;
  el('save').disabled = done !== frames.length;
  el('previous').disabled = index === 0;
  el('next').disabled = index === frames.length - 1;
  draw();
}
function show(next) {
  index = next;
  start = current = null;
  el('frame').textContent = `Frame ${index + 1} of ${frames.length} · ${frames[index].cue}`;
  image = new Image();
  image.onload = draw;
  image.src = 'data:image/png;base64,' + frames[index].png;
  refresh();
}
function point(event) {
  const bounds = canvas.getBoundingClientRect();
  return [Math.max(0, Math.min(639, Math.floor((event.clientX - bounds.left - canvas.clientLeft) * 640 / canvas.clientWidth))),
          Math.max(0, Math.min(399, Math.floor((event.clientY - bounds.top - canvas.clientTop) * 400 / canvas.clientHeight)))];
}
function box(a, b) {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]) + 1, Math.max(a[1], b[1]) + 1];
}
canvas.onpointerdown = event => {
  if (event.button !== 0 || edits[index].status || !image.naturalWidth) return;
  canvas.setPointerCapture(event.pointerId);
  start = point(event);
};
canvas.onpointermove = event => {
  if (start) { current = box(start, point(event)); draw(); }
};
canvas.onpointerup = event => {
  if (!start) return;
  const rect = box(start, point(event));
  start = current = null;
  if (rect[2] - rect[0] > 1 && rect[3] - rect[1] > 1) {
    edits[index][part()] = rect;
    if (edits[index].neck_bounds_px) edits[index].status = 'visible';
  }
  refresh();
};
canvas.onpointercancel = () => { start = current = null; draw(); };
function set(status) {
  edits[index] = status ? {status} : {};
  start = current = null;
  refresh();
}
el('reset').onclick = () => set(null);
el('unknown').onclick = () => set('unobservable');
el('empty').onclick = () => set('empty');
el('previous').onclick = () => show(index - 1);
el('next').onclick = () => show(index + 1);
el('save').onclick = () => {
  const data = {schema: '__SCHEMA__', frames: frames.map(({key, sha256}, i) => ({key, sha256, ...edits[i]}))};
  const blob = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
  const download = document.createElement('a');
  download.href = blob;
  download.download = 'mask-annotations.json';
  download.click();
  setTimeout(() => URL.revokeObjectURL(blob), 1000);
};
show(0);
</script></html>
"""
