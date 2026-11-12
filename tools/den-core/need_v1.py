"""den-core need v1: need v0 for any close-up task, with the phone patience as a setting (coordinator plan C2).

need.py (v0) stays untouched, so every old replay keeps its pinned hash. v1 changes three things:
- a `playing` sample is one person visible and the activity in CLOSE_UP (reading, plus den-live-scene-2's
  `hobby`, and the context fact schema's `desk_hobby`), not reading alone;
- the record's focused_engagement.kind is the latest playing sample's activity ("reading" or "hobby");
- the exit after `idle` answers is config["phone_patience_s"] (default 30, the live goals' number), and an
  `unknown` answer no longer restarts that clock (Codex 15: phone, unclear, phone could hold the spot forever).
  An unknown still breaks a run of playing samples, as in v0.

Standard library only. Pure.
"""
import need

VERSION = "need-v1"
CLOSE_UP = ("reading", "hobby", "desk_hobby", "craft")  # craft: in case den-live-scene-2 says it as an activity
KIND = {"reading": "reading", "hobby": "hobby", "desk_hobby": "hobby", "craft": "hobby"}
DEFAULT_CONFIG = need._merge(need.DEFAULT_CONFIG, {"version": VERSION, "phone_patience_s": 30})


def sample(line):
    """need v0's sample, with any CLOSE_UP activity counting as playing."""
    s = need.sample(line)
    facts = s["facts"]
    if facts and s["status"] == "idle" and facts.get("people_visible") == "one" and facts.get("activity_hypothesis") in CLOSE_UP:
        s["status"] = "playing"
    return s


class NeedV1(need.Need):
    version = VERSION
    code_path = __file__

    def __init__(self, config):
        super().__init__(config)
        self.exit_idle_s = config["phone_patience_s"]
        self.kind = "reading"

    def handle(self, event):
        if event["kind"] != "facts":
            return super().handle(event)
        t = event["t"]
        s = sample(event["data"])
        if s["status"] == "playing":
            self.kind = KIND.get(s["facts"].get("activity_hypothesis"), "reading")
        self._dwell(s, t)
        state = self._state()
        if state == "none":
            self.onset_ts = None
        if state != self.state or self.value_set_ts is None:
            self.state, self.value_set_ts = state, t
        record = self._record(t)
        if need._changes(record) == self.last:
            return []
        self.last = need._changes(record)
        return [{"kind": "person_state", "data": record}]

    def _dwell(self, s, now):
        if s["status"] == "unknown" and self.engaged and self.idle_since is not None:
            keep = self.idle_since  # an unclear answer mid-exit doesn't restart the patience clock
            super()._dwell(s, now)
            if self.last_sample == s["observed_at"]:
                self.idle_since = keep
            return
        super()._dwell(s, now)

    def _record(self, t):
        record = super()._record(t)
        record["focused_engagement"]["kind"] = self.kind
        return record


def make_policy(config=None):
    return NeedV1(need._merge(DEFAULT_CONFIG, config))
