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

It refuses to start without step 2's registration, the camera token, the detector's venv, or Node. Every 10 s it
prints one line of words and counts: the camera, frames a second, people, how far behind, drops. Never a coordinate.
Nothing is kept: no frames, no tracks. Only the tracker's method copy goes in <data root>\\private\\live\\.
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
import body_link  # noqa: E402  the live body's process (body_worker.py), fed without waiting
import perceive  # noqa: E402
import register_now as rn  # noqa: E402  the method file's numbers: the gate, and whether the camera's correction is required
import sofa_check as sc  # noqa: E402  the data root, the pinned archives, the venv, say
import source  # noqa: E402
import stream as people_stream  # noqa: E402

say, Stop = sc.say, sc.Stop
ROOT = HERE.parents[1]
LIVE_PAGE = HERE / "live_page.mjs"
# Home with the live view's layers: this checkout when it holds the body layer too (claude/den-live-body), else the
# claude/den-live-home worktree beside it.
APP = ROOT if (ROOT / "app" / "src" / "home-3d" / "apartment-body-layer.mjs").is_file() else ROOT.parent / "den-live-home"
PAGE_PORT, STREAM_PORT = 18190, 8111
STATUS_EVERY_S = 10.0
CAMERA_ID = "den-oak"
TREES = ("observe", "track", "register")   # the pinned trees the loop loads: den-observe and den-take, den-track, den-register


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


def status_line(perceiver, latest):
    """One line of words and counts from the newest event and the loop's own counters: never a position."""
    event = latest or {}
    health, camera, c = event.get("health") or {}, event.get("camera") or {}, perceiver.counts
    lag = health.get("lag_s_median")
    problems = ", ".join("%d %s" % (c[key], words) for key, words in (
        ("refused_depth", "refused for depth"), ("detector_errors", "detector errors"), ("unreadable", "unreadable"),
        ("out_of_order", "out of order"), ("expired", "too late")) if c[key])
    return ("camera %s%s | %s frames a second | %d people | %s behind | %d dropped a minute | %d frames%s"
            % (camera.get("status", "starting"), (": " + camera["why"]) if camera.get("why") else "",
               "-" if health.get("fps_in") is None else "%.1f" % health["fps_in"], len(event.get("tracks") or []),
               "-" if lag is None else "%.2f s" % lag, health.get("dropped_per_min") or 0, c["frames"],
               (", " + problems) if problems else ""))


def revision_of(receipt, transform_id):
    """(whether this step 2 receipt registered transform_id, the scan revision its picks were made on or None)."""
    fit = (receipt or {}).get("fit") or {}
    if fit.get("result") == "registered" and fit.get("transform_id") == transform_id:
        return True, receipt.get("scan_revision")
    return False, None


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


def run(*, root, camera_url, token_file, venv, app, packs, room_model, camera_id=CAMERA_ID, page_room="real",
        open_page=webbrowser.open, until=None, status_every_s=STATUS_EVERY_S, floor=None, require_correction=None,
        page_extra=(), page_port=PAGE_PORT, stream_port=STREAM_PORT):
    trees = {key: sc.archive(root / "tools", key) for key in TREES}
    if not Path(room_model).is_file():
        raise Stop("no registration yet: paste step 2's line (register_now.py) first")
    if not Path(venv).is_file():
        raise Stop("the detector's venv is not at %s" % venv)
    if not (Path(app) / "app" / "src" / "home-3d" / "apartment-people-layer.mjs").is_file():
        raise Stop("the Home checkout with the people layer isn't at %s" % app)
    take = den_take(trees["observe"])
    camera = sc.open_camera(take, camera_url, token_file, trees["observe"])
    state = (root / "private" / "live").resolve()
    if take.inside_checkout(state) or take.in_onedrive(state):
        raise Stop("the data root is inside a git checkout or OneDrive")

    def picked_on(transform_id):
        revision = registered_revision(Path(room_model).parent, transform_id)
        if revision is None:
            say("Note: step 2's receipt doesn't say which scan revision the registration was picked on.")
        return revision

    def open_live_page():
        proc, url = page(app, page_room, page_extra, page_port=page_port, stream_port=stream_port)
        say("Live: open %s and walk around. Ctrl+C stops it." % url)
        open_page(url)
        return lambda: stop_page(proc)

    return serve(trees=trees, take=take, camera=camera, room_model=room_model, camera_id=camera_id,
                 floor=floor, packs=packs, site=None if floor is not None else den_site(), picked_on=picked_on, state=state,
                 detector_argv=[str(venv), "-B", str(HERE / "detect_worker.py"), "--tools-root", str(trees["observe"]),
                                "--state", str(root / "state" / "detector")],
                 stream_port=stream_port, busy="is another run_local.py still open?", until=until,
                 status_every_s=status_every_s, require_correction=require_correction, after_start=open_live_page)


def start_body(perceiver, out, venv_python, log_path, revision):
    """The live body's process, in the detector's venv, set up with the registration's rectified-left intrinsics and
    room_from_camera. Its frames go out on the stream's /body path, tagged with the registration and scan revision the
    markers carry. One that doesn't start is said once; the markers go on without it."""
    camera = perceiver.camera
    rotation, translation = camera.room_from_left
    setup = {"left_k": list(camera.rig.left_k[:4]), "room_from_left": [[list(r) for r in rotation], list(translation)], "home": perceiver.home}
    scope = {"transform_id": camera.transform_id, "content_revision": revision}
    link = body_link.BodyLink([str(venv_python), "-B", str(HERE / "body_worker.py")], log_path, setup=setup,
                              on_frame=lambda frame: out.publish_body({**frame, **scope}))
    if link.stopped:
        say("The live body is off: %s. The markers go on." % link.stopped)
        link.close()
        return None
    perceiver.body = link
    say("The live body is on (body_worker.py). It learns the empty room once the den has been empty for a few seconds.")
    return link


def serve(*, trees, take, camera, room_model, camera_id, floor, packs, site, picked_on, state, detector_argv, stream_port,
          busy, until, status_every_s, require_correction=None, after_start=None, detector_log="detector.log", body=True,
          body_log="body.log"):
    """The live loop, shared by run_local (this PC, with the page) and run_service (the LA service, without it). It keeps
    only the tracker's method copy and the detector's log in state. picked_on(transform_id) gives the scan revision the
    registration was picked on, or None; after_start() runs once the loop is up and returns its own cleanup."""
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
    # The registration first: one it can't use stops here, before the detector's half minute.
    try:
        method_copy = perceive.track_method_copy(t, perceive.padded(bounds), state)
        perceiver = perceive.Perceiver(t, room_model_path=room_model, camera_id=camera_id, max_error_bound_m=m["gate_m"],
                                       track_method_path=method_copy, home="den", content_revision=revision,
                                       require_source_correction=bool(live["require_source_correction"] if require_correction is None else require_correction))
    except perceive.Stop as exc:
        raise Refusal(str(exc)) from None
    if revision is not None:
        registered_on = picked_on(perceiver.camera.transform_id)
        if registered_on is not None and registered_on != revision:
            raise Refusal("the camera was registered on scan revision %s, but the active scan is %s: register again with step 2"
                          % (registered_on, revision))
    say("Registration %s; the camera's correction %s." % (perceiver.camera.transform_id,
                                                          "required" if perceiver.require_source_correction else "not required"))
    detector = out = src = loop = cleanup = link = None
    try:
        say("Starting the detector (about half a minute)...")
        try:
            detector = perceive.DetectorWorker(detector_argv, state / detector_log)
        except perceive.Stop as exc:
            raise Stop(str(exc)) from None
        perceiver.detector = detector
        try:
            out = people_stream.PeopleStream(port=stream_port)
        except OSError:
            raise Stop("port %d is busy: %s" % (stream_port, busy)) from None
        if body:
            link = start_body(perceiver, out, detector_argv[0], state / body_log, revision)
        mailbox = source.Mailbox()
        src = source.CameraSource(take, camera, mailbox)
        loop = perceive.Loop(src, mailbox, perceiver, out.publish)
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
                say(status_line(perceiver, out.latest))
                next_line += status_every_s
            time.sleep(0.2)
        return {"counts": dict(perceiver.counts), "sessions": perceiver.sessions, "latest": out.latest, "drops": mailbox.dropped,
                "received": mailbox.received, "status": status_line(perceiver, out.latest),
                "body": None if link is None else {**link.counts, "why": link.why, "stopped": link.stopped}}
    finally:
        for thread in (loop, src):
            if thread is not None:
                thread.halt.set()
        for thread in (loop, src):
            if thread is not None and thread.is_alive():
                thread.join(5)
        if detector is not None:
            detector.close()
        if link is not None:
            link.close()
        if out is not None:
            out.close()
        if cleanup is not None:
            cleanup()


def main(argv=None, *, root=None, camera_url=None, token_file=None, venv=sc.VENV_PY, console=sc.has_console, **kw):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    if not console():
        say("live: refused, no console. Paste the line into PowerShell yourself.")
        return 2
    root = Path(root) if root else sc.data_root()
    try:
        observe_tree = sc.archive(root / "tools", "observe")
        sys.path.insert(0, str(observe_tree / "tools" / "den-take"))
        import take
        run(root=root, camera_url=camera_url or take.DEFAULT_CAMERA, token_file=token_file or take.DEFAULT_TOKEN_FILE,
            venv=venv, app=kw.pop("app", APP), packs=kw.pop("packs", None) or content_packs(),
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
