"""File adapters: each turns one kind of copied file into a time-ordered list of (t, kind, data).

Adding a source (README.md): one function here that returns items in an input
kind of events.py, its name in events.ADAPTER_SOURCES, and one flag in run.py.
Nothing downstream changes.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parents[2] / "stack" / "services" / "perception-lab"


def lab():
    """The perceiver's own la_fact_log and qwen_scene modules, from this checkout."""
    if str(LAB) not in sys.path:
        sys.path.insert(0, str(LAB))
    import la_fact_log
    import qwen_scene
    return la_fact_log, qwen_scene


def fact_time(available_at):
    return datetime.strptime(available_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()


def _fact(log, raw):
    """(t, line dict) for a good la-fact-log-1 line, else None."""
    try:
        value = json.loads(raw.decode("ascii"))
        if (not isinstance(value, dict) or set(value) != set(log.KEYS) or value["schema"] != log.SCHEMA
                or value["source_id"] != log.SOURCE_ID):
            return None
        observation = value["observation"]
        if observation is not None and (not isinstance(observation, dict) or set(observation) != log.RECEIPT_KEYS):
            return None
        return fact_time(value["available_at"]), value
    except (ValueError, TypeError):
        return None


def fact_log(paths):
    """(items, torn) from la-fact-log-1-*.jsonl day files, sorted by (available_at, request_id).

    A bad last line in a file without a final newline is a torn copy: skipped and
    counted. Any other bad line, or a request_id seen twice, raises ValueError.
    """
    log, _ = lab()
    found, seen, torn = [], set(), 0
    for path in paths:
        rows = Path(path).read_bytes().split(b"\n")
        terminated = rows[-1] == b""
        if terminated:
            rows.pop()
        for number, raw in enumerate(rows, 1):
            good = _fact(log, raw)
            if good is None:
                if not terminated and number == len(rows):
                    torn += 1
                    continue
                raise ValueError("%s line %d is not a la-fact-log-1 line" % (path, number))
            t, value = good
            if value["request_id"] in seen:
                raise ValueError("%s line %d repeats request %s" % (path, number, value["request_id"]))
            seen.add(value["request_id"])
            found.append((t, value["request_id"], value))
    found.sort(key=lambda item: item[:2])
    return [(t, "facts", value) for t, _, value in found], torn


def poll_log(path):
    """Occupancy and on/off light edges from a den-desk-observe HA poll log, on change only.

    Rows carry no brightness or kelvin. den_lighting ignores HA OFF (vacancy needs
    an MQTT zero), so this log's occ=off only approximates its input.
    """
    items, last = [], {}
    for number, raw in enumerate(Path(path).read_bytes().decode("ascii").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
            t = datetime.fromisoformat(row["t"]).timestamp()
            lights, occ = row["lights"].items(), row["occ"]
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError("%s line %d is not a poll row" % (path, number)) from None
        # Lights before occupancy, as in the synthetic preamble.
        for entity_id, state in lights:
            if state in ("on", "off") and last.get(entity_id) != state:
                items.append((t, "light_observed", {"entity_id": entity_id, "on": state == "on", "brightness": None,
                                                    "kelvin": None, "context": "unknown"}))
            last[entity_id] = state
        occ = occ if occ in ("on", "off") else "unknown"
        if last.get(None) != occ:
            items.append((t, "occupancy", {"value": occ, "via": "ha_poll"}))
            last[None] = occ
    return sorted(items, key=lambda item: item[0])


def mqtt_timeline(path, t0=0.0):
    """Occupancy edges from a Frigate MQTT step list (tests/fixtures/mqtt in victoria-ha),
    by den_lighting's own rules: mqtt_count, mqtt_availability, mqtt_connection_changed."""
    steps = sorted((s for s in json.loads(Path(path).read_text("ascii")) if "every" not in s), key=lambda s: s["at"])
    items, value, frigate, broker = [], "unknown", None, None
    for step in steps:
        new = value
        if "connect" in step:
            if not step["connect"] or broker is False:
                new = "unknown"
            broker = bool(step["connect"])
        elif step.get("topic") == "frigate/available":
            available = isinstance(step.get("payload"), str) and step["payload"].strip() == "online"
            if not available or frigate is False:
                new = "unknown"  # back online after offline stays unknown until the next count
            frigate = available
        elif step.get("topic") == "frigate/den_oak/person":
            payload = step.get("payload")
            if step.get("retain") or frigate is False or broker is False or not isinstance(payload, str):
                continue
            payload = payload.strip()
            if not payload or len(payload) > 32 or not payload.isascii() or not payload.isdecimal():
                continue
            new = "on" if int(payload) > 0 else "off"
        if new != value:
            items.append((float(t0 + step["at"]), "occupancy", {"value": new, "via": "mqtt"}))
            value = new
    return items
