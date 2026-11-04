"""correction-capture: den_lighting's hand corrections per occupied hour, from read-only HA recorder exports.

Offline and standard library only: it reads copied files and never reaches a host. See README.md.

python -B tools/correction-capture/capture.py --status S.jsonl --lights B.jsonl --commands C.jsonl --out ABS_FOLDER
"""
import argparse
import bisect
import json
import math
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEN_CORE = HERE.parent / "den-core"
if str(DEN_CORE) not in sys.path:
    sys.path.insert(0, str(DEN_CORE))

import events  # noqa: E402  den-event-1, canonical JSON, inside_checkout
import sources  # noqa: E402  den-core's readers for the bulb and command exports

SCHEMA = "den-exposure-1"
RULE_PATH = HERE / "cph-budget-v0.json"
RULE_ID = "cph-budget-v0"
# sha256 of the rule's canonical JSON (events.sha256_json), pinned when it was written, before any real export.
RULE_SHA256 = "bec5b06ce979c66030481c6f1ff8505c605543206e6b4e98f8afe07812bf600d"
CHECKOUT_DEN_LIGHTING = HERE.parents[1] / "ha-config" / "custom_components" / "den_lighting"
LIGHTS = events.LIGHTS
STATUS_KEYS = frozenset({"last_updated_ts", "entity_id", "state", "attributes"})
STATUS_NEEDS = ("automatic", "gaming_session", "occupancy", "revision", "holds", "provisional_fixtures")
WINDOW_S = 5.0  # a bulb's own rows this far either side of a hold say who made it
SETTLE_S = 3.0  # holds in one HA context, or this close together, are one action
# den_lighting counts an id-less report as its own inside transition (2 or 5 s) + 3 s of its command; the export's
# time_fired_ts comes after the light call returns, so the window opens a little before it.
ACK_BEFORE_S, ACK_AFTER_S = 2.0, 8.0
BLOCK_ANCHOR_S = 20 * 3600  # 24-hour blocks start at 20:00 UTC: noon in Victoria in winter, 1 pm in summer
PEOPLE = ("app", "switch_or_dimmer")
SOURCES = ("app", "switch_or_dimmer", "automation", "unknown")  # an action takes the first its changes have
STRATA = ("no_game", "game")


# ---------------------------------------------------------------- readers

def _finite(t):
    return type(t) in (int, float) and math.isfinite(t)


def _lines(paths, what):
    """(path, line number, object) per non-blank line; a line repeated in overlapping exports counts once."""
    seen = set()
    for path in paths:
        for number, raw in enumerate(sources._text(path).splitlines(), 1):
            raw = raw.strip()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            try:
                row = json.loads(raw)
            except ValueError:
                row = None
            if not isinstance(row, dict):
                raise ValueError("%s line %d is not a %s row" % (path, number, what))
            yield path, number, row


def _status(attrs):
    """den_lighting's status attributes, or None for a row without them (unavailable, or a start-up placeholder)."""
    if not isinstance(attrs, dict) or not all(key in attrs for key in STATUS_NEEDS):
        return None
    holds, provisional = attrs["holds"], attrs["provisional_fixtures"]
    if (type(attrs["automatic"]) is not bool or type(attrs["gaming_session"]) is not bool
            or attrs["occupancy"] not in ("on", "off", "unknown") or type(attrs["revision"]) is not int
            or not isinstance(holds, dict) or not set(holds) <= set(LIGHTS)
            or not all(isinstance(h, dict) and isinstance(h.get("target"), dict) and sources._scene(h["target"])
                       for h in holds.values())
            or not isinstance(provisional, list) or not set(provisional) <= set(LIGHTS)):
        raise ValueError
    return {"automatic": attrs["automatic"], "game": attrs["gaming_session"], "occupancy": attrs["occupancy"],
            "revision": attrs["revision"], "holds": {e: h["target"] for e, h in holds.items()},
            "provisional": set(provisional), "occupancy_source": str(attrs.get("occupancy_source"))}


def status_rows(paths):
    """(entity_id, [(t, status or None)]) from the status sensor export, in time order."""
    found, entities = [], set()
    for path, number, row in _lines(paths, "status sensor export"):
        try:
            t = row["last_updated_ts"]
            if set(row) != STATUS_KEYS or not _finite(t) or not isinstance(row["entity_id"], str):
                raise ValueError
            status = _status(row["attributes"])
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError("%s line %d is not a status sensor export row" % (path, number)) from None
        entities.add(row["entity_id"])
        found.append((float(t), status))
    if len(entities) > 1:
        raise ValueError("the status export holds more than one entity: %s" % ", ".join(sorted(entities)))
    found.sort(key=lambda item: item[0])
    return (entities.pop() if entities else None), found


def bulb_rows(paths):
    """The four bulbs' on/off rows from den-core's bulb export, per bulb in time order:
    {t, entity_id, on, flip, context, user, parent}. flip: the row turns the bulb the other way from its last row.
    Other states (unavailable, unknown) are skipped, as den_lighting skips them."""
    found, seen = [], set()
    for path, number, row in _lines(paths, "den bulb state export"):
        try:
            t = row["last_updated_ts"]
            if (set(row) != sources.LIGHT_KEYS or not _finite(t) or row["entity_id"] not in LIGHTS
                    or not isinstance(row["state"], str)):
                raise ValueError
            context, user, parent = (sources.ulid(row[key]) for key in ("context_id", "context_user_id",
                                                                          "context_parent_id"))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError("%s line %d is not a den bulb state export row" % (path, number)) from None
        if (row["entity_id"], t) in seen:
            raise ValueError("%s line %d repeats %s at %r" % (path, number, row["entity_id"], t))
        seen.add((row["entity_id"], t))
        if row["state"] in ("on", "off"):
            found.append({"t": float(t), "entity_id": row["entity_id"], "on": row["state"] == "on",
                          "context": context, "user": user, "parent": parent})
    found.sort(key=lambda r: r["t"])
    by_bulb = {e: [] for e in LIGHTS}
    for r in found:
        rows = by_bulb[r["entity_id"]]
        r["flip"] = not rows or rows[-1]["on"] != r["on"]
        rows.append(r)
    return by_bulb


def command_rows(paths):
    """(per bulb [(t, target)] in time order, den_lighting's own context ids) from the command export, read by
    den-core's recorder_commands. A context in two files must carry the same command."""
    merged = {}
    for path in paths:
        for t, _, data in sources.recorder_commands([path]):
            key = data["context_id"].upper()
            if merged.get(key, (t, data)) != (t, data):
                raise ValueError("%s: context %s differs from another export" % (path, data["context_id"]))
            merged[key] = (t, data)
    by_bulb = {e: [] for e in LIGHTS}
    for t, data in sorted(merged.values(), key=lambda item: item[0]):
        by_bulb[data["entity_id"]].append((t, data["target"]))
    return by_bulb, set(merged)


def installed_files(path):
    """The owner's sha256sum of the installed den_lighting files, compared with this checkout's."""
    found = {}
    for number, line in enumerate(sources._text(path).splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if (len(parts) != 2 or len(parts[0]) != 64 or not set(parts[0]) <= set(string.hexdigits)):
            raise ValueError("%s line %d is not a sha256sum line" % (path, number))
        found[parts[1].strip().lstrip("*").replace("\\", "/").rsplit("/", 1)[-1]] = parts[0].lower()
    checkout = {p.name: events.sha256_file(p) for p in sorted(CHECKOUT_DEN_LIGHTING.iterdir()) if p.is_file()}
    manifest = json.loads((CHECKOUT_DEN_LIGHTING / "manifest.json").read_text("utf-8"))
    differs = sorted(name for name in set(found) | set(checkout) if found.get(name) != checkout.get(name))
    return {"files": dict(sorted(found.items())), "matches_checkout": not differs, "differs": differs,
            "checkout_version": manifest.get("version")}


# ---------------------------------------------------------------- statistics

def poisson_cdf(k, mu):
    """P(X <= k) for X ~ Poisson(mu)."""
    if k < 0:
        return 0.0
    if mu <= 0:
        return 1.0
    logs = [-mu + i * math.log(mu) - math.lgamma(i + 1) for i in range(k + 1)]
    top = max(logs)
    return min(1.0, math.exp(top) * sum(math.exp(x - top) for x in logs))


def _root(f, hi):
    """mu in [0, hi] where the decreasing f crosses 0, by bisection."""
    lo = 0.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def garwood(k, level=0.95):
    """Exact two-sided Poisson interval for a count k (Garwood 1936), as (lower, upper) counts."""
    a = (1 - level) / 2
    top = k + 10 * math.sqrt(k + 1) + 20
    lower = 0.0 if k == 0 else _root(lambda mu: poisson_cdf(k - 1, mu) - (1 - a), top)
    return lower, _root(lambda mu: poisson_cdf(k, mu) - a, top)


def load_rule(path=RULE_PATH):
    """cph-budget-v0, refused unless its canonical sha256 is the one pinned before any real export was opened."""
    rule = json.loads(Path(path).read_text("utf-8"))
    digest = events.sha256_json(rule)
    if rule.get("id") != RULE_ID or digest != RULE_SHA256:
        raise ValueError("%s is not the pinned %s (sha256 %s): write a new rule id instead of editing it"
                         % (path, RULE_ID, digest))
    return rule, digest


def hours_per_arm(rate, constants):
    """A13's occupied hours per arm for a 30% improvement: hours_constant * phi / rate (None for a rate of 0)."""
    return constants["hours_constant"] * constants["phi"] / rate if rate else None


def decide(occupied_h, people_low_hours, every_high_hours, constants):
    """cph-budget-v0's verdict from the hours per arm at people's lower and every hold's upper interval ends."""
    budget = constants["max_hours_per_arm"]
    if occupied_h < constants["min_occupied_h"]:
        return "insufficient"
    if every_high_hours is None or every_high_hours > budget:
        return "beyond_budget"
    if people_low_hours is not None and people_low_hours <= budget:
        return "within_budget"
    return "undetermined"


def verdict(occupied_h, people, every, constants):
    """cph-budget-v0 for one stratum, from its occupied hours and the two action counts."""
    if occupied_h < constants["min_occupied_h"]:
        return "insufficient"
    level = constants["interval_level"]
    return decide(occupied_h, hours_per_arm(garwood(people, level)[0] / occupied_h, constants),
                  hours_per_arm(garwood(every, level)[1] / occupied_h, constants), constants)


def _rate(count, occupied_h, span_days, constants):
    if not occupied_h:
        return {"count": count, "per_occupied_h": None, "interval": None, "hours_per_arm": None,
                "weeks_one_den": None}
    lo, hi = garwood(count, constants["interval_level"])
    hours = hours_per_arm(count / occupied_h, constants)
    # Both arms take turns in the one den, so it needs twice the hours per arm of this stratum's occupied time.
    weeks = 2 * hours * span_days / (7 * occupied_h) if hours is not None and span_days else None
    return {"count": count, "per_occupied_h": _r(count / occupied_h), "interval": [_r(lo / occupied_h),
            _r(hi / occupied_h)], "hours_per_arm": _r(hours), "weeks_one_den": _r(weeks)}


def phi_hat(per_session):
    """Pearson dispersion of the per-block counts, with a log(occupied_h) offset and one rate per stratum."""
    totals = {}
    for s in per_session:
        h, c = totals.get(s["mode_stratum"], (0.0, 0))
        totals[s["mode_stratum"]] = (h + s["occupied_h"], c + s["corrections"])
    used = [s for s in per_session if s["occupied_h"] > 0 and totals[s["mode_stratum"]][1] > 0]
    strata = {s["mode_stratum"] for s in used}
    if len(used) - len(strata) < 1:
        return None, "not estimated: needs more 24-hour blocks with corrections than strata with corrections"
    pearson = 0.0
    for s in used:
        h, c = totals[s["mode_stratum"]]
        mu = s["occupied_h"] * c / h
        pearson += (s["corrections"] - mu) ** 2 / mu
    return (_r(pearson / (len(used) - len(strata))),
            "Pearson dispersion over %d 24-hour blocks, log(occupied_h) offset, one rate per stratum; planning only: "
            "%s keeps phi = 1" % (len(used), RULE_ID))


# ---------------------------------------------------------------- capture

def _r(value, places=4):
    return None if value is None else round(value, places)


def _utc(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _block(t):
    return math.floor((t - BLOCK_ANCHOR_S) / 86400)


def _block_name(block):
    return datetime.fromtimestamp(block * 86400 + BLOCK_ANCHOR_S, timezone.utc).date().isoformat()


def _in_charge(s):
    return s["automatic"] and s["occupancy"] == "on"


def _stratum(s):
    return "game" if s["game"] else "no_game"


def walk(rows):
    """Read the status rows in order. Returns (candidates, exposure seconds per (block, stratum), seconds per
    stratum and occupancy source, in-charge seconds lost across restarts, restarts).

    A candidate is a bulb that became held, whose held target changed, or whose provisional hold (den_lighting's
    own fade report) was confirmed. A row whose revision went down, or that follows a row without status
    attributes, starts after an HA restart: the holds it shows were restored, and the time across it is not
    exposure. The last row's time is not exposure either: the export does not say how long it lasted."""
    out, exposure, by_source, lost, restarts = [], {}, {}, 0.0, 0
    prev = prev_t = None
    broken, provisional_since = False, {}
    for t, s in rows:
        if s is None:
            broken = prev is not None
            continue
        restart = prev is not None and (broken or s["revision"] < prev["revision"])
        broken = False
        if prev is not None and _in_charge(prev):
            if restart:
                lost += t - prev_t
            else:
                _add(exposure, by_source, prev_t, t, prev)
        restarts += restart
        for e in LIGHTS:
            after = s["holds"].get(e)
            if after is None:
                provisional_since.pop(e, None)
                continue
            base = {"t": t, "entity_id": e, "after": after, "status": s, "held_before": None, "since": t}
            if prev is None or restart:
                out.append({**base, "hold": "opened",
                            "reason": "held_at_export_start" if prev is None else "restored_after_restart"})
                provisional_since.pop(e, None)
                if e in s["provisional"]:
                    provisional_since[e] = t
                continue
            before = prev["holds"].get(e)
            if e in s["provisional"]:
                if e not in provisional_since:
                    provisional_since[e] = t
                    out.append({**base, "hold": "opened" if before is None else "replaced", "held_before": before,
                                "reason": "own_fade_report"})
                continue
            if e in provisional_since:
                out.append({**base, "hold": "confirmed", "since": provisional_since.pop(e), "reason": None})
            elif before is None:
                out.append({**base, "hold": "opened", "reason": None})
            elif before != after:
                out.append({**base, "hold": "replaced", "held_before": before, "reason": None})
        prev, prev_t = s, t
    return out, exposure, by_source, lost, restarts


def _add(exposure, by_source, start, end, s):
    stratum = _stratum(s)
    key = (stratum, s["occupancy_source"])
    by_source[key] = by_source.get(key, 0.0) + end - start
    while start < end:
        block = _block(start)
        stop = min(end, (block + 1) * 86400 + BLOCK_ANCHOR_S)
        exposure[(block, stratum)] = exposure.get((block, stratum), 0.0) + stop - start
        start = stop


def _window(rows, lo, hi):
    times = [r["t"] for r in rows]
    return rows[bisect.bisect_left(times, lo):bisect.bisect_right(times, hi)]


def _own_fade(row, commands):
    """An id-less row within den_lighting's acknowledgement window of its command to that bulb, unless it turns
    the bulb against that command (a flip to the other on/off)."""
    times = [t for t, _ in commands]
    lo, hi = bisect.bisect_left(times, row["t"] - ACK_AFTER_S), bisect.bisect_right(times, row["t"] + ACK_BEFORE_S)
    near = commands[lo:hi]
    return any(not (row["flip"] and row["on"] != target["on"]) for _, target in near)


def attribute(c, bulbs, commands, own):
    """(source, row) by A11's capture rule, from the bulb's own rows WINDOW_S around the hold (from the start of a
    confirmed provisional hold). den_lighting's own rows (its context or parent, or an id-less report inside its
    acknowledgement window) are set aside; of the rest, a user id is the HA app, a parent context another
    automation, and no ids a switch or dimmer. Only den_lighting's own rows: den_lighting. No rows: unknown."""
    e = c["entity_id"]
    kept, own_seen = [], False
    for r in _window(bulbs[e], c["since"] - WINDOW_S, c["t"] + WINDOW_S):
        if (r["context"] in own or r["parent"] in own
                or not r["user"] and not r["parent"] and _own_fade(r, commands[e])):
            own_seen = True
        else:
            kept.append(r)
    for source, test in (("app", lambda r: r["user"]), ("automation", lambda r: r["parent"]),
                         ("switch_or_dimmer", lambda r: True)):
        hits = [r for r in kept if test(r)]
        if hits:
            return source, min(hits, key=lambda r: abs(r["t"] - c["t"]))
    return ("den_lighting" if own_seen else "unknown"), None


def _before(c, bulbs, commands):
    """The bulb's setting before the change: its held target, else den_lighting's latest command to it, else its
    last recorded on/off (no levels)."""
    if c["held_before"] is not None:
        return c["held_before"], "hold"
    sent = [target for t, target in commands[c["entity_id"]] if t <= c["since"]]
    if sent:
        return sent[-1], "den_lighting_command"
    rows = [r for r in bulbs[c["entity_id"]] if r["t"] < c["since"] - WINDOW_S]
    if rows:
        return {"on": rows[-1]["on"], "brightness": None, "kelvin": None}, "recorded_state"
    return None, None


def group(changes):
    """One action per HA context, or per run of changes no more than SETTLE_S apart."""
    actions, by_context = [], {}
    for c in sorted(changes, key=lambda c: (c["t"], LIGHTS.index(c["entity_id"]))):
        context = c["row"]["context"] if c["row"] else None
        action = by_context.get(context) if context else None
        if action is None and actions and c["t"] - actions[-1][-1]["t"] <= SETTLE_S:
            action = actions[-1]
        if action is None:
            action = []
            actions.append(action)
        action.append(c)
        if context:
            by_context[context] = action
    return actions


def _context(row):
    if row is None:
        return {"id": None, "user_id": None, "parent_id": None}
    return {"id": row["context"], "user_id": "present" if row["user"] else None, "parent_id": row["parent"]}


def _at(s):
    return {"automatic": s["automatic"], "occupancy": s["occupancy"], "gaming_session": s["game"]}


def capture(status_paths, light_paths, command_paths, installed_path=None, rule_path=RULE_PATH):
    """(den-exposure-1 record, den-core correction events) from the three exports."""
    rule, rule_sha = load_rule(rule_path)
    constants = rule["constants"]
    entity, rows = status_rows(status_paths)
    bulbs = bulb_rows(light_paths)
    commands, own = command_rows(command_paths)
    valid = [t for t, s in rows if s is not None]
    if not valid:
        raise ValueError("the status export has no row with den_lighting's status attributes")
    span_days = (valid[-1] - valid[0]) / 86400
    found, exposure, by_source, lost, restarts = walk(rows)

    counted, not_counted, replay = [], [], []
    for c in found:
        c["source"] = c["row"] = None
        if c["reason"] is None:
            c["source"], c["row"] = attribute(c, bulbs, commands, own)
            if c["source"] == "den_lighting":
                c["reason"] = "own_fade_report"
        (counted if c["reason"] is None else not_counted).append(c)

    corrections, sessions = [], {}
    for action in group(counted):
        first, last = action[0], action[-1]
        if not _in_charge(first["status"]):
            for c in action:
                c["reason"] = "outside_exposure"
            not_counted.extend(action)
            continue
        source = next(s for s in SOURCES if any(c["source"] == s for c in action))
        stratum, block = _stratum(first["status"]), _block(first["t"])
        key = (block, stratum)
        people, every = sessions.get(key, (0, 0))
        sessions[key] = (people + (source in PEOPLE), every + 1)
        contexts = {c["row"]["context"] for c in action if c["row"]}
        changes = []
        for c in action:
            before, before_from = _before(c, bulbs, commands)
            changes.append({"entity_id": c["entity_id"], "t": c["t"], "hold": c["hold"], "source": c["source"],
                            "ha_context": _context(c["row"]), "before": before, "before_from": before_from,
                            "after": c["after"]})
        cid = "cc-%d" % round(first["t"] * 1000)
        corrections.append({
            "correction_id": cid, "ts": last["t"], "ts_utc": _utc(last["t"]), "source": source,
            "people": source in PEOPLE, "mode_stratum": stratum, "session_id": "%s/%s" % (_block_name(block), stratum),
            "ha_context": _context(first["row"]),
            "action": {"grouping": "one change" if len(action) == 1 else "one HA context"
                       if len(contexts) == 1 and all(c["row"] for c in action) else "within %g s" % SETTLE_S,
                       "settle_s": SETTLE_S, "n_changes": len(action)},
            "changes": changes, "attribution": None})
        replay += [(c["t"], cid, c) for c in action]

    skipped = []
    for c in sorted(not_counted, key=lambda c: (c["t"], LIGHTS.index(c["entity_id"]))):
        skipped.append({"entity_id": c["entity_id"], "t": c["t"], "ts_utc": _utc(c["t"]), "hold": c["hold"],
                        "reason": c["reason"], "source": c["source"], "after": c["after"], "at": _at(c["status"])})
        if c["reason"] in ("held_at_export_start", "outside_exposure"):
            replay.append((c["t"], "hold-%d-%d" % (round(c["t"] * 1000), LIGHTS.index(c["entity_id"])), c))

    keys = sorted(set(exposure) | set(sessions), key=lambda k: (k[0], STRATA.index(k[1])))
    per_session = [{"session_id": "%s/%s" % (_block_name(block), stratum),
                    "occupied_h": _r(exposure.get((block, stratum), 0.0) / 3600), "occupancy_source": "den_lighting",
                    "mode_stratum": stratum, "corrections": sessions.get((block, stratum), (0, 0))[0]}
                   for block, stratum in keys]
    phi, phi_status = phi_hat(per_session)
    strata = {}
    for stratum in STRATA:
        h = sum(v for (_, s), v in exposure.items() if s == stratum) / 3600
        people = sum(p for (_, s), (p, _) in sessions.items() if s == stratum)
        every = sum(n for (_, s), (_, n) in sessions.items() if s == stratum)
        mine = [c for c in corrections if c["mode_stratum"] == stratum]
        people_rate = _rate(people, h, span_days, constants)
        strata[stratum] = {
            "occupied_h": _r(h), "occupied_h_per_week": _r(h * 7 / span_days) if span_days else None,
            "occupied_h_by_occupancy_source": {src: _r(v / 3600) for (s, src), v in sorted(by_source.items())
                                               if s == stratum},
            "people": people_rate, "every_hold": _rate(every, h, span_days, constants),
            "actions_by_source": {src: sum(c["source"] == src for c in mine) for src in SOURCES},
            "hours_per_arm_at_phi_hat": _r(people_rate["hours_per_arm"] * phi)
            if phi is not None and people_rate["hours_per_arm"] is not None else None,
            "verdict": verdict(h, people, every, constants)}

    reasons = {}
    for c in skipped:
        reasons[c["reason"]] = reasons.get(c["reason"], 0) + 1
    record = {
        "schema": SCHEMA,
        "rule": {"id": RULE_ID, "sha256": rule_sha, "sha256_of": "the rule's canonical JSON (events.sha256_json)"},
        "tool": {"name": "correction-capture", "code_sha256": events.sha256_file(__file__)},
        "controller": {"policy": "den_lighting", "status_entity": entity,
                       "installed": installed_files(installed_path) if installed_path else None},
        "inputs": [{"kind": kind, "name": Path(p).name, "sha256": events.sha256_file(p)}
                   for kind, paths in (("status", status_paths), ("lights", light_paths), ("commands", command_paths),
                                       ("installed", [installed_path] if installed_path else []))
                   for p in paths],
        "span": {"from": _utc(valid[0]), "to": _utc(valid[-1]), "days": _r(span_days), "restarts": restarts,
                 "status_rows": len(rows), "bulb_rows": sum(map(len, bulbs.values())),
                 "commands": sum(map(len, commands.values()))},
        "exposure": {"per_session": per_session, "occupied_h_total": _r(sum(exposure.values()) / 3600),
                     "corrections_total": sum(p for p, _ in sessions.values()), "phi_hat": phi,
                     "phi_hat_status": phi_status},
        "corrections": corrections,
        "not_counted": skipped,
        "planning": {"rule": RULE_ID, "strata": strata, "restart_gap_h": _r(lost / 3600),
                     "not_counted_by_reason": dict(sorted(reasons.items()))},
    }
    return record, correction_events(replay)


def correction_events(replay):
    """den-event-1 correction lines, so a den-core replay holds each bulb when the live den_lighting did: every
    hold it opened from outside, except restored ones (opened earlier) and its own fade reports."""
    out = []
    for t, cid, c in sorted(replay, key=lambda item: (item[0], LIGHTS.index(item[2]["entity_id"]))):
        event = {"schema": events.SCHEMA, "t": t, "seq": len(out), "kind": "correction", "source": "correction_capture",
                 "data": {"correction_id": cid, "entity_id": c["entity_id"], "after": c["after"],
                          "source": c["source"] or c["reason"]}}
        events.validate(event)
        out.append(event)
    return out


def write(out, record, replay_events=None):
    """exposure.json (and corrections.jsonl) in a new folder outside every git checkout."""
    out = Path(out)
    if not out.is_absolute() or events.inside_checkout(out):
        raise ValueError("--out must be an absolute folder outside every git checkout")
    if (out / "exposure.json").exists():
        raise ValueError("%s already holds an exposure.json" % out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "exposure.json", "x", encoding="ascii", newline="\n") as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n")
    if replay_events is not None:
        with open(out / "corrections.jsonl", "x", encoding="ascii", newline="\n") as handle:
            handle.write("".join(events.dumps(event) + "\n" for event in replay_events))


def summary(record):
    lines = ["%s to %s (%.1f days, %d restart(s)); rule %s sha256 %s" % (
        record["span"]["from"], record["span"]["to"], record["span"]["days"], record["span"]["restarts"],
        RULE_ID, record["rule"]["sha256"][:12])]
    for stratum, p in record["planning"]["strata"].items():
        lines.append("%s: %.2f occupied h (%s h/week)" % (stratum, p["occupied_h"], p["occupied_h_per_week"]))
        for name in ("people", "every_hold"):
            r = p[name]
            lines.append("  %-10s %3d actions  %s per h  95%% %s  hours/arm %s  weeks %s" % (
                name, r["count"], r["per_occupied_h"], r["interval"], r["hours_per_arm"], r["weeks_one_den"]))
        lines.append("  by source %s" % p["actions_by_source"])
        lines.append("  verdict: %s" % p["verdict"])
    lines.append("not counted: %s" % (record["planning"]["not_counted_by_reason"] or "none"))
    lines.append("phi_hat: %s (%s)" % (record["exposure"]["phi_hat"], record["exposure"]["phi_hat_status"]))
    installed = record["controller"]["installed"]
    if installed:
        lines.append("installed den_lighting %s this checkout's %s%s" % (
            "matches" if installed["matches_checkout"] else "DIFFERS from", installed["checkout_version"],
            "" if installed["matches_checkout"] else ": " + ", ".join(installed["differs"])))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", nargs="+", required=True, help="den_lighting status sensor export(s)")
    parser.add_argument("--lights", nargs="+", required=True, help="the four bulbs' state export(s), den-core format")
    parser.add_argument("--commands", nargs="+", required=True, help="den_lighting_command export(s), den-core format")
    parser.add_argument("--installed", help="sha256sum of the installed custom_components/den_lighting files")
    parser.add_argument("--out", required=True, help="absolute folder outside every git checkout")
    parser.add_argument("--events", action="store_true", help="also write den-core correction events")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if not out.is_absolute() or events.inside_checkout(out):
        parser.error("--out must be an absolute folder outside every git checkout")
    record, replay_events = capture(args.status, args.lights, args.commands, args.installed)
    write(out, record, replay_events if args.events else None)
    print(summary(record))
    print("wrote %s" % (out / "exposure.json"))


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        sys.exit("capture.py: %s" % error)
