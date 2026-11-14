"""The desk switch (plan 05 §3.6, step 05-4): one private file sends one fixture's commands to a real board.

desk.json (schema den-c12-desk-1) reaches the service as the `desk.json` systemd credential, root-only on LA. Without
it den-live is virtual only, exactly as before: no sender, no Home Assistant write client, and the trace's run header
says `desk: null`. With it:
- **One fixture.** One registered fixture is mapped to its board's Home Assistant device (`fixture_id`, `device`).
- **The measured profile.** That fixture's profile is the board's own (`profile`, in den-core's profile shape, with
  `fictional: false`), in place of the fictional one. So den-live's move timing and range checks match the real head,
  and the run header's profile_sha256 names it.
- **light false is the dark rehearsal.** Every move goes with its spot at level 0, and no keep-lit goes. Home still
  shows the virtual spot.
- **Readback (optional).** `readback` names the board's Home Assistant entities, read every second. Their values are
  the snapshot's `devices` key, a device row in the trace when they change, and the Stop copy's latch read.
The room geometry stays `physical_commands: false`: the desk mapping is a separate setting.

attach() goes on a Preview after it's built and before its first trace row, without changing preview.py: it wraps
the trace's own storage in one lock (the preview's writer and the sender's lanes share the day files), hands each
fsynced row to the sender, and adds `devices` to every snapshot the preview publishes.
"""
import collections
import json
import math
import re
import threading
from pathlib import Path

import c12_send
import ha_link
import preview_trace


SCHEMA = "den-c12-desk-1"
FILE = "desk.json"
KEYS = {"schema", "fixture_id", "device", "light", "profile"}
READBACK = ("state", "controller", "stop_latched", "last_seq", "pan_cdeg", "tilt_cdeg", "fault_code")
NUMBERS = ("last_seq", "pan_cdeg", "tilt_cdeg", "fault_code")
# den-core's profile shape (scurve.FICTIONAL_PROFILE's keys): the measured one must have exactly these.
PROFILE_KEYS = {"label", "fictional", "axes", "range", "lit_motion", "dim_motion", "retarget_mid_move",
                "path_margin_cdeg", "settle_s", "beam_fwhm_deg", "lumens"}
DEVICE = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
ENTITY = re.compile(r"[a-z_]+\.[a-z0-9_]+\Z")
WORD = re.compile(r"[a-z0-9_]{1,32}\Z")


class DeskError(ValueError):
    """A short sentence: never the file's text."""


def _finite(value, low=-math.inf, high=math.inf):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def check_profile(profile):
    if not isinstance(profile, dict) or set(profile) != PROFILE_KEYS or profile["fictional"] is not False:
        raise DeskError("the desk needs the board's measured profile, in den-core's shape with fictional false")
    axes, ranges = profile["axes"], profile["range"]
    if (not isinstance(profile["label"], str) or not profile["label"].strip()
            or not isinstance(axes, list) or len(axes) != 2
            or not all(isinstance(a, dict) and set(a) == {"v_max", "a_max", "j_max"}
                       and all(_finite(v, 1e-9) for v in a.values()) for a in axes)
            or not isinstance(ranges, dict) or set(ranges) != {"pan", "tilt"}
            or not all(isinstance(ranges[k], list) and len(ranges[k]) == 2 and all(_finite(v, -m, m) for v in ranges[k])
                       and ranges[k][0] < ranges[k][1] for k, m in (("pan", 180), ("tilt", 100)))
            or not all(type(profile[k]) is bool for k in ("lit_motion", "dim_motion", "retarget_mid_move"))
            or type(profile["path_margin_cdeg"]) is not int or profile["path_margin_cdeg"] < 0
            or not _finite(profile["settle_s"], 0) or not _finite(profile["beam_fwhm_deg"], 1e-9)
            or not _finite(profile["lumens"], 1e-9)):
        raise DeskError("the desk's measured profile has a missing or impossible value")
    try:
        json.dumps(profile, allow_nan=False)
    except ValueError:
        raise DeskError("the desk's measured profile has a missing or impossible value") from None


def parse(data, fixtures):
    """The checked mapping, from desk.json's content and the registered fixtures."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise DeskError("the desk file isn't %s" % SCHEMA)
    if not KEYS <= set(data) <= KEYS | {"readback"}:
        raise DeskError("the desk file has a missing or unknown key")
    if data["fixture_id"] not in fixtures:
        raise DeskError("the desk file maps a fixture the room doesn't register")
    if not isinstance(data["device"], str) or not DEVICE.match(data["device"]):
        raise DeskError("the desk file's device isn't a Home Assistant device reference")
    if type(data["light"]) is not bool:
        raise DeskError("the desk file's light must be true or false")
    check_profile(data["profile"])
    readback = data.get("readback") or {}
    if (not isinstance(readback, dict) or not set(readback) <= set(READBACK)
            or not all(isinstance(e, str) and ENTITY.match(e) for e in readback.values())
            or len(set(readback.values())) != len(readback)):
        raise DeskError("the desk file's readback must name Home Assistant entities, each once")
    return {"fixture_id": data["fixture_id"], "device": data["device"], "light": data["light"],
            "profile": data["profile"], "readback": dict(readback)}


def load(folder, fixtures):
    """The desk mapping, or None when the folder (the service's credentials) holds no desk.json: virtual only. A desk
    file that's there but doesn't check raises DeskError: the coordinator then stays off rather than run virtual by
    mistake."""
    if folder is None:
        return None
    path = Path(folder) / FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DeskError("the desk file doesn't read") from None
    return parse(data, fixtures)


def readback_value(field, state):
    """One entity's state as the board reported it: None when unknown."""
    raw = state.get("state") if isinstance(state, dict) else None
    if not isinstance(raw, str) or raw in ("unknown", "unavailable"):
        return None
    if field == "stop_latched":
        return {"on": True, "off": False}.get(raw)
    if field in NUMBERS:
        try:
            number = float(raw)
        except ValueError:
            return None
        return int(number) if math.isfinite(number) and number == int(number) else None
    return raw if WORD.match(raw) else None


class Desk:
    """The desk mapping on a running Preview: the sender, its storage, and the board's readback."""

    def __init__(self, preview, desk, client_factory, *, threaded=True, poll_s=ha_link.POLL_S):
        trace = preview.trace
        if trace.ordinal:
            raise DeskError("the desk must be set before the trace's first row")
        self.preview, self.desk = preview, desk
        self.fixture, self.device = desk["fixture_id"], desk["device"]
        mapping = {"fixture_id": self.fixture, "device": self.device, "light": desk["light"]}
        if not preview_trace.valid_desk(mapping):
            raise DeskError("the desk mapping isn't one")
        trace.header["desk"] = mapping
        lock, store = threading.Lock(), trace.append

        def locked(row):   # the preview's writer and the sender's lanes write the same day files
            with lock:
                store(row)
        self.store = locked
        self.released = collections.deque(maxlen=64)
        self.sender = c12_send.Sender({self.fixture: self.device}, client_factory, record=locked,
                                      on_release=self._released, on_board_stop=c12_send.copy_stop_into(preview),
                                      clock=preview.clock, threaded=threaded, light=desk["light"])
        trace.append = self.sender.after(locked)
        self.values, self.read_mono = None, None
        self.poller = None
        if desk["readback"]:
            self.poller = ha_link.Poller(client_factory(), list(desk["readback"].values()), self.read,
                                         poll_s=poll_s, clock=preview.clock)
        connect = preview.connect
        preview.connect = lambda publish: connect(self.publishing(publish))
        preview.publish = self.publishing(preview.publish)

    def start(self):
        self.sender.start()
        if self.poller is not None and self.sender.threaded:
            self.poller.start()
        return self

    def close(self):
        if self.poller is not None:
            self.poller.halt.set()
        self.sender.close()

    def _released(self, fixture_id, trace_id, reason):
        """A refused or expired move, or two failed keep-lits. Until the preview takes a release hook with a hold-off
        (plan 05, 05-2's note for the wiring step), it's kept here and in the send rows: the board's own refusal or
        lease already keeps it dark."""
        self.released.append({"fixture_id": fixture_id, "trace_id": trace_id, "reason": reason})

    def read(self, snapshot):
        """One readback poll: {entity: state or None}."""
        values = {field: readback_value(field, snapshot.get(entity)) for field, entity in self.desk["readback"].items()}
        now = self.preview.clock()
        changed = values != self.values
        self.values, self.read_mono = values, now
        if changed:
            self.store({"schema": preview_trace.SCHEMA, "kind": "device", "run_id": self.preview.trace.epoch,
                        "fixture_id": self.fixture, "device": self.device, "at_mono": now, "values": dict(values)})
        self.sender.observe(self.fixture, values.get("stop_latched"))

    def devices(self):
        problem = "no readback" if self.poller is None else self.poller.problem
        return {self.fixture: {"device": self.device, "light": self.desk["light"], "read_mono": self.read_mono,
                               "values": self.values, "problem": problem, "sent": self.sender.counts["acked"],
                               "released": len(self.released)}}

    def publishing(self, publish):
        def with_devices(snapshot):
            if isinstance(snapshot, dict):
                snapshot["devices"] = self.devices()   # additive: the preview's own keys are unchanged
            publish(snapshot)
        return with_devices


def attach(preview, desk, fixed, *, threaded=True):
    """The desk on a built Preview, before its start(): its sender calls the den home's Home Assistant through the asks
    block's link (fixed: ha_url, token and an optional ha_ca_file), one persistent connection per lane."""
    def session():
        return ha_link.HaSession(fixed["ha_url"], fixed["token"], cafile=fixed.get("ha_ca_file"))
    return Desk(preview, desk, session, threaded=threaded).start()
