"""den-core need v0: Qwen scene facts to a room-level PersonState.

What v0 is: a room-level stand-in for the architecture's PersonState (K7, in
place of the per-person estimator N2). It turns la-fact-log-1 lines, carried
as den-event-1 `facts` events, into person_state/v1 records for one anonymous
person with the constant track id 'room'. It fills focused_engagement for
reading, posture, and the activity word behind visual_demand. It makes no
lighting judgement.

What it is not: no tracks, identity or multi-person handling; no lux or light
model; no visual_demand level (demand_gates is parked config, unused); no
working region or attention; no second dwell preset.

Reading rule, the look test's (as in tools/reading-shadow): playing = a valid
answer with people_visible one and activity_hypothesis reading; idle = any
other valid answer (two people reading is idle); unknown = no answer, an
invalid answer, or people_visible / activity_hypothesis unclear.

On/off timing: an exact port of den_lighting's gaming dwell
(policy.Controller.gaming_sample, gaming always enabled), so the timing
reading-shadow-v0 scores is the timing this module emits. A sample's capture
time is its observed_at; the clock is the event's t (the line's available_at).

How it plugs in: a den-core policy, make_policy(config) and handle(event).
After the spine merges, run run.py --policies need,incumbent,decide.
Standard library only; it reads no clock, randomness, file, environment or
network, and imports no other den-core module.
"""
import copy
from datetime import datetime, timezone

VERSION = "need-v0"
DEFAULT_CONFIG = {
    "version": VERSION,
    "onset_protocol": {
        "value": "contact-starting",
        "status": "owner answer, choices.md item 3",
        "effect": "starting at the first playing sample; onset_ts is its capture time; since_s counts from onset",
    },
    "dwell": {
        "value": "den_lighting",
        "status": "parity with the incumbent and reading-shadow-v0",
        "entry_samples": 2, "exit_idle_s": 30, "sample_ttl_s": 45, "future_tolerance_s": 5,
        "note": "A7 5 s entry dwell, kept by the owner onset answer, is not implemented in v0",
    },
    "demand_gates": {
        "value": {"fine": 0.6, "casual": 0.4},
        "status": "owner seed, choices.md item 5",
        "used": False,
        "note": "v0 never turns activity words into a visual_demand level",
    },
}
# Qwen's subject_posture words to PersonState postures; anything else is unknown.
POSTURE = {"sitting": "sitting", "standing": "standing", "lying": "lying", "walking": "standing"}


def parse_iso(text):
    """Epoch seconds from la-fact-log-1's ISO-8601 UTC with milliseconds and 'Z'."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()


def sample(line):
    """One reading sample from one la-fact-log-1 line (a dict): status playing / idle / unknown."""
    observation = line["observation"]
    facts = None
    if (line["outcome"] == "completed" and isinstance(observation, dict) and observation.get("status") == "valid"
            and isinstance(observation.get("facts"), dict)):
        facts = observation["facts"]
    if facts is None or "unclear" in (facts.get("people_visible"), facts.get("activity_hypothesis")):
        status = "unknown"
    elif facts.get("people_visible") == "one" and facts.get("activity_hypothesis") == "reading":
        status = "playing"
    else:
        status = "idle"
    return {"status": status, "answered": facts is not None, "observed_at": parse_iso(line["observed_at"]),
            "available_at": parse_iso(line["available_at"]), "request_id": line["request_id"], "facts": facts}


def _merge(base, override):
    """A deep copy of base with override's keys merged in, dicts recursively."""
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _changes(record):
    """The fields whose change emits a record."""
    return (record["focused_engagement"]["state"], record["posture"], record["visual_demand"]["basis"],
            record["field_support"]["focused_engagement"]["support_status"], record["flags"])


class Need:
    id = "need"
    version = VERSION
    code_path = __file__

    def __init__(self, config):
        self.config = config
        dwell = config["dwell"]
        self.entry_samples, self.exit_idle_s = dwell["entry_samples"], dwell["exit_idle_s"]
        self.ttl_s, self.future_s = dwell["sample_ttl_s"], dwell["future_tolerance_s"]
        # The dwell, named as in den_lighting's Controller (engaged is its gaming).
        self.last_sample, self.playing, self.idle_since = None, 0, None
        self.engaged, self.onset_ts = False, None
        self.support = None  # the latest fresh, in-order, answered sample
        self.state, self.value_set_ts = "none", None
        self.last = None  # _changes() of the last record emitted

    def handle(self, event):
        t = event["t"]
        if event["kind"] == "facts":
            self._dwell(sample(event["data"]), t)
            state = self._state()
            if state == "none":
                self.onset_ts = None
            if state != self.state or self.value_set_ts is None:
                self.state, self.value_set_ts = state, t
        elif event["kind"] != "tick" or self.last is None:
            return []  # a tick only re-checks support, once a record exists
        record = self._record(t)
        if _changes(record) == self.last:
            return []
        self.last = _changes(record)
        return [{"kind": "person_state", "data": record}]

    def _dwell(self, s, now):
        """den_lighting policy.Controller.gaming_sample, step for step."""
        captured = s["observed_at"]
        if not -self.future_s <= now - captured <= self.ttl_s:
            self.playing, self.idle_since = 0, None
            return
        if self.last_sample is not None and captured <= self.last_sample:
            return
        if self.last_sample is None or captured - self.last_sample > self.ttl_s:
            self.playing, self.idle_since = 0, None
        self.last_sample = captured
        if s["answered"]:
            self.support = s
        if s["status"] == "playing":
            self.playing += 1
            self.idle_since = None
            if self.playing == 1 and not self.engaged:
                self.onset_ts = captured
            if not self.engaged and self.playing >= self.entry_samples:
                self.engaged = True
        elif s["status"] == "idle":
            self.playing = 0
            if self.idle_since is None:
                self.idle_since = captured
            if self.engaged and captured - self.idle_since >= self.exit_idle_s:
                self.engaged = False
        else:
            self.playing, self.idle_since = 0, None

    def _state(self):
        if self.engaged:
            return "engaged" if self.idle_since is None else "ending"
        return "starting" if self.playing >= 1 else "none"

    def _record(self, t):
        s = self.support
        facts = s["facts"] if s else {}
        captured = s["observed_at"] if s else None
        observed = captured is not None and t - captured <= self.ttl_s
        activity = facts.get("activity_hypothesis")
        return {
            "schema_version": "person_state/v1",
            "state_id": "room@%.3f" % t,
            "emitted_ts": t,
            "track_id": "room",
            "identity": "unbound",
            "focused_engagement": {
                "state": self.state, "onset_ts": self.onset_ts, "confidence": None, "kind": "reading",
                "since_s": None if self.onset_ts is None else round(t - self.onset_ts, 3)},
            "visual_demand": {"level": None, "basis": None if activity is None else "qwen:" + activity,
                              "confidence": None},
            "posture": POSTURE.get(facts.get("subject_posture"), "unknown"),
            "field_support": {"focused_engagement": {
                "last_support_capture_ts": captured, "value_set_ts": self.value_set_ts,
                "support_status": "observed" if observed else "expired"}},
            "sources": {"focused_engagement": [{"origin": "qwen", "ref": s["request_id"]}] if s else []},
            "interpreter": {"path": "rules", "version": self.version},
            "flags": [] if observed else ["stale:qwen"],
        }


def make_policy(config=None):
    """The need policy, with DEFAULT_CONFIG deep-merged with config."""
    return Need(_merge(DEFAULT_CONFIG, config))
