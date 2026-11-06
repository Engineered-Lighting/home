"""The one deterministic replay path: inputs, ticks and plant echoes through the policies, all logged.

Every event is stamped, validated, logged and round-tripped through its own
canonical line before any policy sees it, so a live run and its replay hand
the policies identical values.
"""
import heapq
import importlib
import itertools
import json
import sys
from pathlib import Path

import events

HERE = Path(__file__).resolve().parent
ORDER = ("need", "incumbent", "decide")
TAIL_S = 150.0  # seconds of ticks after the last input, so a vacancy OFF can still happen
MAX_TICKS = 16_000_000  # a year of 2 s ticks, each a line of the log; no replay comes near it
LATEST_S = 32503680000.0  # the year 3000, as far as Windows' gmtime goes, and run.py's summary prints each time; before 1970 it stops too


def tagged(items, source):
    """Adapter items (t, kind, data) as runner inputs (t, kind, data, source)."""
    return [(t, kind, data, source) for t, kind, data in items]


def import_module(name):
    """A policy module from this folder, by plain name."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    if not (HERE / (name + ".py")).is_file():
        raise ValueError("%s.py is not in tools/den-core yet" % name)
    return importlib.import_module(name)


def load_policies(names, configs=None):
    return [import_module(name).make_policy((configs or {}).get(name)) for name in names]


def describe(policy):
    return {"id": policy.id, "module": Path(policy.code_path).stem, "version": policy.version,
            "code_sha256": events.sha256_file(policy.code_path), "config": policy.config,
            "config_sha256": events.sha256_json(policy.config)}


def check_span(inputs, tick_s, tail_s):
    """ValueError for inputs no run can tick through, before a run grows a heap of ticks until memory runs out: a time that
    isn't a number, one past any clock, or two good ones a lifetime apart, which is --mqtt with no --mqtt-t0 beside real facts."""
    times = [item[0] for item in inputs]
    read = [item[0] for item in inputs if item[1] not in events.REFERENCE_KINDS]
    if not (all(map(events.is_number, times)) and all(0 <= t <= LATEST_S for t in read) and events.is_number(tick_s) and events.is_number(tail_s)
            and tick_s > 0 and (not read or (max(read) + tail_s - min(read)) / tick_s <= MAX_TICKS)):
        raise ValueError("no run can tick through these inputs, from %r to %r at tick_s %r and tail_s %r: a time in one of them is wrong"
                         % (min(times, default=None), max(times, default=None), tick_s, tail_s))


def run(inputs, policies, *, tick_s=2.0, tail_s=TAIL_S, plant=True, shown="incumbent", synthetic=False,
        assume_occupied=False, sources=(), actuation=None):
    """(header, events) for one run; inputs are (t, kind, data, source).

    Reference kinds (events.REFERENCE_KINDS) are logged in time order like any input, but no policy is
    handed one and they do not set the span or the ticks, so a run's decisions are the same with or
    without them.

    actuation defaults to synthetic or replay, by the synthetic flag. 'shadow' decides beside what the real bulbs
    reported, so it needs the plant off: a run the plant echoes is not one. Nothing is sent in any den-core run,
    and a synthetic run is only ever synthetic."""
    check_span(inputs, tick_s, tail_s)
    inputs = sorted(inputs, key=lambda item: item[0])
    read = [item for item in inputs if item[1] not in events.REFERENCE_KINDS]
    if not read:
        raise ValueError("no inputs")
    policies = sorted(policies, key=lambda p: ORDER.index(p.id) if p.id in ORDER else len(ORDER))
    actuation = actuation or ("synthetic" if synthetic else "replay")
    if actuation not in events.ACTUATIONS or synthetic != (actuation == "synthetic"):
        raise ValueError("actuation %r does not fit synthetic=%s" % (actuation, synthetic))
    if actuation == "shadow" and plant:
        raise ValueError("actuation shadow needs the plant off: the real bulbs report")
    header = {"schema": events.LOG_SCHEMA, "sources": list(sources), "policies": [describe(p) for p in policies],
              "tick_s": tick_s, "tail_s": tail_s, "plant": plant, "shown": shown, "synthetic": synthetic,
              "assume_occupied": assume_occupied, "actuation": actuation}
    first, last = read[0][0], read[-1][0]
    queue, counter, log = [], itertools.count(), []

    def push(t, rank, kind, data, source):  # rank: 0 input, 1 plant echo, 2 tick
        heapq.heappush(queue, (t, rank, next(counter), kind, data, source))

    if assume_occupied:
        push(first, 0, "occupancy", {"value": "on", "via": "assumed"}, "runner")
    for t, kind, data, source in inputs:
        push(t, 0, kind, data, source)
    if assume_occupied:
        push(last + 5, 0, "occupancy", {"value": "off", "via": "assumed"}, "runner")
    for k in itertools.count(1):
        if first + k * tick_s > last + tail_s:
            break
        push(first + k * tick_s, 2, "tick", {}, "runner")

    def emit(t, kind, data, source):
        event = {"schema": events.SCHEMA, "t": float(t), "seq": len(log), "kind": kind, "source": source,
                 "data": data}
        event = json.loads(events.dumps(event))
        events.validate(event)
        log.append(event)
        return event

    def deliver(event, start):
        for i in range(start, len(policies)):
            policy = policies[i]
            for out in policy.handle(event):
                data = dict(out["data"])
                if out["kind"] == "decision":
                    data["actuation"] = actuation
                derived = emit(event["t"], out["kind"], data, policy.id)
                if plant and out["kind"] == "decision" and policy.id == shown:
                    for e, scene in derived["data"]["requested_scene"].items():
                        push(event["t"] + derived["data"]["transitions"].get(e, 0), 1, "light_observed",
                             {"entity_id": e, "on": scene["on"], "brightness": scene["brightness"],
                              "kelvin": scene["kelvin"], "context": "own"}, "plant")
                deliver(derived, i + 1)

    while queue:
        t, _, _, kind, data, source = heapq.heappop(queue)
        event = emit(t, kind, data, source)
        if kind not in events.REFERENCE_KINDS:
            deliver(event, 0)
    return header, log
