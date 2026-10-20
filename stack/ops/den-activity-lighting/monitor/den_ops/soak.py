"""The supervised soak (SOAK.md): sample, evaluate the written criteria, and
write a receipt (JSON plus a short markdown summary).

Run on each host for at least 3 h:
    python -m den_ops soak run --config soak.json --out <dir>
The owner's scripted MVP checklist runs alongside it on the LA host:
    python -m den_ops soak checklist --config soak.json --out <dir>
Then ``soak evaluate`` (re)builds a host receipt from its samples, and
``soak combine`` merges the hosts' receipts into the release verdict.
"""
from __future__ import annotations

import json
import os
import statistics
import time

from . import SOAK_CONFIG_SCHEMA, SOAK_RECEIPT_SCHEMA
from . import monitor as mon
from . import policy as ladder
from .hostio import write_atomic

MIN_DURATION_S = 3 * 3600
DEFAULT_FRESHNESS = {
    "online_fraction": 0.99,          # samples with brain "online" and /healthz ok
    "occupancy_fresh_s": 90.0,        # occupancy heartbeat ttl (CONTRACT)
    "occupancy_fresh_fraction": 0.99,
    "frigate_ok_fraction": 0.99,
    "occupied_health_fraction": 0.95, # jev + deep_stack healthy while the den is occupied
    "activity_fresh_s": 30.0,
    "activity_fresh_fraction": 0.90,  # of occupied samples, after the first minute of occupancy
}
DEFAULT_BOUNDS = {
    "host_mem_available_slope_mib_per_h": -256.0,   # host MemAvailable may not fall faster than this
    "gpu_mem_used_drift_mib": 1024.0,                # max - min GPU memory after warm-up
    "monitor_fresh_s": 180.0,                        # the monitor's status.json must keep updating
    "monitor_fresh_fraction": 0.95,
}


def load_soak_config(path):
    with open(path, encoding="utf-8") as handle:
        cfg = json.load(handle)
    if cfg.get("schema") != SOAK_CONFIG_SCHEMA:
        raise ValueError("soak config must be %s" % SOAK_CONFIG_SCHEMA)
    for unit in cfg.get("units", []):
        if unit.get("scope", "system") not in ("system", "user") or not str(unit.get("name", "")).endswith(".service"):
            raise ValueError("bad soak unit: %r" % (unit,))
    return cfg


# --- sampling --------------------------------------------------------------

def _int_or_none(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def read_unit(host, name, scope):
    argv = ("systemctl",) + (("--user",) if scope == "user" else ()) + (
        "show", name, "--property=ActiveState,NRestarts,MemoryCurrent,MemoryMax")
    code, text = host.run(argv)
    props = dict(line.split("=", 1) for line in str(text or "").splitlines() if "=" in line)
    current, limit = _int_or_none(props.get("MemoryCurrent")), _int_or_none(props.get("MemoryMax"))
    return {
        "active": props.get("ActiveState"),
        "n_restarts": _int_or_none(props.get("NRestarts")),
        "memory_mib": None if current is None or current >= 2 ** 63 else current / 1048576.0,
        "memory_max_mib": None if limit is None or limit >= 2 ** 63 else limit / 1048576.0,
        "readable": code == 0,
    }


def read_brain(host, brain):
    code, health = host.http("GET", brain["healthz_url"], timeout_s=5)
    state_code, state = host.http("GET", brain["state_url"], timeout_s=5)
    out = {"healthz_ok": code == 200 and isinstance(health, dict) and health.get("ok") is not False,
           "reachable": state_code == 200 and isinstance(state, dict)}
    if not out["reachable"]:
        return out
    occupancy = state.get("occupancy") or {}
    inputs = state.get("inputs") or {}
    fixtures = state.get("fixtures") or []
    out.update({
        "online": state.get("brain") == "online",
        "mode": state.get("mode"),
        "why": str(state.get("why", ""))[:120],
        "present": bool(occupancy.get("present")),
        "people_count": occupancy.get("people_count"),
        "occupancy_age_s": inputs.get("occupancy_age_s"),
        "activity_age_s": inputs.get("activity_age_s"),
        "pose_age_s": inputs.get("pose_age_s"),
        "health": dict(inputs.get("health") or {}),
        "spot_on": any(((f.get("spot") or {}).get("on")) for f in fixtures),
        "jev_calls_last_hour": (state.get("cost") or {}).get("jev_calls_last_hour"),
    })
    return out


def read_monitor_status(host, path, now):
    try:
        doc = json.loads(host.read_text(path))
    except (OSError, ValueError):
        return None
    return {"level": doc.get("level"), "step": doc.get("step"), "latched": doc.get("latched"),
            "age_s": None if doc.get("checked_at") is None else now - float(doc["checked_at"])}


def sample_once(cfg, monitor_cfg, host, journal_state):
    """One sample. ``journal_state`` carries the kernel-journal cursor between
    samples (the first sample only records where to start)."""
    now = host.now()
    signals, notices = mon.collect_signals(monitor_cfg, host)
    faults, fault_notices = mon.fault_records(monitor_cfg, host, journal_state)
    sample = {
        "at": now, "signals": signals,
        "faults": faults,
        "notices": [n["detail"] for n in notices + fault_notices],
        "rebooted": any(n["signal"] == "host.rebooted" for n in fault_notices),
        "units": {u["name"]: read_unit(host, u["name"], u.get("scope", "system")) for u in cfg.get("units", [])},
    }
    if cfg.get("brain"):
        sample["brain"] = read_brain(host, cfg["brain"])
    if cfg.get("monitor_status"):
        sample["monitor"] = read_monitor_status(host, cfg["monitor_status"], now)
    return sample


def run(cfg, monitor_cfg, host, out_dir, *, sleep=time.sleep, duration_s=None, log=print):
    """Sample every ``interval_s`` for ``duration_s``; samples are appended to
    samples.jsonl as they are taken, so an interrupted soak keeps its data."""
    duration = float(duration_s if duration_s is not None else cfg.get("duration_s", MIN_DURATION_S))
    interval = float(cfg.get("interval_s", 60))
    os.makedirs(out_dir, exist_ok=True)
    samples_path = os.path.join(out_dir, "samples-%s.jsonl" % cfg["host"])
    journal_state = {"journal": {}}
    started = host.now()
    meta = {"host": cfg["host"], "release": cfg.get("release"), "started_at": started}
    write_atomic(os.path.join(out_dir, "soak-meta-%s.json" % cfg["host"]), meta)
    count = 0
    while True:
        sample = sample_once(cfg, monitor_cfg, host, journal_state)
        with open(samples_path, "a", encoding="utf-8") as out:
            out.write(json.dumps(sample, sort_keys=True) + "\n")
        count += 1
        if sample["faults"]:
            log("soak: new fault record(s): %s" % ", ".join(f["class"] for f in sample["faults"]))
        if host.now() - started >= duration:
            break
        sleep(max(0.0, interval - (host.now() - sample["at"])))
    log("soak: %d samples over %.0f s" % (count, host.now() - started))
    return samples_path


def read_samples(path):
    samples = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


# --- criteria --------------------------------------------------------------

def _slope_per_hour(points):
    """Least-squares slope of (t seconds, value) points, per hour."""
    if len(points) < 3:
        return None
    ts = [p[0] for p in points]
    vs = [p[1] for p in points]
    mean_t, mean_v = statistics.fmean(ts), statistics.fmean(vs)
    denom = sum((t - mean_t) ** 2 for t in ts)
    if denom == 0:
        return None
    return 3600.0 * sum((t - mean_t) * (v - mean_v) for t, v in zip(ts, vs)) / denom


def _fraction(flags):
    flags = list(flags)
    return None if not flags else sum(1 for f in flags if f) / len(flags)


def _criterion(cid, title, passed, detail, measured=None):
    return {"id": cid, "title": title, "result": "pass" if passed is True else "fail" if passed is False else "pending",
            "detail": detail, "measured": measured or {}}


def _is_temperature(rule):
    return rule["signal"].startswith("temp.") or rule["signal"].endswith("temp_c")


def evaluate(cfg, monitor_cfg, samples, *, checklist=None, min_duration_s=MIN_DURATION_S):
    """The written pass criteria (SOAK.md) over one host's samples."""
    criteria = []
    if not samples:
        return [_criterion("duration", "Soak ran at least 3 h", False, "no samples")]
    interval = float(cfg.get("interval_s", 60))
    started, ended = samples[0]["at"], samples[-1]["at"]
    duration = ended - started
    expected = max(1, int(duration // interval) + 1)
    need = max(float(cfg.get("duration_s", min_duration_s)), float(min_duration_s))
    criteria.append(_criterion(
        "duration", "Soak ran at least 3 h with steady sampling",
        duration >= need and len(samples) >= 0.95 * expected,
        "%.2f h, %d of %d expected samples" % (duration / 3600, len(samples), expected),
        {"duration_s": duration, "samples": len(samples), "expected_samples": expected}))

    warm = started + float(cfg.get("warmup_s", 1800))
    steady = [s for s in samples if s["at"] >= warm] or samples

    # 1. No new kernel, OOM, thermal or hardware faults, and no reboot.
    faults = [f for s in samples for f in s.get("faults", [])]
    reboots = sum(1 for s in samples if s.get("rebooted"))
    by_class = {}
    for fault in faults:
        by_class[fault["class"]] = by_class.get(fault["class"], 0) + 1
    criteria.append(_criterion(
        "faults", "No new kernel, OOM, thermal or hardware fault records; no reboot",
        not faults and not reboots,
        "none" if not faults and not reboots else "%s%s" % (
            ", ".join("%s x%d" % kv for kv in sorted(by_class.items())), "; %d reboot(s)" % reboots if reboots else ""),
        {"by_class": by_class, "reboots": reboots, "examples": [f["message"] for f in faults[:5]]}))

    # 2. Temperatures under the existing limits; 3. other resource limits.
    rules = monitor_cfg.get("rules", [])
    for cid, title, chosen in (
            ("temperatures", "Temperatures stay under the existing limits (monitor level-1 thresholds)",
             [r for r in rules if _is_temperature(r)]),
            ("resource_limits", "Memory, swap, GPU memory and disk stay inside the monitor thresholds",
             [r for r in rules if not _is_temperature(r)])):
        worst, crossings, unread = {}, [], 0
        for rule in chosen:
            values = [ladder.evaluate_rule(s["signals"], rule) for s in samples]
            seen = [v["value"] for v in values if v["value"] is not None]
            unread += sum(1 for v in values if v["value"] is None)
            if seen:
                worst[rule.get("id") or rule["signal"]] = max(seen) if rule["op"] == ">=" else min(seen)
            crossings += [v["detail"] for v in values if v["level"] > 0][:3]
        criteria.append(_criterion(cid, title, not crossings and (not chosen or bool(worst)),
                                   "worst: " + ", ".join("%s=%.1f" % kv for kv in sorted(worst.items()))
                                   if not crossings else "; ".join(crossings),
                                   {"worst": worst, "unreadable_samples": unread}))

    # 4. Memory flat: each unit and the host; GPU memory within its drift bound.
    bounds = {**DEFAULT_BOUNDS, **(cfg.get("bounds") or {})}
    flat, notes, measured = True, [], {}
    for unit in cfg.get("units", []):
        name = unit["name"]
        points = [(s["at"], s["units"][name]["memory_mib"]) for s in steady
                  if s.get("units", {}).get(name, {}).get("memory_mib") is not None]
        slope = _slope_per_hour(points)
        peak = max((p[1] for p in points), default=None)
        limit = next((s["units"][name]["memory_max_mib"] for s in reversed(samples)
                      if s.get("units", {}).get(name, {}).get("memory_max_mib")), None)
        growth = float(unit.get("memory_growth_mib_per_h", 32.0))
        fraction = float(unit.get("memory_peak_fraction", 0.85))
        measured[name] = {"slope_mib_per_h": slope, "peak_mib": peak, "memory_max_mib": limit}
        if slope is None:
            flat = False
            notes.append("%s memory unreadable" % name)
            continue
        if slope > growth:
            flat = False
            notes.append("%s grows %.1f MiB/h > %.1f" % (name, slope, growth))
        if limit and peak is not None and peak > fraction * limit:
            flat = False
            notes.append("%s peak %.0f MiB > %.0f%% of MemoryMax %.0f" % (name, peak, 100 * fraction, limit))
    host_points = [(s["at"], s["signals"]["mem.available_mib"]) for s in steady if "mem.available_mib" in s["signals"]]
    host_slope = _slope_per_hour(host_points)
    measured["host_mem_available_slope_mib_per_h"] = host_slope
    if host_slope is not None and host_slope < float(bounds["host_mem_available_slope_mib_per_h"]):
        flat = False
        notes.append("host MemAvailable falls %.0f MiB/h" % -host_slope)
    gpu = [s["signals"]["gpu.mem_used_mib"] for s in steady if "gpu.mem_used_mib" in s["signals"]]
    if gpu:
        drift = max(gpu) - min(gpu)
        measured["gpu_mem_used_drift_mib"] = drift
        if drift > float(bounds["gpu_mem_used_drift_mib"]):
            flat = False
            notes.append("GPU memory drifts %.0f MiB > %.0f" % (drift, float(bounds["gpu_mem_used_drift_mib"])))
    criteria.append(_criterion("memory_flat", "Memory and GPU memory stay flat within the stated bounds", flat,
                               "; ".join(notes) or "flat", measured))

    # 5. Bounded restarts, units active.
    ok, notes, measured = True, [], {}
    for unit in cfg.get("units", []):
        name = unit["name"]
        counts = [s["units"][name]["n_restarts"] for s in samples
                  if s.get("units", {}).get(name, {}).get("n_restarts") is not None]
        restarts = (counts[-1] - counts[0]) if len(counts) >= 2 else None
        active = _fraction(s.get("units", {}).get(name, {}).get("active") == "active" for s in samples)
        measured[name] = {"restarts": restarts, "active_fraction": active}
        if restarts is None or restarts > int(unit.get("max_restarts", 0)):
            ok = False
            notes.append("%s restarts %s > %s" % (name, restarts, unit.get("max_restarts", 0)))
        if active is None or active < 0.99:
            ok = False
            notes.append("%s active %.0f%% of samples" % (name, 100 * (active or 0)))
    criteria.append(_criterion("restarts", "Services stay active with bounded restarts", ok,
                               "; ".join(notes) or "within bounds", measured))

    # 6. The monitor kept running and never backed off.
    if cfg.get("monitor_status"):
        seen = [s.get("monitor") for s in samples]
        fresh = _fraction(m is not None and m.get("age_s") is not None and m["age_s"] <= bounds["monitor_fresh_s"]
                          for m in seen)
        levels = sorted({m["level"] for m in seen if m and m.get("level") is not None})
        criteria.append(_criterion(
            "monitor", "The den-ops monitor ran throughout and stayed at level 0",
            fresh is not None and fresh >= bounds["monitor_fresh_fraction"] and levels == [0],
            "fresh %.0f%% of samples, levels seen %s" % (100 * (fresh or 0), levels),
            {"fresh_fraction": fresh, "levels": levels}))

    # 7. Brain online with fresh inputs (the host that runs the brain).
    if cfg.get("brain"):
        criteria.append(_brain_criterion(cfg, samples))

    # 8. The owner's scripted MVP checklist.
    if cfg.get("brain") or checklist is not None:
        criteria.append(_checklist_criterion(checklist))
    return criteria


def _brain_criterion(cfg, samples):
    fresh = {**DEFAULT_FRESHNESS, **(cfg.get("freshness") or {})}
    brain = [s.get("brain") or {} for s in samples]
    online = _fraction(b.get("reachable") and b.get("online") and b.get("healthz_ok") for b in brain)
    reachable = [b for b in brain if b.get("reachable")]
    occ_fresh = _fraction((b.get("occupancy_age_s") is not None and b["occupancy_age_s"] <= fresh["occupancy_fresh_s"])
                          for b in reachable)
    frigate = _fraction(b.get("health", {}).get("frigate") is True for b in reachable)
    # Occupied samples, skipping the first minute of each occupied stretch
    # (the loop wakes and the first JEV answer arrives).
    occupied, since = [], None
    for sample in samples:
        b = sample.get("brain") or {}
        if b.get("reachable") and b.get("present"):
            since = sample["at"] if since is None else since
            if sample["at"] - since >= 60:
                occupied.append(b)
        else:
            since = None
    health = _fraction(b.get("health", {}).get("jev") is True and b.get("health", {}).get("deep_stack") is True
                       for b in occupied)
    activity = _fraction(b.get("activity_age_s") is not None and b["activity_age_s"] <= fresh["activity_fresh_s"]
                         for b in occupied)
    # Measured, not judged: the longest stretch of occupied samples with a
    # stale pose (the roll gap AL-3 asked the first supervised run to measure).
    gap, worst_gap, previous = 0.0, 0.0, None
    for sample in samples:
        b = sample.get("brain") or {}
        stale = b.get("present") and (b.get("pose_age_s") is None or b["pose_age_s"] > 10)
        if stale and previous is not None:
            gap += sample["at"] - previous
            worst_gap = max(worst_gap, gap)
        elif not stale:
            gap = 0.0
        previous = sample["at"]
    checks = [
        ("online", online, fresh["online_fraction"]),
        ("occupancy fresh", occ_fresh, fresh["occupancy_fresh_fraction"]),
        ("frigate ok", frigate, fresh["frigate_ok_fraction"]),
    ]
    if occupied:
        checks += [("jev+deep stack ok while occupied", health, fresh["occupied_health_fraction"]),
                   ("activity fresh while occupied", activity, fresh["activity_fresh_fraction"])]
    failed = [name for name, value, need in checks if value is None or value < need]
    detail = ", ".join("%s %.1f%%" % (name, 100 * (value or 0)) for name, value, _ in checks)
    if not occupied:
        detail += "; no occupied stretch over 60 s (occupancy checks not exercised)"
    return _criterion("brain_inputs", "Brain online with fresh producer inputs", not failed, detail,
                      {"online": online, "occupancy_fresh": occ_fresh, "frigate_ok": frigate,
                       "occupied_samples": len(occupied), "occupied_health": health, "activity_fresh": activity,
                       "worst_pose_gap_s": worst_gap, "failed": failed})


def _checklist_criterion(checklist):
    if checklist is None:
        return _criterion("mvp_checklist", "Owner MVP behaviour checklist", None, "not run yet")
    failed = [r["id"] for r in checklist.get("results", []) if r["result"] == "fail"]
    missing = [r["id"] for r in checklist.get("results", []) if r["result"] == "skipped" and not r.get("optional")]
    passed = not failed and not missing
    return _criterion("mvp_checklist", "Owner MVP behaviour checklist", passed,
                      "all required steps passed" if passed else "failed: %s; skipped: %s" % (failed, missing),
                      {"results": {r["id"]: r["result"] for r in checklist.get("results", [])}})


def verdict(criteria):
    results = {c["result"] for c in criteria}
    return "fail" if "fail" in results else "incomplete" if "pending" in results else "pass"


# --- owner checklist -------------------------------------------------------

def _fixtures(state):
    return state.get("fixtures") or []


def _any_spot(state):
    return any((f.get("spot") or {}).get("on") for f in _fixtures(state))


def _all_off(state):
    return state.get("mode") == "off" and not _any_spot(state) and all(
        not (f.get("ambient") or {}).get("radial_level") and not (f.get("ambient") or {}).get("down_level")
        for f in _fixtures(state))


def _ambient_on(state):
    return any((f.get("ambient") or {}).get("radial_level") or (f.get("ambient") or {}).get("down_level")
               for f in _fixtures(state))


# id, instruction, expect(state) or None for an owner confirmation,
# timeout_s, hold_s (the condition must hold this long), optional
CHECKLIST = (
    ("empty_off", "Make sure nobody is in the den. The room should be off and stay off for 60 s.",
     _all_off, 300, 60, False),
    ("sofa_ambient", "Walk in and sit on the sofa. Ambient should fade up near the sofa.",
     lambda s: ((s.get("occupancy") or {}).get("zones") or {}).get("sofa") is True and _ambient_on(s), 30, 0, False),
    ("reading_spot", "Start reading a book on the sofa. A spotlight should come on over your shoulder.",
     lambda s: _any_spot(s) and s.get("mode") == "reading", 20, 0, False),
    ("reading_stop", "Put the book down and stop reading. The spotlight should fade out.",
     lambda s: not _any_spot(s), 30, 0, False),
    ("second_person", "With a second person in the den (optional): ambient only, no spotlight, for 20 s.",
     lambda s: ((s.get("occupancy") or {}).get("people_count") or 0) >= 2 and not _any_spot(s) and _ambient_on(s),
     60, 20, True),
    ("manual_pause", "In Home, change one den fixture by hand. That fixture should pause.",
     lambda s: any(f.get("paused_until") for f in _fixtures(s)), 15, 0, False),
    ("jev_loss", "Read on the sofa, then stop the activity loop (Lab panel, or POST .../activity-loop/stop). "
                 "Lighting should drop to ambient only. Resume the loop after this step.",
     lambda s: not _any_spot(s) and _ambient_on(s) and not ((s.get("inputs") or {}).get("health") or {}).get("deep_stack", True),
     90, 0, False),
    ("brain_offline", "Stop lighting-brain.service for about 30 s and watch Home: it should say 'brain offline' and "
                      "hold the fixtures. Start the brain again.", None, 0, 0, False),
    ("leave_off", "Leave the den. Everything should turn off within about 2 minutes.", _all_off, 180, 0, False),
    ("why_log", "Look back at Home's why line and decision log: did they explain each step?", None, 0, 0, False),
)


def run_checklist(fetch_state, ask, *, clock=time.time, sleep=time.sleep, steps=CHECKLIST, log=print):
    """Walk the owner through each step and verify it against the brain state.
    ``ask(prompt)`` returns the owner's answer; ``fetch_state()`` returns the
    state document or None."""
    results = []
    for step_id, instruction, expect, timeout_s, hold_s, optional in steps:
        log("\n[%s] %s" % (step_id, instruction))
        if expect is None:
            answer = ask("Did it behave as described? [y/n/s=skip] ").strip().lower()
            result = "pass" if answer.startswith("y") else "skipped" if answer.startswith("s") else "fail"
            results.append({"id": step_id, "result": result, "optional": optional, "confirmed_by": "owner"})
            continue
        answer = ask("Press Enter when you have done it (s=skip) ").strip().lower()
        if answer.startswith("s"):
            results.append({"id": step_id, "result": "skipped", "optional": optional})
            continue
        started = clock()
        held_since, evidence, result = None, None, "fail"
        while clock() - started <= timeout_s + hold_s:
            state = fetch_state()
            if isinstance(state, dict):
                decisions = state.get("decisions") or [{}]
                evidence = {"mode": state.get("mode"), "why": state.get("why"),
                            "last_rule": decisions[0].get("rule") if decisions else None}
                if expect(state):
                    held_since = clock() if held_since is None else held_since
                    if clock() - held_since >= hold_s:
                        result = "pass"
                        break
                else:
                    held_since = None
            sleep(1.0)
        elapsed = clock() - started
        log("  -> %s after %.1f s (%s)" % (result, elapsed, evidence))
        results.append({"id": step_id, "result": result, "optional": optional, "elapsed_s": round(elapsed, 1),
                        "evidence": evidence})
    return {"schema": "den-ops-soak-checklist-1", "results": results}


# --- receipts --------------------------------------------------------------

def receipt(cfg, criteria, samples, checklist=None):
    return {
        "schema": SOAK_RECEIPT_SCHEMA, "host": cfg["host"], "release": cfg.get("release"),
        "started_at": samples[0]["at"] if samples else None, "ended_at": samples[-1]["at"] if samples else None,
        "samples": len(samples), "verdict": verdict(criteria), "criteria": criteria,
        "checklist": checklist,
    }


def markdown(doc):
    lines = ["# Den activity lighting soak: %s" % doc.get("host", "release"), "",
             "- Release: `%s`" % doc.get("release"),
             "- Verdict: **%s**" % doc["verdict"].upper()]
    if doc.get("started_at"):
        lines.append("- Window: %s to %s UTC (%d samples)" % (
            time.strftime("%Y-%m-%d %H:%M", time.gmtime(doc["started_at"])),
            time.strftime("%Y-%m-%d %H:%M", time.gmtime(doc["ended_at"])), doc.get("samples", 0)))
    lines += ["", "| Criterion | Result | Detail |", "|---|---|---|"]
    for c in doc["criteria"]:
        lines.append("| %s | %s | %s |" % (c["title"], c["result"], str(c["detail"]).replace("|", "/")))
    return "\n".join(lines) + "\n"


def write_receipt(out_dir, doc, stem):
    write_atomic(os.path.join(out_dir, stem + ".json"), doc)
    with open(os.path.join(out_dir, stem + ".md"), "w", encoding="utf-8") as out:
        out.write(markdown(doc))


def combine(receipts, release=None):
    """The release verdict from every host's receipt."""
    criteria = []
    for doc in receipts:
        for c in doc["criteria"]:
            criteria.append({**c, "title": "%s: %s" % (doc["host"], c["title"])})
    releases = sorted({str(doc.get("release")) for doc in receipts})
    return {"schema": SOAK_RECEIPT_SCHEMA, "host": "+".join(doc["host"] for doc in receipts),
            "release": release or (releases[0] if len(releases) == 1 else releases),
            "started_at": min((d["started_at"] for d in receipts if d.get("started_at")), default=None),
            "ended_at": max((d["ended_at"] for d in receipts if d.get("ended_at")), default=None),
            "samples": sum(d.get("samples", 0) for d in receipts),
            "verdict": verdict(criteria) if len(releases) == 1 or release else "fail",
            "criteria": criteria, "hosts": [d["host"] for d in receipts]}
