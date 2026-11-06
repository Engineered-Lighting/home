"""File adapters: each turns one kind of copied file into a time-ordered list of (t, kind, data).

Adding a source (README.md): one function here that returns items in an input
kind of events.py, its name in events.ADAPTER_SOURCES, and one flag in run.py.
Nothing downstream changes.
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import events
from events import LIGHTS

LAB = Path(__file__).resolve().parents[2] / "stack" / "services" / "perception-lab"
LA_LATENCY = Path(__file__).resolve().parents[1] / "la-latency"
# The den's Frigate person-occupancy entity: what the Jetson's occupancy reader polls and den_lighting reads.
DEN_OCCUPANCY = "binary_sensor.den_oak_person_occupancy"
COMMAND_KEYS = frozenset({"entity_id", "revision", "target", "context_id"})
LIGHT_KEYS = frozenset({"last_updated_ts", "entity_id", "state", "context_id", "context_user_id", "context_parent_id"})
STATUS_KEYS = frozenset({"last_updated_ts", "entity_id", "state", "attributes"})
CORRECTION_KEYS = frozenset({"correction_id", "entity_id", "after", "source"})
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
FRAME_APART_S = 0.0005  # den-verify takes a frame by its ts within this


def lab():
    """The perceiver's own la_fact_log and qwen_scene modules, from this checkout."""
    if str(LAB) not in sys.path:
        sys.path.insert(0, str(LAB))
    import la_fact_log
    import qwen_scene
    return la_fact_log, qwen_scene


def flaps():
    """tools/la-latency/occupancy_flaps.py, whose EDGE and parse() read the reader's journal edge lines."""
    if str(LA_LATENCY) not in sys.path:
        sys.path.append(str(LA_LATENCY))
    import occupancy_flaps
    return occupancy_flaps


def _text(path):
    """A copied text file: UTF-8, with or without a byte-order mark. A bad byte fails its own line; UTF-16 is refused."""
    return events.read_text(path, "replace")


def fact_time(available_at):
    return datetime.strptime(available_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()


def _fact(log, raw):
    """(t, line dict) for a good la-fact-log-1 line, else None."""
    try:
        value = json.loads(raw.decode("ascii"))
        if (not isinstance(value, dict) or set(value) != set(log.KEYS) or value["schema"] != log.SCHEMA
                or value["source_id"] != log.SOURCE_ID or not isinstance(value["request_id"], str)):
            return None
        observation = value["observation"]
        if observation is not None and (not isinstance(observation, dict) or set(observation) != log.RECEIPT_KEYS):
            return None
        fact_time(value["observed_at"])  # need reads it the same way, where a bad one would have no line number
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
        rows = events.read_raw(path).split(b"\n")
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
    for number, raw in enumerate(events.read_text(path).splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
            t = datetime.fromisoformat(row["t"]).timestamp()
            lights, occ = row["lights"].items(), row["occ"]
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):  # the last two: a date no clock here has
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
    try:
        steps = sorted((s for s in events.read_json(path) if "every" not in s), key=lambda s: s["at"])
        if any(not isinstance(s, dict) or not events.is_number(s["at"]) for s in steps):
            raise TypeError
    except (TypeError, KeyError):
        raise events.BadFile("%s is not a Frigate MQTT step list" % path) from None
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


def occupancy_journal(paths):
    """(items, gaps): the den's occupancy edges from the Jetson occupancy reader's journal lines, at their
    observed_at, and the spans in which the reader may have missed an off.

    Lines are matched by occupancy_flaps.EDGE (other lines are ignored). Only DEN_OCCUPANCY is kept. to=on
    and to=off map to on and off, anything else to unknown. An edge repeated across copies counts once.
    The reader logs an off only straight from on, so a dropout that ends off leaves no line. The next line
    after that to=on is then 'from=off' (an off was surely missed) or, when the person comes back out of a
    second dropout, 'from=unknown' (one may have been, which a dropout that ended on also logs). Each is in
    gaps as (t of the to=on, t of that line, sure); the replay stays occupied through it.
    """
    edges = set()
    for path in paths:
        lines = _text(path).splitlines()
        try:
            found = flaps().parse(lines)
        except ValueError as error:  # EDGE takes any run of digits and dots
            raise ValueError("%s: an occupancy edge's observed_at is not a number: %s" % (path, error)) from None
        if not all(math.isfinite(edge[0]) for edge in found):  # float() reads 401 digits as Infinity
            raise ValueError("%s: an occupancy edge's observed_at is not a number a float can hold" % path)
        edges.update(found)
    den = [(t, a, b) for t, a, b, entity in sorted(edges) if entity == DEN_OCCUPANCY]
    gaps = [(den[n - 1][0], t, a == "off") for n, (t, a, _) in enumerate(den)
            if n and den[n - 1][2] == "on" and a in ("off", "unknown")]
    return [(t, "occupancy", {"value": b if b in ("on", "off") else "unknown", "via": "jetson_journal"})
            for t, _, b in den], gaps


def _scene(value):
    b, k = value.get("brightness"), value.get("kelvin")
    return (set(value) == {"on", "brightness", "kelvin"} and type(value["on"]) is bool
            and (b is None or type(b) is int and 0 <= b <= 255) and (k is None or type(k) is int and k > 0))


def recorder_commands(paths):
    """den_lighting_command events from an HA recorder export (README): one JSON object per line,
    {time_fired_ts, event_data}, as live_command items at time_fired_ts, in time order.

    They are a reference to compare with, never a policy input (events.REFERENCE_KINDS). A bad line
    or a context_id seen twice raises ValueError; blank lines are skipped.
    """
    found, seen = [], set()
    for path in paths:
        for number, raw in enumerate(_text(path).splitlines(), 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
                t, data = row["time_fired_ts"], row["event_data"]
                if (not events.is_number(t) or set(data) != COMMAND_KEYS
                        or data["entity_id"] not in LIGHTS or type(data["revision"]) is not int
                        or not isinstance(data["context_id"], str) or not _scene(data["target"])):
                    raise ValueError
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError("%s line %d is not a den_lighting_command export row" % (path, number)) from None
            if data["context_id"] in seen:
                raise ValueError("%s line %d repeats context %s" % (path, number, data["context_id"]))
            seen.add(data["context_id"])
            found.append((float(t), "live_command", data))
    return sorted(found, key=lambda item: item[0])


def ulid(hex_bytes):
    """The ULID text of a recorder context_id_bin given as hex (HA stores Context ids as 16 bytes); '' -> None."""
    if hex_bytes == "":
        return None
    raw = bytes.fromhex(hex_bytes)
    if len(raw) != 16:
        raise ValueError("a context id is 16 bytes")
    n = int.from_bytes(raw, "big")
    return "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def recorder_lights(paths, own=()):
    """The four bulbs' recorded states from an HA recorder export (README): one JSON object per line,
    {last_updated_ts, entity_id, state, context_id, context_user_id, context_parent_id} with the context
    ids as hex, as light_observed items at last_updated_ts, in time order.

    HA's recorder keeps no light brightness or colour temperature, so both are null. A state other than
    on or off is skipped, as den_lighting skips it. context is 'own' when the state's context or its
    parent is one of den_lighting's command contexts (own: the context_id values of recorder_commands),
    'person' when a user made it, else 'unknown'. A bad line, or a bulb recorded twice at one time,
    raises ValueError; blank lines are skipped.
    """
    own, found, seen = {c.upper() for c in own}, [], set()
    for path in paths:
        for number, raw in enumerate(_text(path).splitlines(), 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
                t = row["last_updated_ts"]
                if (set(row) != LIGHT_KEYS or not events.is_number(t)
                        or row["entity_id"] not in LIGHTS or not isinstance(row["state"], str)):
                    raise ValueError
                context, user, parent = (ulid(row[key]) for key in ("context_id", "context_user_id",
                                                                      "context_parent_id"))
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError("%s line %d is not a den bulb state export row" % (path, number)) from None
            if (row["entity_id"], t) in seen:
                raise ValueError("%s line %d repeats %s at %r" % (path, number, row["entity_id"], t))
            seen.add((row["entity_id"], t))
            if row["state"] not in ("on", "off"):
                continue
            label = "own" if {context, parent} & own else "person" if user else "unknown"
            found.append((float(t), "light_observed", {"entity_id": row["entity_id"], "on": row["state"] == "on",
                                                       "brightness": None, "kelvin": None, "context": label}))
    return sorted(found, key=lambda item: item[0])


def correction_capture(paths):
    """The holds the live den_lighting opened from outside, from tools/correction-capture's corrections.jsonl
    (capture.py --events): den-event-1 correction lines from source correction_capture, as correction items at
    their t, in time order.

    One action that changed several bulbs is one line per bulb under one correction_id. A line repeated in
    overlapping files counts once; the same correction_id and bulb with other values, a line that is not such a
    correction, or a bulb that is not one of the four raises ValueError. Blank lines are skipped.
    """
    found, seen = [], {}
    for path in paths:
        for number, raw in enumerate(_text(path).splitlines(), 1):
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
                events.validate(event)
                data = event["data"]
                if (event["kind"] != "correction" or event["source"] != "correction_capture"
                        or set(data) != CORRECTION_KEYS or data["entity_id"] not in LIGHTS
                        or not isinstance(data["correction_id"], str) or not isinstance(data["source"], str)
                        or not isinstance(data["after"], dict) or not _scene(data["after"])):
                    raise ValueError
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError("%s line %d is not a correction-capture correction line" % (path, number)) from None
            item = (float(event["t"]), "correction", data)
            key = (data["correction_id"], data["entity_id"])
            if key in seen:
                if seen[key] != item:
                    raise ValueError("%s line %d repeats correction %s for %s with other values"
                                     % (path, number, data["correction_id"], data["entity_id"]))
                continue
            seen[key] = item
            found.append(item)
    return sorted(found, key=lambda item: (item[0], LIGHTS.index(item[2]["entity_id"])))


def light_states(path):
    """[{id, ts}] in time order from canon LightState JSON Lines, in the shape den-photometry writes as light_state.jsonl. A
    frame's id is its `id` key if it has one, else 'ls-%.3f' % ts. The frames are references for the coordinator to name, not
    inputs to its solve, so nothing else of them goes on. A bad line, or two frames within den-verify's 0.0005 s, which it could
    not tell apart, raises ValueError; blank lines are skipped."""
    found = []
    for number, raw in enumerate(_text(path).splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
            ts, name = row["ts"], row.get("id")
            if not events.is_number(ts):
                raise ValueError
            name = "ls-%.3f" % ts if name is None else name  # a ts of 60 digits would make an id no line keeps room for
            if not (isinstance(name, str) and name and len(events.dumps(name)) - 2 <= events.MAX_FRAME_ID):
                raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError("%s line %d is not a LightState: it needs a ts in seconds, and any id must be text of at most %d "
                             "characters" % (path, number, events.MAX_FRAME_ID)) from None
        found.append((float(ts), number, name))
    found.sort()
    for (a, line_a, _), (b, line_b, _) in zip(found, found[1:]):
        if b - a < FRAME_APART_S:
            raise ValueError("%s lines %d and %d are both at ts %r: two frames at one time can't be told apart" % (path, line_a, line_b, a))
    return [{"id": name, "ts": ts} for ts, _, name in found]


def _status(attributes):
    """(automatic, gaming, occupancy, revision) from den_lighting's status attributes, or None for a row without
    them (the sensor unavailable, as across an HA restart)."""
    if not isinstance(attributes, dict) or not {"automatic", "gaming_session", "occupancy", "revision"} <= set(attributes):
        return None
    automatic, gaming, occupancy, revision = (attributes[key] for key in ("automatic", "gaming_session", "occupancy",
                                                                         "revision"))
    if (type(automatic) is not bool or type(gaming) is not bool or occupancy not in ("on", "off", "unknown")
            or type(revision) is not int):
        raise ValueError
    return automatic, gaming, occupancy, revision


def den_lighting_status(paths):
    """(items, restarts) from den_lighting's status sensor export (tools/correction-capture's step 4): one JSON
    object per line, {last_updated_ts, entity_id, state, attributes}, in time order.

    - occupancy {value, via: 'den_lighting_status'} on each change of the occupancy den_lighting itself held: its
      Controller.occupancy, which its status shows. A row without status attributes (the sensor unavailable) is
      'unknown': den_lighting was not running. A row whose revision went down follows an HA restart, which starts
      den_lighting at unknown, so it gives unknown and then its own value at the same t. Each of the two counts
      as one restart.
    - live_status {automatic, gaming} on each change, a reference (events.REFERENCE_KINDS): whether the live
      den_lighting was automatic and in a game (gaming_session), null for a row without status attributes.

    A line repeated in overlapping exports counts once. A bad row, or rows for more than one entity, raises
    ValueError; blank lines are skipped.
    """
    rows, seen, entities = [], set(), set()
    for path in paths:
        for number, raw in enumerate(_text(path).splitlines(), 1):
            raw = raw.strip()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            try:
                row = json.loads(raw)
                t = row["last_updated_ts"]
                if (set(row) != STATUS_KEYS or not events.is_number(t)
                        or not isinstance(row["entity_id"], str)):
                    raise ValueError
                status = _status(row["attributes"])
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError("%s line %d is not a den_lighting status sensor export row" % (path, number)) from None
            entities.add(row["entity_id"])
            rows.append((float(t), status))
    if len(entities) > 1:
        raise ValueError("the status export holds more than one entity: %s" % ", ".join(sorted(entities)))
    items, occupancy, live, revision, restarts = [], None, None, None, 0
    for t, status in sorted(rows, key=lambda row: row[0]):
        values = ["unknown"]
        if status is None:
            restarts += revision is not None  # the sensor went unavailable after a status row
        else:
            if revision is not None and status[3] < revision:
                restarts += 1
                values = ["unknown", status[2]]
            else:
                values = [status[2]]
        for value in values:
            if value != occupancy:
                items.append((t, "occupancy", {"value": value, "via": "den_lighting_status"}))
                occupancy = value
        now = {"automatic": None, "gaming": None} if status is None else {"automatic": status[0], "gaming": status[1]}
        if now != live:
            items.append((t, "live_status", now))
            live = now
        revision = None if status is None else status[3]
    return items, restarts
