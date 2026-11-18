"""What the coordinator learns from the owner's own changes to the den's fixtures (plan 04b), for each situation key
(den-core's situation: `task/evening`, `sitting/day`, ...).

den-core `learn`'s rule, on den-live's quantities. Each correction moves what's learned halfway from what was there
before (what was learned, or else what the coordinator showed) toward what the owner set. Warmth moves halfway in
mireds. Off flips only after two corrections the same way in a row. Going back and forth settles in between.
- The task spot: its light, as a scale on den-core's SPOT_TARGET (the lux the owner's level gives at the page, in
  den-core's light model, so it carries across fixtures and throws); its aim offset from the page in room metres; and
  whether there's a spot at all.
- The room's glow: one level and one warmth per situation (Home draws it on every fixture's glow zones and downlight).

den-core's `Learner` keeps 0-255 integers for each bulb, which can't hold a lux scale or an offset, so this store is
den-live's own. test_fixture_learning holds it to `Learner` on level, warmth and on/off.

Standard library only. Pure: what's learned is replayed from manual.Log's lines.
"""

import math

OFFSET_MAX_M = 0.30          # a move of the aim further than this from the page isn't a correction of it
LUX_SCALE = (0.25, 4.0)      # the task light's learned range around den-core's SPOT_TARGET


def halfway(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a + (b - a) / 2.0


def halfway_kelvin(a, b):
    """Halfway in mireds, as den-core learn does, to the nearest kelvin."""
    if a is None or b is None:
        return b if a is None else a
    return int(round(1e6 / ((1e6 / a + 1e6 / b) / 2.0)))


def _vote(prior, after_off):
    """den-core's on/off rule: (off, pending) after a correction that set off to after_off (None: not a toggle)."""
    off, pending = prior.get("off", False), None
    if after_off is not None and after_off != off:
        if prior.get("pending") == after_off:
            off = after_off   # the second change in a row the same way
        else:
            pending = after_off
    return off, pending


PRIORS_KEPT = 512   # the holds whose first change can still be superseded (a hold lasts minutes, not hundreds of changes)


def _scale(value):
    low, high = LUX_SCALE
    return min(high, max(low, value))


class Learning:
    def __init__(self):
        self.entries = {}   # {key: {"spot": {...}, "room": {...}}}
        self.priors = {}    # {(event_id, part): (key, the part's row before that event)}: what a hold's later changes redo
        self.skipped = 0    # replayed lines that couldn't be applied

    def apply(self, key, learn, at_ms, event_id=None, supersedes=None, keys=None):
        """One event's learning inputs (see Preview._learning_inputs): {"spot": {lux_scale, lux_before, offset_m,
        off}, "room": {level, level_before, cct_k, cct_before}}, either part optional. keys: a part's own situation
        key, where it isn't `key`.

        A hand hold is one correction, however many changes it takes: supersedes {part: the hold's first event_id}
        puts the part back as it was before that first change and learns the hold's latest inputs (merged by the
        preview) in one halfway step, so "dim, then dim again, then off" counts once, and replay is the same."""
        changed = {}
        for part in ("spot", "room"):
            values = learn.get(part)
            if not values:
                continue
            part_key = (keys or {}).get(part, key)
            origin = (supersedes or {}).get(part)
            if origin is not None and (origin, part) in self.priors:
                part_key, before = self.priors[(origin, part)]
                entry = self.entries.setdefault(part_key, {})
                if before is None:
                    entry.pop(part, None)
                else:
                    entry[part] = dict(before)
            else:
                if event_id is not None:
                    row = self.entries.get(part_key, {}).get(part)
                    self.priors[(event_id, part)] = (part_key, None if row is None else dict(row))
                    while len(self.priors) > PRIORS_KEPT:
                        self.priors.pop(next(iter(self.priors)))
            entry = self.entries.setdefault(part_key, {})
            changed[part] = entry[part] = (self._spot if part == "spot" else self._room)(entry.get(part), values, at_ms)
        return changed

    @staticmethod
    def _spot(prior, spot, at_ms):
        prior = prior or {"lux_scale": None, "offset_m": None, "off": False, "pending": None, "n": 0}
        off, pending = _vote(prior, spot.get("off"))
        new = {"off": off, "pending": pending, "n": prior["n"] + 1, "last_ms": at_ms,
               "lux_scale": prior.get("lux_scale"), "offset_m": prior.get("offset_m")}
        if spot.get("lux_scale") is not None:
            # From what was learned, or else from what the coordinator gave (its own scale, in range). A correction
            # with no brightness (an aim, or off) teaches no light.
            start = prior.get("lux_scale")
            if start is None:
                start = _scale(spot.get("lux_before") or 1.0)
            new["lux_scale"] = _scale(halfway(start, spot["lux_scale"]))
        if spot.get("offset_m") is not None:
            was = prior.get("offset_m") or [0.0, 0.0, 0.0]
            new["offset_m"] = [halfway(a, b) for a, b in zip(was, spot["offset_m"])]
        return new

    @staticmethod
    def _room(prior, room, at_ms):
        prior = prior or {"level": room.get("level_before"), "cct_k": room.get("cct_before"), "n": 0}
        return {"n": prior["n"] + 1, "last_ms": at_ms,
                "level": min(1.0, max(0.0, halfway(prior.get("level"), room.get("level"))))
                if room.get("level") is not None or prior.get("level") is not None else None,
                "cct_k": halfway_kelvin(prior.get("cct_k"), room.get("cct_k"))}

    def spot(self, key):
        """The task spot's learned values for this situation, or None."""
        row = (self.entries.get(key) or {}).get("spot")
        if not row:
            return None
        return {"lux_scale": row.get("lux_scale"), "offset_m": row.get("offset_m"), "off": row["off"], "n": row["n"]}

    def room(self, key):
        """The room glow's learned values for this situation, or None."""
        row = (self.entries.get(key) or {}).get("room")
        if not row or row.get("level") is None:
            return None
        return {"level": row["level"], "cct_k": row.get("cct_k"), "n": row["n"]}

    def public(self):
        """For /coordinator and the panel's "learned from you" line: rounded, no pending votes."""
        out = {}
        for key, entry in sorted(self.entries.items()):
            row = {}
            spot = entry.get("spot")
            if spot:
                row["spot"] = {"lux_scale": None if spot.get("lux_scale") is None else round(spot["lux_scale"], 3),
                               "offset_m": None if spot.get("offset_m") is None
                               else [round(x, 3) for x in spot["offset_m"]],
                               "off": spot["off"], "n": spot["n"], "last_ms": spot["last_ms"]}
            room = entry.get("room")
            if room and room.get("level") is not None:
                row["room"] = {"level": round(room["level"], 3), "cct_k": room.get("cct_k"), "n": room["n"],
                               "last_ms": room["last_ms"]}
            if row:
                out[key] = row
        return out

    @classmethod
    def replay(cls, lines):
        """What the log's lines teach, in order (manual.Log.read())."""
        learning = cls()
        for line in lines:
            if line.get("learn") and isinstance(line.get("key"), str):
                try:
                    learning.apply(line["key"], line["learn"], line["event"].get("at_ms"),
                                   line["event"].get("event_id"), line.get("supersedes"), line.get("keys"))
                except (AttributeError, KeyError, TypeError, ValueError):
                    learning.skipped += 1   # a line that parses but can't be applied never stops the coordinator
        return learning


def offset(point, page):
    """The owner's aim point relative to the page, or None when it's further than OFFSET_MAX_M: that's pointing the
    light at something else, not a correction of its aim."""
    if point is None or page is None:
        return None
    delta = [point[i] - page[i] for i in range(3)]
    return delta if math.sqrt(sum(d * d for d in delta)) <= OFFSET_MAX_M else None
