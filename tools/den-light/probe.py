#!/usr/bin/env python3
"""Rung 8 Stage 1's probe session. Standard library only; runs on the owner's PC.

    python -B tools\\den-light\\probe.py --session C:\\den-light\\<session-id> --ha http://<ha-host>:8123 --camera http://<jetson-address>:8090 --stage0 C:\\den-light\\stage0-<date>\\sweep-<n>
    python -B tools\\den-light\\probe.py --recover C:\\den-light\\<session-id> --ha http://<ha-host>:8123 --camera http://<jetson-address>:8090

One session, in the plan's order: check the store copy and HA, wait for the den
to empty, pause den_lighting, hold the camera, step the bulbs, end all off,
release, write the saved normal scene back from the pre-probe snapshot (still
paused), switch on, then den_lighting.resume_automatic. Every HA call is
logged in probes.jsonl as a non-human change before it is sent (den-light-
probe-1), with its context id after; each step logs its send and settle
times. It aborts on occupancy, on any state change it did not send, on the
camera guard, on HA lost and on Ctrl+C. --recover finishes an interrupted or
aborted session from session.json and probes.jsonl. Neither token is ever
printed or written.
"""
import argparse
import http.client
import json
import os
import re
import signal
import statistics
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import guard
import meter
import model
import sweep

HERE = Path(__file__).resolve().parent
DEFAULT_HA_TOKEN = r"C:\den-light\private\ha-token.txt"
DEFAULT_STORE_COPY = r"C:\den-light\private\den_lighting.state"
SESSION_FILES = ("session.json", "probes.jsonl", "frames", "readings.json", "light-model.json", "score.json")
SESSION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MIN_FREE_BYTES = 1024 ** 3
SCHEMA = "den-light-session-1"


class Abort(Exception):
    def __init__(self, reason, *, ha_lost=False, game=False):
        super().__init__(reason)
        self.reason, self.ha_lost, self.game = reason, ha_lost, game


class HAUnreachable(Exception):
    pass


class HAAnswered(HAUnreachable):
    """HA answered, but not 200 (a failed Zigbee delivery is a 500). Handled as unreachable, except
    that a refused probe write aborts with HA still there to take the restore."""


# ---- Home Assistant's REST API --------------------------------------------------------

class HA:
    def __init__(self, url, token, timeout):
        parts = urlsplit(url or "")
        if parts.scheme != "http" or not parts.hostname or parts.path not in ("", "/") or parts.query:
            raise sweep.Refused("--ha must look like http://<host>:8123")
        self.host, self.port, self.timeout = parts.hostname, parts.port or 80, timeout
        self._token = token

    def __repr__(self):
        return f"HA({self.host}:{self.port})"

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        headers = {"Authorization": "Bearer " + self._token, "Content-Type": "application/json"}
        try:
            connection.request(method, path, body=None if body is None else json.dumps(body).encode(), headers=headers)
            response = connection.getresponse()
            data = response.read(sweep.MAX_BODY + 1)
            status = response.status
        except (OSError, http.client.HTTPException) as exc:
            raise HAUnreachable(type(exc).__name__) from None
        finally:
            connection.close()
        if status != 200:
            raise HAAnswered(f"answered {status} to {method} {path.split('?')[0]}")
        if len(data) > sweep.MAX_BODY:
            raise HAUnreachable("reply too large")
        try:
            return json.loads(data) if data else None
        except ValueError:
            raise HAUnreachable("reply is not JSON") from None

    def states(self):
        return {s["entity_id"]: s for s in self.request("GET", "/api/states")}

    def call(self, domain, service, data):
        return self.request("POST", f"/api/services/{domain}/{service}", data) or []

    def history(self, start_iso, entity_ids):
        # significant_changes_only=0: the sensor's occupancy is an attribute, and HA drops attribute changes by default
        return self.request("GET", f"/api/history/period/{quote(start_iso)}?significant_changes_only=0"
                                   "&filter_entity_id=" + ",".join(entity_ids))

    def logbook(self, start_iso):
        return self.request("GET", f"/api/logbook/{quote(start_iso)}")


def iso_time(text):
    from datetime import datetime
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def ha_now(by_id):
    """The latest HA timestamp in one read: a lower bound on HA's clock."""
    stamps = [iso_time(s["last_updated"]) for s in by_id.values() if s.get("last_updated")]
    return max(stamps) if stamps else None


# ---- the session folder and its log ------------------------------------------------------

def session_folder(path, token_files, *, new):
    path = Path(path)
    if not path.is_absolute():
        raise sweep.Refused("the session folder must be an absolute path, for example C:\\den-light\\s1-20261110")
    if new and not path.exists():
        if not path.parent.is_dir():
            raise sweep.Refused("the session folder's parent does not exist (README makes C:\\den-light)")
        if not SESSION_NAME.fullmatch(path.name):
            raise sweep.Refused("a session id is letters, digits, '-', '_' and '.' only")
    resolved = path.resolve()
    if sweep.inside_checkout(resolved):
        raise sweep.Refused("the folder is inside a git checkout; frames never go into git")
    if sweep.in_onedrive(resolved):
        raise sweep.Refused("the folder is inside OneDrive; frames stay on this PC only")
    for token in token_files:
        token = Path(token).resolve()
        if resolved == token.parent or resolved in token.parents:
            raise sweep.Refused("the session folder must not contain a private token file")
    if new and resolved.exists() and any((resolved / name).exists() for name in SESSION_FILES):
        raise sweep.Refused("the folder already holds a session; use a new session id")
    if sweep.free_bytes(resolved) < MIN_FREE_BYTES:
        raise sweep.Refused("less than 1 GB free on the folder's drive")
    return resolved


class ProbeLog:
    """probes.jsonl: the run's log of non-human changes, each line flushed as written."""

    def __init__(self, path, wall):
        self.path, self.wall = Path(path), wall
        self.out = open(self.path, "a", encoding="utf-8", newline="\n")
        if self.path.stat().st_size:  # after a kill mid-write, the next line must not join the torn one
            with open(self.path, "rb") as existing:
                existing.seek(-1, os.SEEK_END)
                if existing.read(1) != b"\n":
                    self.out.write("\n")

    def write(self, kind, **fields):
        line = {"log": guard.PROBE_CHANGE, "kind": kind, "pc": self.wall(), **fields}
        self.out.write(json.dumps(line, sort_keys=True) + "\n")
        self.out.flush()
        try:
            os.fsync(self.out.fileno())
        except OSError:
            pass
        return line

    def close(self):
        if not self.out.closed:
            self.out.close()


def read_log(path):
    return model.read_jsonl(path)


def write_session(folder, record):
    """session.json, replaced whole (written beside it, then moved over it)."""
    temp = folder / "session.json.new"
    with open(temp, "w", encoding="utf-8", newline="\n") as out:
        json.dump(record, out, indent=2, sort_keys=True, allow_nan=False)
        out.write("\n")
    os.replace(temp, folder / "session.json")


def stage0_meter_norm(sweep_folder):
    """meter-norm/v1's parameters from a passing Stage 0 sweep (response.json, or a passing recheck)."""
    folder = Path(sweep_folder)
    candidates = [folder / "response.json"] + sorted(folder.glob("recheck-*.json"))
    for path in reversed(candidates):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("outcome") == "pass" and isinstance(record.get("meter_norm"), dict):
            return record["meter_norm"], path.name
    raise sweep.Refused("--stage0 must be a Stage 0 sweep folder whose response.json (or a recheck) passed")


def fixture_records(by_id):
    """The four bulbs as live-v1 `fixture` records, from the limits HA reports."""
    out = []
    for e in guard.LIGHTS:
        attributes = (by_id.get(e) or {}).get("attributes") or {}
        low, high = attributes.get("min_color_temp_kelvin"), attributes.get("max_color_temp_kelvin")
        if type(low) not in (int, float) or type(high) not in (int, float):
            raise sweep.Refused(f"{e} reports no colour temperature range")
        out.append({"record": "fixture", "contract": "live-v1", "fixture_id": e.split(".", 1)[1],
                    "model": "hue-den-4", "pose": {"position": None, "method": "assumed"},
                    "channels": [{"channel_id": "white", "kind": "tunable_white", "entity_id": e,
                                  "cct_range_k": [int(low), int(high)]}],
                    "orientation_calibration": None, "optics": None, "dimming_curve": None, "gimbal": None})
    return out


# ---- the session --------------------------------------------------------------------------

class Session:
    """One probe session in one new session folder (or --recover on an old one)."""

    def __init__(self, folder, ha, camera, method1, method0, *, clock=time.perf_counter, wall=time.time,
                 sleep=time.sleep, say=print):
        self.folder, self.ha, self.camera = Path(folder), ha, camera
        self.m1, self.m0 = method1, method0
        self.clock, self.wall, self.sleep, self.say = clock, wall, sleep, say
        self.log = None
        self.record = {}
        self.owner = None
        self.switch = self.sensor = None
        self.snapshot = None
        self.last = None             # the last HA read, by entity id
        self.last_read = None
        self.occupancy_seen = []     # the sensor's occupancy values, in order, since vacancy
        self.interrupted = False
        self.probing = False
        self.hold = None             # the held settings, or None
        self.generation = None
        self.last_post = None
        self.camera_session = None
        self.last_sequence = None
        self.last_frame_at = None
        self.last_all_off = None     # the last all-off capture's region means, for the bracket check
        self.frames_saved = 0
        self.last_clean = 0
        self.regions = meter.sample_regions(method0["pixel_step"], method0["grid_cols"], method0["grid_rows"])
        self.on_step = None          # tests: called with each step before it is sent
        self.stage0 = None           # {sweep, file, meter_norm_sha256}: the passing Stage 0 sweep

    # Ctrl+C: never raises; it marks the session, which aborts at its next check
    def on_ctrl_c(self, signum, frame):
        self.interrupted = True

    def checkpoint(self):
        if self.interrupted and self.probing:
            raise Abort("Ctrl+C")

    def save_record(self, **changes):
        self.record.update(changes)
        write_session(self.folder, self.record)

    # Home Assistant -----------------------------------------------------------------
    def read(self):
        try:
            by_id = self.ha.states()
        except HAUnreachable as exc:
            raise Abort(f"Home Assistant unreachable ({exc})", ha_lost=True) from None
        self.last_read = self.clock()
        return by_id

    def sample(self, by_id):
        attributes = (by_id.get(self.sensor) or {}).get("attributes") or {}
        value = attributes.get("occupancy")
        if not self.occupancy_seen or self.occupancy_seen[-1] != value:
            self.occupancy_seen.append(value)
        self.log.write("sample", ha_time=ha_now(by_id), occupancy=value,
                       binary=(by_id.get(guard.OCCUPANCY) or {}).get("state"),
                       automatic=attributes.get("automatic"), gaming_session=attributes.get("gaming_session"))

    def guard_read(self):
        """One HA read checked against the last: logs every change and raises Abort on any abort rule."""
        self.checkpoint()
        by_id = self.read()
        self.sample(by_id)
        before = self.last
        for e in guard.LIGHTS:
            kind = guard.classify(e, before.get(e), by_id.get(e), self.owner)
            if kind:
                s = by_id.get(e) or {}
                self.log.write("observed", entity_id=e, state=s.get("state"),
                               scene=guard.scene_of_state(s), last_updated=s.get("last_updated"),
                               context_id=(s.get("context") or {}).get("id"), owner=kind)
        reason = guard.abort_reason(before, by_id, self.owner, self.switch, self.sensor)
        self.last = by_id
        if reason:
            game = ((by_id.get(self.sensor) or {}).get("attributes") or {}).get("gaming_session") is True
            raise Abort(reason, game=game)
        return by_id

    def guard_if_due(self):
        if self.last_read is None or self.clock() - self.last_read >= self.m1["poll_s"]:
            self.guard_read()
        else:
            self.checkpoint()

    def send(self, e, target):
        """One logged probe write: the intent line, the call, then HA's context id."""
        data = {"entity_id": e, "transition": 0}
        if target["on"]:
            data.update(brightness=target["brightness"], color_temp_kelvin=target["kelvin"])
        service = "turn_on" if target["on"] else "turn_off"
        self.owner.intend(e, target)
        self.log.write("intent", entity_id=e, service="light." + service, data=data, change="non-human")
        try:
            states = self.ha.call("light", service, data)
        except HAAnswered as exc:
            raise Abort(f"Home Assistant refused a probe write ({exc})") from None
        except HAUnreachable as exc:
            raise Abort(f"Home Assistant unreachable ({exc})", ha_lost=True) from None
        context = next(((s.get("context") or {}).get("id") for s in states if s.get("entity_id") == e), None)
        self.owner.responded(e, context)
        self.log.write("sent", entity_id=e, context_id=context, own=True)
        return context

    def apply(self, scene):
        """Send what differs from the tool's latest intent, then wait until HA shows every target."""
        sent_pc = self.wall()
        t_send = self.clock()
        for e in guard.LIGHTS:
            want = scene[e]
            latest = self.owner.latest(e)
            if want["on"] == latest["on"] and (not want["on"] or (want["brightness"], want["kelvin"]) ==
                                               (latest["brightness"], latest["kelvin"])):
                continue
            self.send(e, want)
        t_acked = self.clock()
        deadline = t_acked + self.m1["settle_limit_s"]
        while True:
            by_id = self.guard_read()
            if all(guard.matches(scene[e], guard.scene_of_state(by_id.get(e))) for e in guard.LIGHTS):
                break
            if self.clock() > deadline:
                raise Abort("the bulbs did not reach the step's scene")
            self.sleep(min(0.2, self.m1["poll_s"]))
        return {"sent_pc": sent_pc, "send_s": t_acked - t_send, "t_send": t_send}

    # the camera -------------------------------------------------------------------------
    def post_hold(self, settings):
        body = {"exposure_us": settings["exposure_us"], "iso": settings["iso"],
                "white_balance_kelvin": self.m0["white_balance_kelvin"], "ttl_s": self.m0["hold_ttl_s"]}
        try:
            status, reply = self.camera.post("/meter", body)
        except OSError as exc:
            raise Abort(f"camera unreachable ({type(exc).__name__})") from None
        if status == 503:
            raise Abort("camera 503")
        if status != 200 or type(reply.get("generation")) is not int:
            raise Abort(f"POST /meter was refused ({status})")
        self.hold = dict(settings)
        self.generation, self.last_post = reply["generation"], self.clock()
        self.log.write("hold", exposure_us=settings["exposure_us"], iso=settings["iso"], generation=self.generation)

    def release(self):
        if self.hold is None:
            return None
        self.hold = None
        try:
            status, reply = self.camera.post("/meter/release", {})
        except Exception as exc:
            self.log.write("release", released=False, error=type(exc).__name__)
            return False
        released = status == 200 and reply.get("held") is False
        self.log.write("release", released=released, status=status)
        return released

    def next_frame(self):
        """(report, nv12, meta): the next new bundle, with the camera guard's frame rules."""
        while True:
            self.guard_if_due()
            if self.hold is not None and self.clock() - self.last_post >= self.m0["renew_every_s"]:
                self.post_hold(self.hold)
            if self.last_frame_at is not None and self.clock() - self.last_frame_at > self.m1["frame_timeout_s"]:
                raise Abort(f"{self.m1['frame_timeout_s']} s without a frame")
            try:
                status, data = self.camera.get("/frame.npz")
            except OSError as exc:
                raise Abort(f"camera unreachable ({type(exc).__name__})") from None
            if status == 503:
                raise Abort("camera 503")
            if status != 200:
                raise Abort(f"GET /frame.npz answered {status}")
            try:
                meta, nv12 = meter.read_bundle(data)
                report = meter.rgb_report(meta)
            except meter.BundleError as exc:
                raise Abort(f"unreadable bundle: {exc}") from None
            if self.camera_session is None:
                self.camera_session = report["session"]
            elif report["session"] != self.camera_session:
                raise Abort("a new camera session")
            if self.last_sequence is not None and report["sequence"] <= self.last_sequence:
                self.sleep(self.m0["poll_s"])
                continue
            self.last_sequence = report["sequence"]
            self.last_frame_at = self.clock()
            return report, nv12, meta

    def means(self, nv12):
        return meter.region_means(meter.decode(nv12, self.m0["pixel_step"]), self.regions, len(set(self.regions)))

    def settled_frames(self, sent_at=None, held=True):
        """(frames, landed): frames_per_capture consecutive agreeing frames (Stage 0's noise tolerance) from
        step_dwell_s after the send, under the hold, and when the camera first showed that light: the earliest
        frame since the send, dwell frames included, with no frame between it and the run that disagrees."""
        tolerance = self.m0["noise_tolerance_codes"]
        start = self.clock() if sent_at is None else sent_at
        counted_from = start + (0 if sent_at is None else self.m1["step_dwell_s"])
        kept, anchor, anchor_at, seen = [], None, 0, []
        while len(kept) < self.m1["frames_per_capture"]:
            report, nv12, meta = self.next_frame()
            now = self.clock()
            if held:
                if not report["held"] or report["generation"] != self.generation:
                    if now - start > self.m1["settle_limit_s"]:
                        raise Abort("the hold did not apply")
                    continue
                ok, why = meter.gain_agreement(report, dict(self.hold, white_balance_kelvin=self.m0["white_balance_kelvin"]),
                                               self.m0["exposure_quantum_us"])
                if not ok:
                    raise Abort(f"a held frame failed gain agreement ({why})")
            means = self.means(nv12)
            seen.append((now, means))
            if now < counted_from:
                continue
            if anchor is None or not meter.agree(anchor, means, tolerance):
                if len(kept) >= 2:
                    raise Abort("frames within a settled step disagree")
                if now - start > self.m1["settle_limit_s"]:
                    raise Abort("the step did not settle")
                anchor, anchor_at, kept = means, len(seen) - 1, [(report, nv12, meta)]
                continue
            kept.append((report, nv12, meta))
        landed = seen[anchor_at][0]
        for at, means in reversed(seen[:anchor_at]):
            if not meter.agree(anchor, means, tolerance):
                break
            landed = at
        return kept, landed

    def check_bracket(self, nv12):
        """Each all-off capture must agree with the one before: something else changed the room otherwise."""
        means = self.means(nv12)
        if self.last_all_off is not None and not meter.agree(self.last_all_off, means, self.m0["noise_tolerance_codes"]):
            raise Abort("all-off brackets disagree")
        self.last_all_off = means

    def save(self, step, kept, timing, landed, captured):
        """Write a clean step's frames (after a guard read that passed) and its capture and step lines."""
        files = []
        for report, nv12, meta in kept:
            self.frames_saved += 1
            name = f"frames/{self.frames_saved:05d}.nv12"
            with open(self.folder / name, "xb") as out:
                out.write(nv12)
            files.append({"file": name, "sha256": meter.sha256_bytes(nv12), "report": report,
                          "rgb": meta["frames"]["rgb"]})
        self.log.write("capture", step=step["index"], frames=files,
                       hold=dict(self.hold) if self.hold else None)
        self.log.write("step", index=step["index"], step_kind=step["kind"], scene=step["scene"],
                       label=step.get("label"), ratio=step.get("ratio"), bulb=step.get("bulb"),
                       level=step.get("level"), kelvin=step.get("kelvin"),
                       sent_pc=timing["sent_pc"], send_s=round(timing["send_s"], 4),
                       settle_s=round(landed - timing["t_send"], 4), capture_s=round(captured - timing["t_send"], 4))
        self.last_clean = step["index"]

    # phases ------------------------------------------------------------------------------
    def start(self, store_text):
        """The start checks, before anything is paused or written to HA. session.json is written here."""
        self.m1.refuse_if_open("A probe session")
        if self.m0.open:
            raise sweep.Refused("METHOD-V1's Stage 0 block still has open numbers")
        self.snapshot = guard.read_store(store_text)
        try:
            by_id = self.ha.states()
        except HAUnreachable as exc:
            raise sweep.Refused(f"Home Assistant is unreachable ({exc})") from None
        self.switch, self.sensor = guard.find_entities(list(by_id.values()))
        problems = guard.start_problems(by_id, self.switch, self.sensor)
        if problems:
            raise sweep.Refused("; ".join(problems))
        seen = sweep.camera_ready(self.camera, drop_in_installed=True)
        fixtures = fixture_records(by_id)
        ranges = {f["channels"][0]["entity_id"]: tuple(f["channels"][0]["cct_range_k"]) for f in fixtures}
        warmest = {e: ranges[e][0] for e in guard.LIGHTS}
        try:
            plan = model.schedule(self.m1, self.snapshot, ranges, warmest)
        except ValueError as exc:
            raise sweep.Refused(f"no valid schedule from METHOD-V1's numbers ({exc})") from None
        (self.folder / "frames").mkdir(parents=True, exist_ok=False)
        with open(self.folder / "fixtures.jsonl", "x", encoding="utf-8", newline="\n") as out:
            for f in fixtures:
                out.write(json.dumps(f, sort_keys=True) + "\n")
        self.log = ProbeLog(self.folder / "probes.jsonl", self.wall)
        snapshot_text = json.dumps(self.snapshot, sort_keys=True)
        self.record = {"schema": SCHEMA, "id": self.folder.name, "outcome": "started", "abort_reason": None,
                       "phase": "start", "started_pc": self.wall(),
                       "code_sha256": {n: meter.file_sha256(HERE / n) for n in
                                       ("probe.py", "guard.py", "model.py", "predict.py", "sweep.py", "meter.py")},
                       "method_sha256": self.m1.file_sha256, "method_stage1_block_sha256": self.m1.block_sha256,
                       "method_stage0_block_sha256": self.m0.block_sha256,
                       "snapshot": self.snapshot, "snapshot_sha256": meter.sha256_bytes(snapshot_text.encode()),
                       "start_readings": {e: guard.scene_of_state(by_id[e]) for e in guard.LIGHTS},
                       "colour_temperature_ranges": {e: list(r) for e, r in ranges.items()},
                       "temperatures": plan["temperatures"], "switch": self.switch, "sensor": self.sensor,
                       "camera_session": seen["session"], "held": None, "unrestored": [], "stage0": self.stage0,
                       "restore": None, "resume": None, "walk_through": None, "vacancy_began_ha": None,
                       "last_clean_step": 0}
        write_session(self.folder, self.record)
        self.log.write("phase", phase="start")
        self.camera_session = seen["session"]
        return plan

    def wait_for_vacancy(self):
        self.say("Start checks passed. Leave the den now and keep it empty for about 15 minutes.")
        self.log.write("phase", phase="wait")
        start, since = self.clock(), None
        while True:
            if self.interrupted:
                raise sweep.Refused("Ctrl+C during the wait (nothing was paused)")
            by_id = self.read()
            self.sample(by_id)
            problems = guard.wait_problems(by_id, self.sensor)
            if problems:
                raise sweep.Refused("; ".join(problems) + " (nothing was paused)")
            if guard.vacant(by_id, self.sensor):
                if since is None:
                    since = self.clock()
                    changed = (by_id.get(guard.OCCUPANCY) or {}).get("last_changed")
                    self.record["vacancy_began_ha"] = iso_time(changed) if changed else ha_now(by_id)
                    self.occupancy_seen = ["off"]
                if self.clock() - since >= self.m1["vacancy_s"]:
                    self.last = by_id
                    return by_id
            else:
                since = None
            if self.clock() - start > self.m1["start_wait_limit_s"]:
                raise sweep.Refused("the den did not stay empty, with all four bulbs off, for "
                                    f"{self.m1['vacancy_s']:g} s within {self.m1['start_wait_limit_s']:g} s")
            self.sleep(self.m1["poll_s"])

    def pause(self, by_id):
        self.owner = guard.Owner({e: guard.scene_of_state(by_id[e]) for e in guard.LIGHTS})
        self.log.write("phase", phase="pause")
        self.save_record(phase="paused", outcome="paused")
        self.control_call("switch", "turn_off", {"entity_id": self.switch})
        by_id = self.read()
        if ((by_id.get(self.sensor) or {}).get("attributes") or {}).get("automatic") is not False:
            raise Abort("automatic did not read false after the pause")
        self.last = by_id

    def pick_exposure(self, plan):
        """All four at full and cool, auto's pick, then down by sqrt(2) until the brightest pixels fit."""
        cool = plan["temperatures"]["cool"]
        scene = model.all_on(self.m1["full_level"], cool)
        timing = self.apply(scene)
        kept, _ = self.settled_frames(timing["t_send"], held=False)
        t_pick = statistics.median(r["exposure_us"] for r, _, _ in kept)
        iso_pick = round(statistics.median(r["iso"] for r, _, _ in kept))
        limit = min(meter.clip_limits(self.m0))
        for k in range(int(self.m1["exposure_search_steps"]) + 1):
            settings = hold_for(t_pick * iso_pick / 2 ** (k / 2), iso_pick, self.m0)
            self.post_hold(settings)
            kept, _ = self.settled_frames()
            y = luma_percentile(kept[-1][1], self.m0["pixel_step"], self.m1["exposure_percentile"])
            self.log.write("exposure_try", exposure_us=settings["exposure_us"], iso=settings["iso"], y=y)
            if y < limit:
                self.save_record(held=settings)
                return settings
        raise Abort("no held exposure kept the brightest pixels below the clipping limit")

    def probe(self, plan):
        self.probing = True
        self.log.write("phase", phase="probe")
        self.save_record(phase="probing", outcome="probing")
        held = self.pick_exposure(plan)
        for step in plan["steps"]:
            if self.on_step:
                self.on_step(self, step)
            settings = held
            if step["kind"] == "confirm":
                settings = hold_for(held["exposure_us"] * held["iso"] * step["ratio"], held["iso"], self.m0)
            if settings != self.hold:
                self.post_hold(settings)
            timing = self.apply(step["scene"])
            kept, landed = self.settled_frames(timing["t_send"])
            captured = self.clock()
            self.guard_read()  # a step is clean only after a read that passed
            if step["kind"] == "all_off":
                self.check_bracket(kept[0][1])
            self.save(step, kept, timing, landed, captured)
            self.record["last_clean_step"] = step["index"]
        self.probing = False
        self.release()
        self.log.write("phase", phase="probe_done")
        self.save_record(phase="probed", outcome="probed", last_clean_step=self.last_clean)

    def restore(self, bulbs, arrival=False):
        """Write each bulb's saved normal back, still paused; check the holds; retry once; list the rest."""
        self.log.write("phase", phase="restore", bulbs=list(bulbs), arrival=arrival)
        plan = guard.restore_plan(self.snapshot, bulbs, arrival=arrival)
        expected = plan[-1]["expect"]
        failures = list(expected)
        for attempt in (1, 2):
            for action in plan[:-1]:
                if action["entity_id"] not in failures:
                    continue
                e = action["entity_id"]
                if action["do"] == "turn_on":
                    self.restore_send(e, guard.scene(True, action["brightness"], action["kelvin"]))
                elif action["do"] == "turn_off":
                    self.restore_send(e, guard.scene(False))
                elif action["do"] == "wait_on":
                    self.wait_state(e, lambda s: s is not None and s["on"])
            self.sleep(self.m1["hold_check_wait_s"])  # past den_lighting's 3 s window for a late report
            holds = self.settle_holds(expected, failures)
            failures = guard.hold_failures({e: expected[e] for e in failures}, holds,
                                           self.m1["hold_brightness_tolerance"], self.m1["hold_mired_tolerance"])
            self.log.write("hold_check", attempt=attempt, failures=failures)
            if not failures:
                break
        return [e for e in expected if e not in failures], failures

    def restore_send(self, e, target):
        data = {"entity_id": e, "transition": 0}
        if target["on"]:
            data.update(brightness=target["brightness"], color_temp_kelvin=target["kelvin"])
        self.log.write("intent", entity_id=e, service="light." + ("turn_on" if target["on"] else "turn_off"),
                       data=data, change="non-human", restore=True)
        states = self.ha_call("light", "turn_on" if target["on"] else "turn_off", data)
        context = next(((s.get("context") or {}).get("id") for s in states if s.get("entity_id") == e), None)
        self.log.write("sent", entity_id=e, context_id=context, own=True, restore=True)

    def ha_call(self, domain, service, data):
        try:
            return self.ha.call(domain, service, data)
        except HAUnreachable as exc:
            raise Abort(f"Home Assistant unreachable ({exc})", ha_lost=True) from None

    def control_call(self, domain, service, data):
        """A switch or resume call, logged like a probe write: every HA call has its intent line."""
        e = data.get("entity_id")
        self.log.write("intent", entity_id=e, service=f"{domain}.{service}", data=data, change="non-human", control=True)
        states = self.ha_call(domain, service, data)
        context = next(((s.get("context") or {}).get("id") for s in states), None)
        self.log.write("sent", entity_id=e, context_id=context, own=True, control=True)

    def wait_state(self, e, test, limit=None):
        deadline = self.clock() + (limit if limit is not None else self.m1["settle_limit_s"])
        while True:
            by_id = self.read()
            if test(guard.scene_of_state(by_id.get(e))):
                return True
            if self.clock() > deadline:
                return False
            self.sleep(min(0.2, self.m1["poll_s"]))

    def back_while_empty(self, e):
        """A bulb that only went unavailable reports on or off again, with both occupancy signals off."""
        deadline = self.clock() + self.m1["unavailable_wait_s"]
        while True:
            by_id = self.read()
            if guard.scene_of_state(by_id.get(e)) is not None and guard.occupancy_reason(by_id, self.sensor) is None:
                return True
            if self.clock() > deadline:
                return False
            self.sleep(min(0.2, self.m1["poll_s"]))

    def settle_holds(self, expected, bulbs):
        """The sensor's holds once they show the restored bulbs (or after settle_limit_s)."""
        deadline = self.clock() + self.m1["settle_limit_s"]
        while True:
            holds = ((self.read().get(self.sensor) or {}).get("attributes") or {}).get("holds") or {}
            if not guard.hold_failures({e: expected[e] for e in bulbs}, holds) or self.clock() > deadline:
                return holds
            self.sleep(min(0.2, self.m1["poll_s"]))

    def resume(self, restored):
        self.log.write("phase", phase="resume", bulbs=list(restored))
        self.control_call("switch", "turn_on", {"entity_id": self.switch})
        if restored:
            self.control_call("den_lighting", "resume_automatic", {"entity_id": list(restored)})
        deadline = self.clock() + self.m1["resume_check_s"]
        while True:
            by_id = self.read()
            attributes = (by_id.get(self.sensor) or {}).get("attributes") or {}
            holds = attributes.get("holds") or {}
            off = all((guard.scene_of_state(by_id.get(e)) or {}).get("on") is False for e in restored)
            check = {"automatic": attributes.get("automatic"), "holds_left": sorted(set(holds) & set(restored)),
                     "bulbs_off": off, "occupancy": attributes.get("occupancy")}
            if not guard.resume_problems(check) or self.clock() > deadline:
                return check
            self.sleep(min(0.5, self.m1["poll_s"]))

    def walk_through(self):
        """Occupancy unknown at resume: ask for one walk through, then confirm the bulbs go off."""
        self.say("Occupancy reads unknown, so den_lighting will send nothing and the restored bulbs could stay lit "
                 "all night. Please walk through the den once, then leave it.")
        self.log.write("phase", phase="walk_through")
        deadline, seen_on, off_at = self.clock() + self.m1["walkthrough_limit_s"], False, None
        while self.clock() <= deadline:
            by_id = self.read()
            value = ((by_id.get(self.sensor) or {}).get("attributes") or {}).get("occupancy")
            if value == "on":
                seen_on = True
            elif value == "off" and seen_on and off_at is None:
                off_at = self.clock()
            if off_at is not None and self.clock() - off_at >= self.m1["off_check_s"]:
                off = all((guard.scene_of_state(by_id.get(e)) or {}).get("on") is False for e in guard.LIGHTS)
                return {"outcome": "bulbs off after the walk-through" if off else "bulbs still lit after the walk-through",
                        "bulbs_off": off}
            self.sleep(self.m1["poll_s"])
        return {"outcome": "bulbs left lit, occupancy unknown", "bulbs_off": False}

    def delete_frames_after(self, index):
        """An abort keeps only the frames of the clean steps."""
        keep = set()
        for line in read_log(self.folder / "probes.jsonl"):
            if line.get("kind") == "capture" and line["step"] <= index:
                keep.update(f["file"] for f in line["frames"])
        removed = 0
        for path in sorted((self.folder / "frames").glob("*.nv12")):
            if "frames/" + path.name not in keep:
                path.unlink()
                removed += 1
        return removed

    def finish(self, outcome, reason=None):
        """Restore, resume and the walk-through, for a clean end or an abort that may restore."""
        by_id = self.read()  # the re-read before any restore write: a change since the last check is not overwritten
        self.sample(by_id)
        guard.abort_reason(self.last, by_id, self.owner, self.switch, self.sensor)
        self.last = by_id
        arrival = self.arrival_seen()
        bulbs = [e for e in self.owner.tools() if e not in self.owner.unavailable]
        restored, failed = self.restore(bulbs, arrival=arrival) if bulbs else ([], [])
        waiting = [e for e in self.owner.tools() if e in self.owner.unavailable]
        for e in waiting:  # back on or off while the room is empty: restore it too
            if self.back_while_empty(e):
                ok, bad = self.restore([e], arrival=arrival)
                restored += ok
                failed += bad
            else:
                failed.append(e)
        resume = self.resume(restored)
        unrestored = [[e, self.owner.latest(e)] for e in guard.LIGHTS if e in self.owner.foreign or e in failed]
        walk = None
        if resume and resume.get("occupancy") == "unknown":
            walk = self.walk_through()
        self.save_record(phase="done", outcome=outcome, abort_reason=reason, unrestored=unrestored,
                         restore={"restored": restored, "failed": failed, "arrival": arrival},
                         resume=resume, walk_through=walk, ended_pc=self.wall())
        self.log.write("phase", phase="done", outcome=outcome)
        return unrestored

    def arrival_seen(self):
        """Occupancy went straight from off to on (no unknown) since vacancy began 120 s or more before."""
        seen = [v for v in self.occupancy_seen if v is not None]
        return bool(seen) and seen[-1] == "on" and "unknown" not in seen and seen[0] == "off"

    def run(self, store_text):
        """The whole session. Returns (outcome, lines to print)."""
        previous = (signal.signal(signal.SIGINT, self.on_ctrl_c)
                    if threading.current_thread() is threading.main_thread() else None)
        try:
            plan = self.start(store_text)
            try:
                by_id = self.wait_for_vacancy()
            except (sweep.Refused, Abort) as exc:
                reason = exc.reason if isinstance(exc, Abort) else str(exc)
                self.save_record(phase="done", outcome="refused", abort_reason=reason)
                raise sweep.Refused(reason) from None
            try:
                self.pause(by_id)
                self.probe(plan)
            except Abort as abort:
                return self.abort(abort)
            except Exception as exc:  # a full disk, a bug: den_lighting must not stay paused for it
                return self.abort(Abort(f"an error in the tool ({type(exc).__name__}: {exc})"))
            try:
                unrestored = self.finish("complete")
            except Abort as stop:  # session.json still reads "probed": --recover knows the data is complete
                return "stopped", [f"STOPPED while restoring: {stop.reason}. The data is complete, but den_lighting "
                                   f"stays paused. Once Home Assistant answers, run probe.py --recover {self.folder}"]
            return "complete", self.result_lines(unrestored)
        finally:
            if self.log:
                self.log.close()
            if previous is not None:
                signal.signal(signal.SIGINT, previous)

    def abort(self, abort):
        """The plan's abort order."""
        self.probing = False
        self.release()
        self.log.write("guard", event="abort", reason=abort.reason)
        removed = self.delete_frames_after(self.last_clean)
        self.save_record(outcome="aborting", abort_reason=abort.reason, last_clean_step=self.last_clean,
                         frames_deleted_after_abort=removed)
        if abort.ha_lost:
            self.save_record(phase="ha_lost", outcome="aborted_ha_lost")
            return "aborted", [f"ABORTED: {abort.reason}. den_lighting stays paused. Once Home Assistant answers, run "
                               f"probe.py --recover {self.folder}"]
        if abort.game:
            self.save_record(phase="game", outcome="aborted_game")
            return "aborted", [f"ABORTED: {abort.reason}. den_lighting stays paused; after the game, run "
                               f"probe.py --recover {self.folder}"]
        try:
            unrestored = self.finish("aborted", abort.reason)
        except Abort as again:
            self.save_record(phase="ha_lost" if again.ha_lost else "restore_failed", outcome="aborted_ha_lost"
                             if again.ha_lost else "aborted", abort_reason=f"{abort.reason}; then {again.reason}")
            return "aborted", [f"ABORTED: {abort.reason}; then {again.reason}. Run probe.py --recover {self.folder}"]
        return "aborted", [f"ABORTED: {abort.reason}."] + self.result_lines(unrestored)

    def result_lines(self, unrestored):
        lines = [guard.unrestored_line(e, scene) for e, scene in unrestored]
        walk = self.record.get("walk_through")
        if walk:
            lines.append(walk["outcome"])
        problems = guard.resume_problems(self.record.get("resume") or {"automatic": True})
        if problems:
            lines.append("den_lighting did not read back as resumed: " + "; ".join(problems) + ". Tell the agent.")
        return lines or ["Every bulb was restored and den_lighting resumed."]

    # --recover --------------------------------------------------------------------------------
    def recover(self):
        """Finish an interrupted or aborted session from session.json and probes.jsonl."""
        try:
            self.record = json.loads((self.folder / "session.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise sweep.Refused("no readable session.json in the folder") from None
        if self.record.get("phase") == "start":  # stopped during the wait: nothing was paused or written
            self.save_record(phase="done", outcome="refused", abort_reason="stopped before the pause")
            raise sweep.Refused("the session stopped before the pause, so there is nothing to recover; "
                                "start a new session with a new id")
        if self.record.get("phase") == "done":
            walk = self.record.get("walk_through") or {}
            if walk.get("outcome") != "bulbs left lit, occupancy unknown":
                raise sweep.Refused("the session already finished")
            self.switch, self.sensor = self.record["switch"], self.record["sensor"]
            self.log = ProbeLog(self.folder / "probes.jsonl", self.wall)
            try:
                self.log.write("phase", phase="recover_walk_through")
                walk = self.walk_through()
                self.save_record(walk_through=walk)
            except Abort as exc:
                raise sweep.Refused(f"the walk-through check stopped: {exc.reason}") from None
            finally:
                self.log.close()
            return self.record["outcome"], [walk["outcome"]]
        lines = read_log(self.folder / "probes.jsonl")
        self.snapshot, self.switch, self.sensor = self.record["snapshot"], self.record["switch"], self.record["sensor"]
        self.owner = rebuild_owner(lines, self.record)
        self.log = ProbeLog(self.folder / "probes.jsonl", self.wall)
        self.log.write("phase", phase="recover")
        try:
            by_id = self.ha.states()
        except HAUnreachable:
            raise sweep.Refused("Home Assistant is still unreachable; nothing was changed") from None
        attributes = (by_id.get(self.sensor) or {}).get("attributes") or {}
        if attributes.get("gaming_session"):
            raise sweep.Refused("a game is still on; run --recover after it ends")
        for e in guard.LIGHTS:
            s = guard.scene_of_state(by_id.get(e))
            if s is None:
                self.owner.unavailable.add(e)
            elif e not in self.owner.foreign and not self.owner.is_own(e, s):
                self.owner.foreign.add(e)
        self.last = by_id
        arrival = False
        if attributes.get("occupancy") == "on":
            arrival = self.arrival_from_history(lines) == "turn_on_only"
        self.occupancy_seen = ["off", "on"] if arrival else ["unknown"]
        if self.record.get("phase") == "probed":  # the data was complete; only the restore was lost
            outcome, reason = "complete", None
        else:
            outcome = "aborted"
            reason = self.record.get("abort_reason") or "interrupted (killed)"
            steps = [l["index"] for l in lines if l.get("kind") == "step"]
            self.delete_frames_after(max(steps, default=0))
        try:
            unrestored = self.finish(outcome, reason)
        except Abort as stop:
            return "stopped", [f"STOPPED: {stop.reason}. den_lighting stays paused. Once Home Assistant answers, run "
                               f"probe.py --recover {self.folder} again."]
        finally:
            self.log.close()
        return outcome, ["Recovered."] + self.result_lines(unrestored)

    def arrival_from_history(self, lines):
        samples = [l for l in lines if l.get("kind") == "sample" and l.get("ha_time") is not None]
        if not samples or self.record.get("vacancy_began_ha") is None:
            return "on_then_off"
        from datetime import datetime, timezone
        start = samples[-1]["ha_time"]
        start_iso = datetime.fromtimestamp(start, timezone.utc).isoformat()
        try:
            raw = self.ha.history(start_iso, [guard.OCCUPANCY, self.sensor])
            book = self.ha.logbook(start_iso)
        except HAUnreachable:
            return "on_then_off"
        history = {}
        for entries in raw or []:
            if not entries:
                continue
            e = entries[0]["entity_id"]
            key = "occupancy" if e == self.sensor else e
            history[key] = [(iso_time(x["last_changed"] if e != self.sensor else x["last_updated"]),
                             x["state"] if e != self.sensor else (x.get("attributes") or {}).get("occupancy"))
                            for x in entries]
        for signal_name in ("occupancy", guard.OCCUPANCY):
            history.setdefault(signal_name, [])
        starts = [iso_time(x["when"]) for x in book or []
                  if x.get("domain") == "homeassistant" and x.get("message") == "started"
                  or x.get("event_type") == "homeassistant_start"]
        return guard.arrival_branch(history, starts, start, self.record["vacancy_began_ha"])


def rebuild_owner(lines, record):
    initial = {e: (record.get("start_readings") or {}).get(e) or guard.scene(False) for e in guard.LIGHTS}
    owner = guard.Owner(initial)
    pending = {}
    for line in lines:
        kind, e = line.get("kind"), line.get("entity_id")
        if line.get("control"):  # the switch and resume calls: no bulb's state follows from them
            continue
        if kind == "intent":
            data = line["data"]
            target = (guard.scene(True, data.get("brightness"), data.get("color_temp_kelvin"))
                      if line["service"] == "light.turn_on" else guard.scene(False))
            if line.get("restore"):  # an earlier restore that stopped part-way: this bulb may read it now
                owner.restoring[e].append(target)
                continue
            owner.intend(e, target)
            pending[e] = True
        elif kind == "sent" and not line.get("restore") and pending.pop(e, False):
            owner.responded(e, line.get("context_id"))
        elif kind == "observed" and line.get("owner") == "foreign":
            owner.foreign.add(e)
    return owner


def hold_for(x_target, iso_pick, method0):
    """Held settings for t·G = x_target (µs·ISO): time changes, not gain, unless time would pass the limit."""
    t = x_target / iso_pick
    iso = iso_pick
    if round(t) > method0["max_exposure_us"]:
        iso = round(x_target / method0["max_exposure_us"])
        t = method0["max_exposure_us"]
    t = max(1, round(t))
    if iso > method0["iso_max"]:
        raise Abort("the held exposure needs more ISO than the camera allows (too dark)")
    return {"exposure_us": t, "iso": iso}


def luma_percentile(nv12, step, q):
    """The q-th percentile of Y (BT.601 luma of the decoded codes) over the sampled pixels."""
    rs, gs, bs = meter.decode(nv12, step)
    ys = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in zip(rs, gs, bs)]
    return model.percentile(ys, q)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", metavar="SESSION_FOLDER")
    group.add_argument("--recover", metavar="SESSION_FOLDER")
    parser.add_argument("--ha", required=True, help="http://<ha-host>:8123")
    parser.add_argument("--camera", required=True, help="http://<jetson-address>:8090, or the tunnel")
    parser.add_argument("--stage0", help="the passing Stage 0 sweep folder (for --session)")
    parser.add_argument("--store-copy", default=DEFAULT_STORE_COPY)
    parser.add_argument("--ha-token-file", default=DEFAULT_HA_TOKEN)
    parser.add_argument("--camera-token-file", default=sweep.DEFAULT_TOKEN_FILE)
    args = parser.parse_args(argv)
    try:
        method1, method0 = model.load_stage1(), meter.load_method()
        tokens = (args.ha_token_file, args.camera_token_file)
        folder = session_folder(args.session or args.recover, tokens, new=bool(args.session))
        ha = HA(args.ha, read_ha_token(args.ha_token_file), method1["ha_timeout_s"])
        camera = sweep.Camera(args.camera, sweep.read_token(args.camera_token_file))
        session = Session(folder, ha, camera, method1, method0)
        if args.recover:
            outcome, lines = session.recover()
        else:
            if not args.stage0:
                raise sweep.Refused("--session needs --stage0, the passing Stage 0 sweep folder")
            norm, source = stage0_meter_norm(args.stage0)
            session.stage0 = {"sweep": Path(args.stage0).name, "file": source,
                              "meter_norm_sha256": norm.get("parameters_sha256")}
            method1.refuse_if_open("A probe session")
            try:
                store_text = Path(args.store_copy).read_bytes().decode("utf-8-sig")
            except (OSError, UnicodeDecodeError):
                raise sweep.Refused("cannot read the store copy (README, Stage 1 step 2)") from None
            folder.mkdir(exist_ok=True)
            outcome, lines = session.run(store_text)
    except (sweep.Refused, model.Refused, guard.Refused) as exc:
        print(f"Refused: {exc}. This command changed nothing.", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    print(f"Outcome: {outcome}. Wrote {folder / 'session.json'}")
    return 0 if outcome == "complete" else 1


def read_ha_token(path):
    try:
        token = Path(path).read_bytes().decode("utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        raise sweep.Refused("cannot read the Home Assistant token file (README, Stage 1 step 1)") from None
    if len(token) < 24 or not all(33 <= ord(ch) < 127 for ch in token):
        raise sweep.Refused("the HA token file must hold one token of at least 24 printable characters")
    return token


if __name__ == "__main__":
    sys.exit(main())
