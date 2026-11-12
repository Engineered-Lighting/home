"""Learning from the owner's own changes (coordinator plan C11).

A correction is den_lighting 1.1's `last_correction` record ({seq, entity_id, before, after, at, user_id}),
or a fixture spot the owner set by hand (source: hand). It counts only while the coordinator is On, the den
is occupied, exactly one person is there, the situation is known, and it isn't one of den_lighting's own fades.
Records are deduplicated by seq.

The update, per (situation, light): brightness moves halfway from the record's `before` (or what was learned)
toward the owner's `after`; warmth moves halfway in mireds; on/off flips only after two corrections in a row
the same way. Going back and forth settles in between.

Standard library, plus a JSON file written whole or not at all.
"""
import json
import os

import situation as situation_mod

VERSION = "learned-v1"


def _halfway(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return int(round(a + (b - a) / 2.0))


def _halfway_kelvin(a, b):
    if a is None or b is None:
        return b if a is None else a
    mired = (1e6 / a + 1e6 / b) / 2.0
    return int(round(1e6 / mired))


class Learner:
    def __init__(self, entries=None, seen=None):
        self.entries = entries or {}  # {situation_key: {light: {on, brightness, kelvin, n, last, pending}}}
        self.seen = set(seen or ())

    def scene(self, key, light):
        row = self.entries.get(key, {}).get(light)
        return None if row is None else {"on": row["on"], "brightness": row["brightness"], "kelvin": row["kelvin"]}

    def apply(self, record, key, people, on, occupied, own_fade=False):
        """Learn from one correction. Returns (learned_row, None) or (None, why_not)."""
        seq = record.get("seq")
        if seq is not None and seq in self.seen:
            return None, "duplicate"
        why = (None if on else "coordinator_off") or (None if occupied else "vacant") or \
              (None if people == 1 else "people_%s" % people) or (None if not own_fade else "own_fade") or \
              (None if key.split("/")[0] in situation_mod.LEARNABLE else "situation_%s" % key.split("/")[0])
        if seq is not None:
            self.seen.add(seq)
        if why:
            return None, why
        light, after = record["entity_id"], record["after"]
        row = self.entries.setdefault(key, {}).get(light)
        prior = row or dict(record.get("before") or {}, n=0, pending=None)
        new = {"on": prior.get("on", True), "n": prior.get("n", 0) + 1, "last": record.get("at"), "pending": None,
               "brightness": prior.get("brightness"), "kelvin": prior.get("kelvin")}
        if after.get("on") is not None and after["on"] != prior.get("on", True):
            if prior.get("pending") == after["on"]:
                new["on"] = after["on"]  # the second change in a row the same way
            else:
                new["pending"] = after["on"]
        if after.get("on"):
            new["brightness"] = _halfway(prior.get("brightness"), after.get("brightness"))
            new["kelvin"] = _halfway_kelvin(prior.get("kelvin"), after.get("kelvin"))
        self.entries[key][light] = new
        return new, None

    def to_json(self):
        return {"version": VERSION, "entries": self.entries, "seen": sorted(self.seen, key=str)}

    @classmethod
    def from_json(cls, data):
        if not data:
            return cls()
        if data.get("version") != VERSION:
            raise ValueError("unknown learned file version %r" % data.get("version"))
        return cls(data.get("entries"), data.get("seen"))


def save(path, learner):
    """Write learned.json whole or not at all."""
    part = path + ".new"
    with open(part, "w", encoding="utf-8") as handle:
        json.dump(learner.to_json(), handle, indent=1, sort_keys=True)
    os.replace(part, path)


def load(path):
    if not os.path.exists(path):
        return Learner()
    with open(path, encoding="utf-8") as handle:
        return Learner.from_json(json.load(handle))


def clear(path, stamp):
    """Home's Clear: keep the old file as learned-<stamp>.json and start empty."""
    if os.path.exists(path):
        os.replace(path, os.path.join(os.path.dirname(path), "learned-%s.json" % stamp))
    return Learner()
