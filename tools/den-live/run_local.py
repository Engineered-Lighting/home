#!/usr/bin/env python3
"""Step 3 of the live den view: your marker, live, on this PC. The owner's paste. Workstation only.

    python -B C:\\claude\\home\\.claude\\worktrees\\den-live\\tools\\den-live\\run_local.py

Then open http://127.0.0.1:18190/ and walk around. Ctrl+C stops it. While it runs, this PC reads the den camera
continuously; your paste is the go-ahead for that.

What runs, all on this PC, nothing installed anywhere:
- source.py: the camera's newest /frame.npz about 5 a second, with den-take's client and token file.
- detect_worker.py, in the detector's venv with no network: YOLO26s and RTMPose-m, as the den simulation runs them.
- perceive.py: den-observe and den-track, unedited at the den simulation's pins, on step 2's registration
  (<data root>\\private\\register\\room-model.jsonl) and your scan's floor, padded 1 m (FLOOR_PAD_M).
- stream.py: the people stream on 127.0.0.1:8111.
- live_page.mjs: Home from the claude/den-live-home worktree, with its people layer, on 127.0.0.1:18190.

The loop itself is serve(), which step 4's LA service (run_service.py) runs too, without the page.

It refuses to start without step 2's qualified registration, the camera token, the detector's venv, or Node. Every 10 s it
prints one line of words and counts: the camera, frames a second, people, how far behind, drops. Never a coordinate.
Nothing is kept: no frames, no tracks. Only the tracker's method copy goes in <data root>\\private\\live\\.

The den's labels (room_labels.py) are kept in <data root>\\private\\labels\\ and served on the stream's labels path, so
the page's Labels tool reads and saves them. With --labels-only it starts just that and the page, on your scan, to label
the den: no camera, detector or registration (the labels plan's step 1).

    python -B C:\\claude\\home\\.claude\\worktrees\\den-labels-live\\tools\\den-live\\run_local.py --labels-only
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import body_link  # noqa: E402  the live body, made in the detector's process and never waited for
import body_rgb  # noqa: E402
import perceive  # noqa: E402
import person_state  # noqa: E402  contract 2's den-person-state-1 log, the LA service's only
import register_now as rn  # noqa: E402  the method file's numbers: the gate, and whether the camera's correction is required
import room_labels  # noqa: E402  the den's labels, kept and served beside the stream
import sofa_check as sc  # noqa: E402  the data root, the pinned archives, the venv, say
import source  # noqa: E402
import stream as people_stream  # noqa: E402
from contracts import DEPTH_SCALE_RANGE, REGISTER_SCHEMA, take_scale  # noqa: E402  contract 1's receipt

say, Stop = sc.say, sc.Stop
ROOT = HERE.parents[1]
LIVE_PAGE = HERE / "live_page.mjs"
# Home with the live view's layers: the claude/den-labels-home worktree beside this one when it holds the Labels tool,
# else this checkout when it holds the body layer too (claude/den-live-body), else the claude/den-live-home worktree.
LABELS_APP = ROOT.parent / "den-labels-home"
APP = (LABELS_APP if (LABELS_APP / "app" / "src" / "home-den-labels-view.mjs").is_file()
       else ROOT if (ROOT / "app" / "src" / "home-3d" / "apartment-body-layer.mjs").is_file() else ROOT.parent / "den-live-home")
PAGE_PORT, STREAM_PORT = 18190, 8111
STATUS_EVERY_S = 10.0
CAMERA_ID = "den-oak"
TREES = ("observe", "track", "register", "coordinator")   # the pinned trees the loop loads: den-observe and den-take,
# den-track, den-register, and the den lighting coordinator's core (den-core and the lighting brain's aim)


class Refusal(Stop):
    """A stop a restart can't mend: an input that's missing or doesn't match, or a registration the camera no longer
    fits. The LA service exits 3 on one, and systemd doesn't restart it (RestartPreventExitStatus)."""


# ---- your scan's floor --------------------------------------------------------------------------------------------

def den_site(root=ROOT):
    """The den's site id, as the gateway's own registration route names it (no site is named in den-live's code)."""
    text = (root / "web-gateway" / "equipment-registration.mjs").read_text(encoding="utf-8")
    match = re.search(r"REGISTRATION_ROUTE = '/api/apartment/v1/sites/([a-z0-9-]+)/", text)
    if not match:
        raise Stop("the gateway's registration route no longer names the den's site")
    return match.group(1)


def scan_floor(packs, site):
    """The active pack's floor.json walkable polygon's box, held to the pack's own manifest digest."""
    import hashlib
    folder = Path(packs) / site
    try:
        revision = (folder / "ACTIVE").read_text(encoding="utf-8").strip()
        manifest = json.loads((folder / revision / "content-pack.json").read_text(encoding="utf-8"))
        asset = manifest["assets"]["floor.json"]
        data = (folder / revision / asset["path"]).read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        raise Stop("your scan's active content pack has no readable floor.json") from None
    if hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise Stop("your scan's floor.json doesn't match its pack's manifest")
    try:
        return perceive.floor_bounds(json.loads(data.decode("utf-8-sig")).get("walkable_polygon")), revision
    except ValueError as exc:
        raise Stop("your scan's floor.json holds no floor: %s" % exc) from None


def content_packs():
    return Path(json.loads((ROOT / "tools" / "den-sim" / "refs.json").read_text(encoding="utf-8"))["content_packs"])


# ---- the run ------------------------------------------------------------------------------------------------------

def page(app, room="real", extra=(), *, page_port=PAGE_PORT, stream_port=STREAM_PORT):
    node = shutil.which("node")
    if not node:
        raise Stop("Node isn't on the PATH; the live page needs it")
    proc = subprocess.Popen([node, str(LIVE_PAGE), "--app", str(app), "--port", str(page_port), "--stream-port", str(stream_port),
                             "--room", room, *extra], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    for _ in range(20):
        line = proc.stdout.readline()
        if not line:
            break
        if line.startswith("ready "):
            threading.Thread(target=drain, args=(proc.stdout,), daemon=True).start()   # keep its pipe from filling
            return proc, line.split(" ", 1)[1].strip()
        say("  " + line.rstrip())
    stop_page(proc)
    raise Stop("the live page didn't start (its words, if any, are above)")


def drain(stream):
    try:
        for _ in stream:
            pass
    except (OSError, ValueError):
        pass


def stop_page(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
    proc.stdout.close()


def status_line(perceiver, latest, logs=()):
    """One line of words and counts from the newest event and the loop's own counters: never a position. logs: the day
    files' writers (person_state.Log, ask.AskLog); lines they skipped at the day's cap are counted at the end."""
    event = latest or {}
    health, camera, c = event.get("health") or {}, event.get("camera") or {}, perceiver.counts
    lag = health.get("lag_s_median")
    skipped = sum(log.skipped for log in logs)
    problems = ", ".join([*("%d %s" % (c[key], words) for key, words in (
        ("refused_depth", "refused for depth"), ("detector_errors", "detector errors"), ("unreadable", "unreadable"),
        ("out_of_order", "out of order"), ("expired", "too late")) if c[key]),
        *(["%d lines not kept: the day's files are at their cap" % skipped] if skipped else [])])
    qwen = health.get("qwen")
    asks = "" if not qwen else " | Qwen %s%s, %d asks a minute" % (qwen["state"], (": " + qwen["why"]) if qwen.get("why") else "",
                                                                 qwen.get("asks_per_min") or 0)
    return ("camera %s%s | %s frames a second | %d people | %s behind | %d dropped a minute | %d frames%s%s"
            % (camera.get("status", "starting"), (": " + camera["why"]) if camera.get("why") else "",
               "-" if health.get("fps_in") is None else "%.1f" % health["fps_in"], len(event.get("tracks") or []),
               "-" if lag is None else "%.2f s" % lag, health.get("dropped_per_min") or 0, c["frames"],
               (", " + problems) if problems else "", asks))


def revision_of(receipt, transform_id):
    """(whether this step 2 receipt registered transform_id, the scan revision its picks were made on or None)."""
    fit = (receipt or {}).get("fit") or {}
    if fit.get("result") == "registered" and fit.get("transform_id") == transform_id:
        return True, receipt.get("scan_revision")
    return False, None


def registered_correction(found):
    """(factor, calibration_sha256) of the camera's own depth correction step 2's take was made on, from its receipt's
    source_correction (register_now.check_source's record: frames, applied, factors, calibrations), or (None, None)
    when it names no single one: every frame applied, one factor, one calibration hash. body_rgb.py reads the same."""
    if not isinstance(found, dict):
        return None, None
    frames, factors, calibrations = found.get("frames"), found.get("factors"), found.get("calibrations")
    if (type(frames) is not int or frames <= 0 or found.get("applied") != frames or not isinstance(factors, list)
            or len(factors) != 1 or not isinstance(calibrations, list) or len(calibrations) != 1):
        return None, None
    factor, calibration = factors[0], calibrations[0]
    if (type(factor) not in (int, float) or not 0.5 <= factor <= 1.5 or not isinstance(calibration, str)
            or not re.fullmatch("[0-9a-f]{64}", calibration)):
        return None, None
    return factor, calibration


def qualified_registration(receipt, transform_id):
    """{scan_revision, depth_scale, correction_factor, calibration_sha256} from a step 2 receipt that registered
    transform_id and qualified it (contract 1, den-live-register-now-2), or ValueError whose words say why not, for the
    caller's one-sentence refusal. The correction's two are None when the receipt names no single correction its take
    was made on; serve() refuses that when the camera's correction is required."""
    receipt = receipt if isinstance(receipt, dict) else {}
    if not revision_of(receipt, transform_id)[0]:
        raise ValueError("step 2's receipt didn't register %s" % transform_id)
    if receipt.get("schema") != REGISTER_SCHEMA:
        raise ValueError("step 2's receipt for %s is from before registrations were qualified" % transform_id)
    if receipt.get("qualified") is not True:
        raise ValueError("step 2 registered %s but didn't qualify it" % transform_id)
    revision, scale = receipt.get("scan_revision"), receipt.get("depth_scale")
    if not isinstance(revision, str) or not revision:
        raise ValueError("step 2's receipt for %s doesn't say which scan it was picked on" % transform_id)
    if type(scale) not in (int, float) or not DEPTH_SCALE_RANGE[0] <= scale <= DEPTH_SCALE_RANGE[1]:
        raise ValueError("step 2's receipt for %s has no depth scale den-live could apply" % transform_id)
    if take_scale(receipt.get("take")) != scale:
        raise ValueError("step 2's receipt for %s asks for depth x%g, but its take was made at x%g"
                         % (transform_id, scale, take_scale(receipt.get("take"))))
    factor, calibration = registered_correction(receipt.get("source_correction"))
    return {"scan_revision": revision, "depth_scale": float(scale), "correction_factor": factor,
            "calibration_sha256": calibration}


def receipt_registration(work, transform_id):
    """What the newest step 2 receipt in work that registered transform_id says, once qualified; else a Refusal."""
    path = registering_receipt(work, transform_id)
    if path is None:
        raise Refusal("no step 2 receipt registered %s: paste register_now.py again" % transform_id)
    try:
        return qualified_registration(json.loads(path.read_text(encoding="utf-8")), transform_id)
    except (OSError, ValueError) as exc:
        raise Refusal("%s: paste register_now.py again" % exc) from None


def registering_receipt(work, transform_id):
    """The newest register-now-N.json receipt in work that registered transform_id, as a path, or None."""
    def number(path):
        digits = re.sub(r"\D", "", path.stem)
        return int(digits) if digits else 0
    for path in sorted(Path(work).glob("register-now-*.json"), key=number, reverse=True):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if revision_of(receipt, transform_id)[0]:
            return path
    return None


def registered_revision(work, transform_id):
    """The scan revision step 2's picks were made on, from the receipt of the paste that registered transform_id: None
    when no receipt says (a registration from before receipts said so)."""
    path = registering_receipt(work, transform_id)
    return revision_of(json.loads(path.read_text(encoding="utf-8")), transform_id)[1] if path else None


def den_take(observe_tree):
    folder = str(Path(observe_tree) / "tools" / "den-take")
    if folder not in sys.path:
        sys.path.insert(0, folder)
    import take
    return take


def local_body_profile(profile_file, room_model, roommodel, camera_id, scan_revision):
    """Read a commissioned profile against this model and the scan the local page will use."""
    try:
        transform = roommodel.current_transform_id(roommodel.load(room_model), camera_id)
    except roommodel.RegistrationRefused:
        raise Stop("the RGB figure needs a usable camera registration") from None
    receipt = registering_receipt(Path(room_model).parent, transform)
    if receipt is None:
        raise Stop("the RGB figure needs the matching successful registration receipt")
    try:
        profile = body_rgb.RgbBodyProfile.from_config(
            json.loads(Path(profile_file).read_text(encoding="utf-8-sig")),
            json.loads(receipt.read_text(encoding="utf-8")),
        )
    except (OSError, TypeError, KeyError, ValueError, OverflowError):
        raise Stop("the RGB figure profile does not match the camera registration") from None
    if profile.scan_revision != scan_revision:
        raise Stop("the RGB figure profile does not match the active scan in the selected packs")
    return profile


def run(*, root, camera_url, token_file, venv, app, packs, room_model, camera_id=CAMERA_ID, page_room="real",
        open_page=webbrowser.open, until=None, status_every_s=STATUS_EVERY_S, floor=None, require_correction=None,
        page_extra=(), page_port=PAGE_PORT, stream_port=STREAM_PORT, body_input=None, body_profile_file=None):
    trees = {key: sc.archive(root / "tools", key) for key in TREES}
    if not Path(room_model).is_file():
        raise Stop("no registration yet: paste step 2's line (register_now.py) first")
    if not Path(venv).is_file():
        raise Stop("the detector's venv is not at %s" % venv)
    if not (Path(app) / "app" / "src" / "home-3d" / "apartment-people-layer.mjs").is_file():
        raise Stop("the Home checkout with the people layer isn't at %s" % app)
    if page_room == "real" and packs is not None:
        page_extra = ("--packs", str(packs), *page_extra)
    body_profile = None
    if body_profile_file is not None:
        if page_room != "real" or floor is not None or body_input is not None or packs is None:
            raise Stop("local RGB figure preview needs the registered room and its content packs")
        if not (Path(app) / "app" / "src" / "home-den-live-view.mjs").is_file():
            raise Stop("local RGB figure preview needs the Home checkout with the measured figure renderer")
        _, scan_revision = scan_floor(packs, den_site())
        tools = perceive.Tools(trees["observe"], trees["track"])
        body_profile = local_body_profile(body_profile_file, room_model, tools.roommodel, camera_id, scan_revision)
    take = den_take(trees["observe"])
    camera = sc.open_camera(take, camera_url, token_file, trees["observe"], body_timing=body_profile is not None)
    state = (root / "private" / "live").resolve()
    if take.inside_checkout(state) or take.in_onedrive(state):
        raise Stop("the data root is inside a git checkout or OneDrive")

    def registration(transform_id):   # the receipt beside the room model: no receipt, or none qualified, refuses
        return receipt_registration(Path(room_model).parent, transform_id)

    def open_live_page():
        proc, url = page(app, page_room, page_extra, page_port=page_port, stream_port=stream_port)
        say("Live: open %s and walk around. Ctrl+C stops it." % url)
        open_page(url)
        return lambda: stop_page(proc)

    return serve(trees=trees, take=take, camera=camera, room_model=room_model, camera_id=camera_id,
                 floor=floor, packs=packs, site=None if floor is not None else den_site(), registration=registration, state=state,
                 detector_argv=[str(venv), "-B", str(HERE / "detect_worker.py"), "--tools-root", str(trees["observe"]),
                                "--state", str(root / "state" / "detector")],
                 stream_port=stream_port, busy="is another run_local.py still open?", until=until,
                 status_every_s=status_every_s, require_correction=require_correction, after_start=open_live_page,
                 labels=room_labels.Store(root / "private" / "labels", say=say), body_input=body_input,
                 body_profile=body_profile)


def labels_only(*, root, app, open_page=webbrowser.open, until=None, page_room="real", page_extra=(), page_port=PAGE_PORT,
                stream_port=STREAM_PORT):
    """The labels plan's step 1: only the labels store, on the stream's labels path, and Home on your scan, so you can
    label the den. No camera, detector or registration. Returns the labels document as it was when stopped."""
    if not (Path(app) / "app" / "src" / "home-den-labels-view.mjs").is_file():
        raise Stop("the Home checkout with the Labels tool isn't at %s" % app)
    store = room_labels.Store(Path(root) / "private" / "labels", say=say)
    try:
        out = people_stream.PeopleStream(port=stream_port, labels=store, body_available=False)
    except OSError:
        raise Stop("port %d is busy: is another run_local.py still open?" % stream_port) from None
    out.start()
    proc = None
    try:
        proc, url = page(app, page_room, (*page_extra, "--labels-only"), page_port=page_port, stream_port=stream_port)
        count = len(store.get()["labels"])
        say("Labels: open %s, then press Labels in the den's view. Ctrl+C stops it." % url)
        say("Your labels stay on this PC, in your private den-live folder (%d so far)." % count)
        open_page(url)
        while not (until and until()):
            time.sleep(0.2)
        return store.get()
    finally:
        if proc is not None:
            stop_page(proc)
        out.close()


def start_body(perceiver, out, detector, body_input):
    """Attach the link now. Its first qualified bundle schedules worker setup without a wait."""
    link = body_link.BodyLink(detector, stream=out, clock=out.body_clock)
    perceiver.body = link
    perceiver.body_provider = body_input
    return link


def serve(*, trees, take, camera, room_model, camera_id, floor, packs, site, registration, state, detector_argv, stream_port,
          busy, until, status_every_s, require_correction=None, after_start=None, detector_log="detector.log", body=True,
          labels=None, asks=None, coordinator=None, body_input=None, body_profile=None, person_log=None, reinstall=False):
    """Run the shared local/service live loop with optional labels, Qwen and the lighting coordinator.

    registration(transform_id) is what step 2's qualified receipt says about the room model's registration,
    {scan_revision, depth_scale, correction_factor, calibration_sha256}, or a Refusal: no receipt, or none qualified,
    starts nothing. The camera's correction its take was made on goes to the Perceiver, which refuses any frame with
    another, saying to paste register_now.py without --again (and, with reinstall, the LA service's, install_la.py
    again after); with the correction required, a receipt that names none is refused here.
    person_log: a state folder whose days/ get each snapshot as den-person-state-1 (person_state.py), beside the ask's
    log; None keeps nothing, as run_local.py does."""
    R = rn.den_register(trees["register"])
    m, live = rn.load_method(R)
    revision = None
    if floor is None:
        bounds, revision = scan_floor(packs, site)
        say("Floor: your scan's walkable floor (pack %s), padded %g m on every side." % (revision, perceive.FLOOR_PAD_M))
    else:
        bounds = floor
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    t = perceive.Tools(trees["observe"], trees["track"])
    # The registration first: one it can't use, or one step 2 didn't qualify, stops here, before the detector's half minute.
    try:
        transform_id = t.roommodel.current_transform_id(t.roommodel.load(room_model), camera_id)
    except t.roommodel.RegistrationRefused as exc:
        raise Refusal("the registration can't be used: %s" % exc) from None
    registered = registration(transform_id)
    registered_on = registered["scan_revision"]
    if revision is not None and registered_on != revision:
        raise Refusal("the camera was registered on scan revision %s, but the active scan is %s: register again with step 2"
                      % (registered_on, revision))
    require = bool(live["require_source_correction"] if require_correction is None else require_correction)
    source_correction = registered.get("correction_factor"), registered.get("calibration_sha256")
    if None in source_correction:
        if require:
            raise Refusal("step 2's receipt for %s doesn't name the camera's depth correction its take was made on: "
                          "register again with step 2's register_now.py" % transform_id)
        source_correction = None   # nothing to hold frames to: the correction isn't required (den-sim's renders)
    try:
        method_copy = perceive.track_method_copy(t, perceive.padded(bounds), state)
        perceiver = perceive.Perceiver(t, room_model_path=room_model, camera_id=camera_id, max_error_bound_m=m["gate_m"],
                                       track_method_path=method_copy, home="den", content_revision=revision,
                                       require_source_correction=require, source_correction=source_correction,
                                       depth_scale=registered["depth_scale"],   # every depth plane, before any lift
                                       reinstall=reinstall)
    except perceive.Stop as exc:
        raise Refusal(str(exc)) from None
    perceiver.labels = labels   # each marker's near phrase, from the same labels the page edits
    say("Registration %s; the camera's correction %s; den-live's depth scale %s." % (
        perceiver.camera.transform_id, "required" if perceiver.require_source_correction else "not required",
        "none" if perceiver.depth_scale == 1.0 else "x%g" % perceiver.depth_scale))
    if (body_input is None and body_profile is not None and body_profile.scan_revision == revision
            and body_profile.transform_id == perceiver.camera.transform_id):
        body_input = body_rgb.RgbBodyInput(body_profile, perceiver.camera)
    detector = out = src = loop = cleanup = link = asker = lighting = None
    try:
        say("Starting the detector (about half a minute)...")
        try:
            detector = perceive.DetectorWorker(detector_argv, state / detector_log)
        except perceive.Stop as exc:
            raise Stop(str(exc)) from None
        perceiver.detector = detector
        body_available = bool(body and body_input is not None and revision is not None and registered_on == revision)
        try:
            out = people_stream.PeopleStream(port=stream_port, labels=labels, body_available=body_available)
        except OSError:
            raise Stop("port %d is busy: %s" % (stream_port, busy)) from None
        if body_available:
            link = start_body(perceiver, out, detector, body_input)
        if asks is not None:
            try:
                asker = asks(detector)   # (asker, stop), or None when the ask can't start
            except Exception as exc:  # the ask's failure never stops the markers; a type name only
                say("The Qwen ask is off: it didn't start (%s). The markers go on." % type(exc).__name__)
                asker = None
            if asker and labels is not None:
                # The labels' line for the asked person, before the loop feeds the ask (the inference plan's hook).
                asker[0].near_line = room_labels.near_line(labels, revision)
        mailbox = source.Mailbox()
        src = source.CameraSource(take, camera, mailbox)
        if coordinator is not None:
            try:
                lighting = coordinator()   # (service, stop), or None when it can't start
            except Exception as exc:  # the coordinator's failure never stops the markers; a type name only
                say("The lighting coordinator is off: it didn't start (%s). The markers go on." % type(exc).__name__)
                lighting = None
            if lighting is not None:
                lighting[0].site = site
                lighting[0].connect(out.publish_spot)
                out.routes.update(lighting[0].routes())
        person = None if person_log is None else person_state.Log(person_log)
        # The day files' writers: lines skipped at the day's cap go on the status line.
        logs = [log for log in (person, getattr(asker[0], "log", None) if asker else None) if log is not None]
        observer = evidence_observer(asker[0] if asker else None, lighting[0] if lighting else None, person)
        loop = perceive.Loop(src, mailbox, perceiver, out.publish, observe=observer)
        out.start()
        src.start()
        loop.start()
        if after_start is not None:
            cleanup = after_start()
        next_line = time.monotonic() + status_every_s
        while not (until and until()):
            if loop.stop_reason:
                raise (Refusal if isinstance(loop.stop_error, perceive.Refused) else Stop)(loop.stop_reason)
            if not loop.is_alive():
                raise Stop("the loop stopped")
            if time.monotonic() >= next_line:
                say(status_line(perceiver, out.latest, logs))
                next_line += status_every_s
            time.sleep(0.2)
        return {"counts": dict(perceiver.counts), "sessions": perceiver.sessions, "latest": out.latest, "drops": mailbox.dropped,
                "received": mailbox.received, "status": status_line(perceiver, out.latest, logs),
                "body": None if link is None else {**link.counts, "why": link.why, "stopped": link.stopped},
                "asks": None if asker is None else dict(asker[0].counts),
                "coordinator": None if lighting is None else dict(lighting[0].counts)}
    finally:
        for thread in (loop, src):
            if thread is not None:
                thread.halt.set()
        for thread in (loop, src):
            if thread is not None and thread.is_alive():
                thread.join(5)
        if asker is not None:
            asker[1]()
        if lighting is not None:
            lighting[1]()
        if detector is not None:
            detector.close()
        if link is not None:
            link.close()
        if out is not None:
            out.close()
        if cleanup is not None:
            cleanup()


def evidence_observer(asker, lighting, person_log=None):
    """Deliver current typed tracking before any reply tied to that snapshot. person_log (a person_state.Log) gets the
    same snapshot the coordinator gets, the ask's eligibility on it, and never stops the loop."""
    def observe(event, snapshot):
        current = snapshot
        try:
            if asker is not None:
                try:
                    event = asker.observe(event, snapshot)
                finally:
                    decorated = asker.snapshot
                    if decorated is not None and decorated.seq == snapshot.seq:
                        current = decorated
                    if lighting is not None:
                        lighting.offer_snapshot(current)
                reply = asker.pop_reply()
                if lighting is not None and reply is not None:
                    lighting.offer_inference(reply)
            elif lighting is not None:
                lighting.offer_snapshot(snapshot)
        finally:
            if person_log is not None:
                person_log.write(current)
        return event
    return observe


def main(argv=None, *, root=None, camera_url=None, token_file=None, venv=sc.VENV_PY, console=sc.has_console, **kw):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels-only", action="store_true", help="only the labels store and the page, to label the den")
    parser.add_argument("--body-profile", type=Path, help="private commissioned RGB figure profile for the local preview")
    parser.add_argument("--app", type=Path, help="Home checkout for the local page")
    parser.add_argument("--packs", type=Path, help="content packs shared by the local page and live loop")
    args = parser.parse_args(argv)
    if args.labels_only and (args.body_profile is not None or args.packs is not None):
        parser.error("--body-profile and --packs apply to the live view")
    if not console():
        say("live: refused, no console. Paste the line into PowerShell yourself.")
        return 2
    root = Path(root) if root else sc.data_root()
    app = kw.pop("app", APP)
    app = args.app if args.app is not None else app
    if args.labels_only:
        try:
            labels_only(root=root, app=app, **kw)
            return 0
        except Stop as exc:
            say("labels: stopped: %s" % exc)
            return 2
        except KeyboardInterrupt:
            say("labels: stopped. Your saved labels are kept; paste the line again to go on.")
            return 0
    try:
        observe_tree = sc.archive(root / "tools", "observe")
        sys.path.insert(0, str(observe_tree / "tools" / "den-take"))
        import take
        default_packs = kw.pop("packs", None)
        packs = args.packs or default_packs or content_packs()
        run(root=root, camera_url=camera_url or take.DEFAULT_CAMERA, token_file=token_file or take.DEFAULT_TOKEN_FILE,
            venv=venv, app=app, packs=packs,
            body_profile_file=args.body_profile,
            room_model=kw.pop("room_model", root / "private" / "register" / "room-model.jsonl"), **kw)
        return 0
    except Stop as exc:
        say("live: stopped: %s" % exc)
        return 2
    except KeyboardInterrupt:
        say("live: stopped. Paste the line again to start it again.")
        return 0
    except Exception as exc:  # type name only
        say("live: stopped (%s). Paste the line again; if it repeats, tell an agent this line." % type(exc).__name__)
        return 2


if __name__ == "__main__":
    sys.exit(main())
