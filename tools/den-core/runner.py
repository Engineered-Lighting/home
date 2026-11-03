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


def run(inputs, policies, *, tick_s=2.0, tail_s=150.0, plant=True, shown="incumbent", synthetic=False,
        assume_occupied=False, sources=()):
    """(header, events) for one run; inputs are (t, kind, data, source)."""
    inputs = sorted(inputs, key=lambda item: item[0])
    if not inputs:
        raise ValueError("no inputs")
    policies = sorted(policies, key=lambda p: ORDER.index(p.id) if p.id in ORDER else len(ORDER))
    actuation = "synthetic" if synthetic else "replay"
    header = {"schema": events.LOG_SCHEMA, "sources": list(sources), "policies": [describe(p) for p in policies],
              "tick_s": tick_s, "tail_s": tail_s, "plant": plant, "shown": shown, "synthetic": synthetic,
              "assume_occupied": assume_occupied, "actuation": actuation}
    first, last = inputs[0][0], inputs[-1][0]
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
        deliver(emit(t, kind, data, source), 0)
    return header, log
