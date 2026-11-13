"""Synthetic perception inputs for the real den-live preview in the browser harness."""

import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import coordinator_live
from contracts import FrameRef, InferenceReply, PlaneRef, TrackEvidence, TrackingSnapshot
import run_service
import sofa_check
from stream import PeopleStream
from test_preview import joints


def main():
    config = json.loads(sys.stdin.readline())
    state = Path(config["state_dir"])
    binding = config["binding"]
    fixtures = config["fixtures"]
    target = config.get("target_m", [1.2, 1.38, .65])
    facing = config.get("facing", [0, 1])
    tree = sofa_check.archive(state / "trees", "coordinator")
    writes = []

    def forbidden(*args, **kwargs):
        writes.append("HA client constructed")
        raise AssertionError("the sofa path cannot open an HA write client")

    block = {"preview": True, "hue_overlay": True, "fixtures": fixtures, "binding": binding}
    settings, why = run_service.coordinator_settings({"coordinator": block}, {"ha_url": "synthetic"})
    if why:
        raise RuntimeError(why)
    with patch.object(run_service.ha_link, "HaClient", forbidden):
        service, close = run_service.coordinator_starter(settings, {"ha_url": "synthetic"}, state=state, tree=tree)()
    service.site = config["site"]
    service.configure({"fade_in_s": 0})
    server = PeopleStream(port=0, body_available=False)
    service.connect(server.publish_spot)
    server.routes.update(service.routes())
    server.routes[("GET", "/test/receipt")] = lambda _body: (200, {"writes": writes, **service.state()[1]})
    server.start()
    halt = threading.Event()
    scene = "standing"

    def feed():
        seq = 0
        first = time.monotonic()
        while not halt.wait(.1):
            if scene == "silent":
                continue
            seq += 1
            now, wall = time.monotonic(), time.time()
            plane = PlaneRef(seq, now, now, 640, 360)
            ref = FrameRef("browser", 1, seq, "synthetic", "rgb", plane, None, None, None,
                           binding["transform_id"], binding["scan_revision"], now, now, wall)
            seated = scene == "reading"
            fx, fy = facing
            x, y, z = target
            origin = x - .38 * fx, y - .38 * fy
            room_pose = {name: ((origin[0] + fy * (jx - 1.2) + fx * (jy - 1),
                                 origin[1] - fx * (jx - 1.2) + fy * (jy - 1), jz + z - .65), status)
                         for name, ((jx, jy, jz), status) in joints(seated).items()}
            track = TrackEvidence("synthetic-owner", "confirmed", origin, .1, "stereo", ("pose",),
                                  "confident", 1, first, room_pose)
            tracking = TrackingSnapshot("browser", 1, seq, ref, "fresh", None, "present", (track,),
                                        track.track_id if seated else None, 1)
            service.offer_snapshot(tracking)
            if seated:
                reply = InferenceReply(f"reply-{seq}", "synthetic", "browser", 1, track.track_id, 1, 1, (ref,),
                    {"status": "valid", "facts": {"people_visible": "one", "activity_hypothesis": "reading",
                                                  "book_state": "open", "held_object": "book"}},
                    {"safe": True, "status": "confident", "window_start_mono": now, "window_end_mono": now},
                    {}, {}, now, now, now + 10, {})
                service.offer_inference(reply)
            server.publish({"schema": "den-live-people-1", "home": "den", "seq": seq,
                            "transform_id": binding["transform_id"], "content_revision": binding["scan_revision"],
                            "capture_ts": wall, "emitted_ts": wall, "mode": "rgb", "camera": {"status": "ok"},
                            "tracks": [{"track_id": track.track_id, "state": "confirmed", "xy_m": track.xy_m,
                                        "sigma_m": .1, "age_s": 0}]})

    worker = threading.Thread(target=feed, daemon=True)
    worker.start()
    print(json.dumps({"port": server.port, "pid": os.getpid()}), flush=True)
    try:
        for line in sys.stdin:
            command = json.loads(line)
            if command.get("command") == "close":
                break
            if command.get("scene") in ("reading", "standing", "silent"):
                scene = command["scene"]
    finally:
        halt.set()
        worker.join(2)
        close()
        server.close()


if __name__ == "__main__":
    main()
