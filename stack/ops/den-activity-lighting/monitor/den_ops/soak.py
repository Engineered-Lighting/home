"""The supervised soak (SOAK.md): sample, evaluate the written criteria, and
write a receipt (JSON plus a short markdown summary).

Run on each host for at least 3 h 10 min:
    python -m den_ops soak run --config soak.json --out <dir>
The owner's scripted MVP checklist runs after the completed soak on the LA host:
    python -m den_ops soak checklist --config soak.json --out <dir>
Then ``soak evaluate`` (re)builds a host receipt from its samples, and
``soak combine`` merges the hosts' receipts into the release verdict.
"""
from __future__ import annotations

import json
import os
import statistics
import time
import uuid
import math
import re

from . import SOAK_CONFIG_SCHEMA, SOAK_RECEIPT_SCHEMA
from . import monitor as mon
from . import policy as ladder
from . import deployment
from .hostio import read_token
from .hostio import write_atomic

MIN_DURATION_S = 3 * 3600
DEFAULT_DURATION_S = 11400
MAX_SAMPLE_GAP_S = 120
CHECKLIST_SCHEMA = "den-ops-soak-checklist-2"
QWEN_PROFILE_KEYS = {'model_version', 'served_model', 'artifact_sha256', 'engine_epoch', 'runtime_revision', 'adapter_revision'}


def _qwen_profile(value):
    return (isinstance(value, dict) and set(value) == QWEN_PROFILE_KEYS
            and all(isinstance(v, str) and 0 < len(v) <= 512 for v in value.values())
            and re.fullmatch(r'[0-9a-f]{64}', value['artifact_sha256']) is not None)


def _qwen_workload(value):
    if (not isinstance(value, dict) or not {'schema', 'producer_epoch', 'state', 'blocked', 'completed', 'model'} <= set(value)
            or value.get('schema') != 'den-qwen-workload-1'
            or not isinstance(value.get('producer_epoch'), str) or not 0 < len(value['producer_epoch']) <= 100
            or value.get('state') not in ('idle', 'running', 'refused', 'stopped')
            or type(value.get('blocked')) is not bool
            or type(value.get('completed')) is not int or value['completed'] < 0
            or not (value.get('model') is None and value['completed'] == 0 or _qwen_profile(value.get('model')))):
        raise ValueError('actual Qwen workload evidence unavailable or malformed')
    return {k: value[k] for k in ('schema', 'producer_epoch', 'state', 'blocked', 'completed', 'model')}
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
    if not isinstance(cfg, dict) or cfg.get("schema") != SOAK_CONFIG_SCHEMA:
        raise ValueError("soak config must be %s" % SOAK_CONFIG_SCHEMA)
    if cfg.get("host") not in deployment.HOSTS:
        raise ValueError("unknown soak host")
    for key in ("duration_s", "interval_s", "warmup_s"):
        if key in cfg and (isinstance(cfg[key], bool) or not isinstance(cfg[key], (int, float)) or not math.isfinite(cfg[key]) or cfg[key] < 0):
            raise ValueError("invalid soak timing: " + key)
    if cfg.get("interval_s", 60) != 60 or cfg.get("duration_s", DEFAULT_DURATION_S) < DEFAULT_DURATION_S:
        raise ValueError("admission soak uses 60 s sampling for at least 11400 s")
    if cfg['host'] == 'victoria-jetson' and not _qwen_profile((cfg.get('loop') or {}).get('qwen_profile')):
        raise ValueError('occupied soak requires the commissioned actual Qwen profile')
    required_units, _ = deployment.required_inventory(cfg.get('execution_profile'))
    if {(u.get("name"), u.get("scope")) for u in cfg.get("units", [])} != required_units[cfg["host"]]:
        raise ValueError("required deployed units/scopes missing")
    names = [u.get("name") for u in cfg.get("units", [])]
    if not names or len(names) != len(set(names)):
        raise ValueError("unique required soak units missing")
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
        "show", name, "--property=ActiveState,NRestarts,MemoryCurrent,MemoryMax,WorkingDirectory,ExecStart")
    code, text = host.run(argv)
    props = dict(line.split("=", 1) for line in str(text or "").splitlines() if "=" in line)
    current, limit = _int_or_none(props.get("MemoryCurrent")), _int_or_none(props.get("MemoryMax"))
    restarts = _int_or_none(props.get("NRestarts"))
    return {
        "active": props.get("ActiveState"),
        "n_restarts": restarts,
        "memory_mib": None if current is None or current >= 2 ** 63 else current / 1048576.0,
        "memory_max_mib": None if limit is None or limit >= 2 ** 63 else limit / 1048576.0,
        "readable": code == 0 and restarts is not None and restarts >= 0 and current is not None and 0 <= current < 2**63 and limit is not None and 0 < limit < 2**63 and props.get("ActiveState") in ("active", "inactive", "failed", "activating", "deactivating"),
        "working_directory": props.get("WorkingDirectory"), "exec_start": props.get("ExecStart"),
    }


def read_brain(host, brain):
    code, health = host.http("GET", brain["healthz_url"], timeout_s=5)
    state_code, state = host.http("GET", brain["state_url"], timeout_s=5)
    out = {"healthz_ok": code == 200 and isinstance(health, dict) and health.get("ok") is True,
           "reachable": state_code == 200 and isinstance(state, dict) and state.get("schema") == "activity-lighting-state-1" and isinstance(state.get("generated_at"), (int, float)) and 0 <= host.now() - state["generated_at"] <= 5}
    if not out["reachable"]:
        return out
    occupancy = state.get("occupancy") or {}
    inputs = state.get("inputs") or {}
    fixtures = state.get("fixtures") or []
    out.update({
        "online": state.get("brain") == "online",
        "mode": state.get("mode"),
        "why": str(state.get("why", ""))[:120],
        "present": occupancy.get("present") is True,
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
    if not isinstance(doc, dict):
        return None
    return {"level": doc.get("level"), "step": doc.get("step"), "latched": doc.get("latched"),
            "age_s": None if doc.get("checked_at") is None else now - float(doc["checked_at"])}


def sample_once(cfg, monitor_cfg, host, journal_state):
    """One sample. ``journal_state`` carries the kernel-journal cursor between
    samples (the first sample only records where to start)."""
    now = host.now()
    signals, notices = mon.collect_signals(monitor_cfg, host)
    faults, fault_notices = mon.fault_records(monitor_cfg, host, journal_state)
    unit_notices, failed = mon.failed_unit_notices(monitor_cfg, host)
    reasons = notices + fault_notices + unit_notices
    for source in ("faults", "failed_units"):
        if not (monitor_cfg.get("sources") or {}).get(source):
            reasons.append(mon._evidence_error("source." + source, "required source absent"))
    sample = {
        "at": now, "monotonic_at": host.monotonic(), "signals": signals,
        "boot_id": (journal_state.get("journal") or {}).get("boot_id"),
        "evidence": mon.evidence_report(reasons), "failed_units": failed,
        "faults": faults,
        "notices": [n["detail"] for n in notices + fault_notices + unit_notices],
        "rebooted": any(n["signal"] == "host.rebooted" for n in fault_notices),
        "units": {u["name"]: read_unit(host, u["name"], u.get("scope", "system")) for u in cfg.get("units", [])},
    }
    if cfg.get("brain"):
        sample["brain"] = read_brain(host, cfg["brain"])
    if cfg.get("monitor_status"):
        sample["monitor"] = read_monitor_status(host, cfg["monitor_status"], now)
    if cfg.get("loop"):
        loop = cfg["loop"]
        try:
            token = read_token(loop["token_file"])
            code, state = host.http("GET", loop["status_url"], token=token, ca_file=loop.get("ca_file"), timeout_s=5)
            deep = state.get("deep_stack") if code == 200 and isinstance(state, dict) else None
            if not isinstance(deep, dict) or not isinstance(deep.get("rolls"), int) or not deep.get("producer_epoch"):
                raise ValueError("activity loop status lacks roll/epoch evidence")
            qwen = _qwen_workload(deep.get('qwen_workload'))
            if qwen['model'] is not None and qwen['model'] != loop.get('qwen_profile'):
                raise ValueError('Qwen model differs from commissioned soak profile')
            if (state.get('state') not in {'running', 'paused', 'stopped'}
                    or 'last_occupied_age_s' not in state
                    or (state['last_occupied_age_s'] is not None
                        and (not _finite_number(state['last_occupied_age_s']) or state['last_occupied_age_s'] < 0))):
                raise ValueError('activity loop occupancy telemetry unavailable')
            sample["loop"] = {"rolls": deep["rolls"], "session_id": deep.get("session_id"), "producer_epoch": deep["producer_epoch"], 'qwen_workload': qwen,
                              'state': state['state'], 'occupancy_age_s': state['last_occupied_age_s']}
        except (OSError, ValueError, KeyError):
            sample["evidence"]["ok"] = False
            sample["evidence"]["errors"].append({"source": "loop", "detail": "authenticated recording/Qwen workload evidence unavailable or incompatible"})
    return sample


def run(cfg, monitor_cfg, host, out_dir, *, sleep=time.sleep, duration_s=None, log=print):
    """One immutable attempt. Invalid or interrupted attempts retain partial evidence."""
    manifest = cfg.get("_deployment")
    if manifest is None:
        raise ValueError("run requires a deployment manifest")
    public_cfg = {k: v for k, v in cfg.items() if k != "_deployment"}
    provenance = deployment.verify(manifest, public_cfg, monitor_cfg, host)
    if not provenance["ok"]:
        raise ValueError("deployment preflight: " + "; ".join(provenance["errors"]))
    duration = float(duration_s if duration_s is not None else cfg.get("duration_s", DEFAULT_DURATION_S))
    interval = float(cfg.get("interval_s", 60))
    os.makedirs(out_dir, exist_ok=False)
    samples_path = os.path.join(out_dir, "samples-%s.jsonl" % cfg["host"])
    journal_state = {"journal": {}}
    run_id = str(uuid.uuid4())
    started, mono_started = host.now(), host.monotonic()
    meta = {**provenance, "host": cfg["host"], "run_id": run_id, "started_at": started, "complete": False}
    write_atomic(os.path.join(out_dir, "soak-meta-%s.json" % cfg["host"]), meta)
    samples = []
    try:
        with open(samples_path, "x", encoding="utf-8") as out:
            while True:
                sample = sample_once(cfg, monitor_cfg, host, journal_state)
                sample.update(run_id=run_id, manifest_sha256=provenance["manifest_sha256"])
                spec = manifest["hosts"][cfg["host"]]
                for unit in spec["units"]:
                    seen = sample["units"].get(unit["name"], {})
                    observed_exec = deployment.execution_identity(seen.get("exec_start"))
                    expected_exec = deployment.execution_identity(unit["exec_start"])
                    if not seen.get("readable") or seen.get("working_directory") != unit["working_directory"] or not observed_exec or not expected_exec or observed_exec != expected_exec:
                        sample["evidence"]["ok"] = False
                        sample["evidence"]["errors"].append({"source": unit["name"], "detail": "unit execution/counter identity missing or changed"})
                samples.append(sample)
                out.write(json.dumps(sample, sort_keys=True) + "\n")
                out.flush()
                if not sample["evidence"]["ok"] or sample["faults"] or sample["rebooted"]:
                    break
                if host.monotonic() - mono_started >= duration:
                    meta["complete"] = True
                    break
                sleep(max(0.0, interval - (host.monotonic() - sample["monotonic_at"])))
    finally:
        final = deployment.verify(manifest, public_cfg, monitor_cfg, host)
        meta.update(ended_at=host.now(), provenance_final=final)
        write_atomic(os.path.join(out_dir, "soak-meta-%s.json" % cfg["host"]), meta)
        criteria = evaluate(cfg, monitor_cfg, samples)
        criteria.append(_criterion("attempt_complete", "Attempt completed with unchanged deployment", meta["complete"] and final["ok"], str(final)))
        write_receipt(out_dir, receipt(cfg, criteria, samples), "soak-" + cfg["host"])
    log("soak: %d samples; complete=%s" % (len(samples), meta["complete"]))
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
    monotonic = [s.get("monotonic_at") for s in samples]
    timed = all(isinstance(t, (int, float)) and math.isfinite(t) for t in monotonic)
    duration = monotonic[-1] - monotonic[0] if timed else 0
    gaps = [b - a for a, b in zip(monotonic, monotonic[1:])] if timed else []
    continuous = timed and all(0 < g <= MAX_SAMPLE_GAP_S for g in gaps) and all(b["at"] > a["at"] and abs((b["at"]-a["at"])-(b["monotonic_at"]-a["monotonic_at"])) <= 5 for a,b in zip(samples,samples[1:]))
    expected = max(1, int(duration // interval) + 1)
    need = max(float(cfg.get("duration_s", min_duration_s)), float(min_duration_s))
    criteria.append(_criterion(
        "duration", "Soak ran at least 3 h with steady sampling",
        duration >= need and len(samples) >= 0.95 * expected and continuous,
        "%.2f h, %d of %d expected samples" % (duration / 3600, len(samples), expected),
        {"duration_s": duration, "samples": len(samples), "expected_samples": expected}))

    manifest = cfg.get("_deployment")
    identity_ok = manifest is not None and len({s.get("run_id") for s in samples}) == 1 and bool(samples[0].get("run_id")) and all(s.get("manifest_sha256") == deployment.digest(manifest) for s in samples)
    criteria.append(_criterion("identity", "Samples bind one deployment and attempt", identity_ok, "matching manifest/run" if identity_ok else "missing or mixed identity"))
    evidence_ok = all(s.get("evidence", {}).get("ok") is True and not s["evidence"].get("errors") and all(u.get("readable") is True for u in s.get("units", {}).values()) for s in samples)
    criteria.append(_criterion("evidence", "Required safety evidence complete at every sample", evidence_ok, "complete" if evidence_ok else "required collection failed or missing"))
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
        criteria.append(_criterion(cid, title, not crossings and unread == 0 and (not chosen or bool(worst)),
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
    if host_slope is None:
        flat = False
        notes.append("host memory unreadable")
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
        counts = [s.get("units", {}).get(name, {}).get("n_restarts") for s in samples]
        if any(type(count) is not int or count < 0 for count in counts):
            ok = False
            notes.append("%s restart counters missing or malformed" % name)
            counts = []
        restarts = (counts[-1] - counts[0]) if len(counts) >= 2 else None
        active = _fraction(s.get("units", {}).get(name, {}).get("active") == "active" for s in samples)
        measured[name] = {"restarts": restarts, "active_fraction": active}
        if restarts is None or restarts < 0 or restarts > int(unit.get("max_restarts", 0)):
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
        fresh = _fraction(m is not None and m.get("age_s") is not None and 0 <= m["age_s"] <= bounds["monitor_fresh_s"]
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

    if cfg["host"] == "victoria-jetson":
        criteria.append(_roll_exposure(cfg, samples))

    # 8. The owner's scripted MVP checklist.
    if cfg.get("brain") or checklist is not None:
        criteria.append(_checklist_criterion(checklist, cfg=cfg, samples=samples))
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
    if not occupied:
        checks.append(("occupied exposure", None, 1))
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


def _roll_exposure(cfg, samples):
    best = {'seconds': 0, 'rolls': 0, 'qwen_completed': 0}
    for span in _loaded_spans(cfg, samples):
        measured = {k: span[k] for k in best}
        if measured['seconds'] > best['seconds']:
            best = measured
        if span['seconds'] >= 3600 and span['rolls'] >= 10:
            return _criterion('occupied_exposure', 'Sustained occupied deep-stack and Qwen exposure', True,
                              'at least 60 min, 10 rolls and Qwen completion progress each sample', measured)
    return _criterion('occupied_exposure', 'Sustained occupied deep-stack and Qwen exposure', None,
                      'need one occupied 60 min span with 10 rolls and actual Qwen progress after warmup', best)


def _loaded_spans(cfg, samples):
    """One process/model and advancing executions, without stitching stalls."""
    if not samples:
        return
    warm = samples[0]['at'] + float(cfg.get('warmup_s', 1800))
    expected = (cfg.get('loop') or {}).get('qwen_profile')
    previous = first = None
    for sample in samples:
        loop = sample.get('loop') or {}
        try:
            qwen = _qwen_workload(loop.get('qwen_workload'))
        except ValueError:
            previous = first = None
            continue
        if (sample['at'] < warm or not loop.get('session_id') or not loop.get('producer_epoch')
                or loop.get('state') != 'running' or not _occupancy_current(loop.get('occupancy_age_s'))
                or type(loop.get('rolls')) is not int or loop['rolls'] < 0
                or qwen['state'] != 'running' or qwen['blocked'] or not _qwen_profile(expected) or qwen['model'] != expected):
            previous = first = None
            continue
        current = dict(at=sample['at'], rolls=loop['rolls'], completed=qwen['completed'],
                       epoch=(loop['producer_epoch'], qwen['producer_epoch']))
        if (previous is None or current['epoch'] != previous['epoch']
                or not 0 < current['at'] - previous['at'] <= _occupied_sample_gap(cfg)
                or current['completed'] <= previous['completed'] or current['rolls'] < previous['rolls']):
            first = current
        previous = current
        yield dict(started_at=first['at'], ended_at=current['at'], seconds=current['at'] - first['at'],
                   rolls=current['rolls'] - first['rolls'], qwen_completed=current['completed'] - first['completed'])


def _checklist_criterion(checklist, *, cfg=None, samples=None):
    if checklist is None:
        return _criterion("mvp_checklist", "Owner MVP behaviour checklist", None, "not run yet")
    expected = {step[0]: step[5] for step in CHECKLIST}
    specifications = {step[0]: step for step in CHECKLIST}
    rows = checklist.get("results") if isinstance(checklist, dict) else None
    errors = []
    if not isinstance(checklist, dict) or checklist.get("schema") != CHECKLIST_SCHEMA or not isinstance(rows, list):
        errors.append("invalid checklist schema")
        rows = []
    ids = [r.get("id") for r in rows if isinstance(r, dict)]
    if len(ids) != len(rows) or len(ids) != len(set(ids)) or set(ids) != set(expected):
        errors.append("required checklist rows missing, duplicated or unknown")
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("result") not in ("pass", "fail", "skipped") or row.get("optional") is not expected.get(row.get("id")):
            errors.append("invalid checklist result")
        elif row["result"] != "pass" and not (row["result"] == "skipped" and expected.get(row["id"])):
            errors.append("step did not pass: " + row["id"])
        if row.get("result") == "pass" and row.get("id") in specifications:
            step = specifications[row["id"]]
            if step[2] is None:
                if row.get("confirmed_by") != "owner":
                    errors.append("owner confirmation missing: " + row["id"])
            elif not isinstance(row.get("evidence"), dict) or not _finite_number(row.get("elapsed_s")) or not step[4] <= row["elapsed_s"] <= step[3] + step[4] + 1:
                errors.append("step observation evidence/timing missing: " + row["id"])
    if cfg is not None:
        manifest = cfg.get("_deployment")
        if not manifest or checklist.get("manifest_sha256") != deployment.digest(manifest) or checklist.get("host") != cfg["host"]:
            errors.append("checklist deployment/host mismatch")
        if not samples or checklist.get("run_id") != samples[0].get("run_id"):
            errors.append("checklist run mismatch")
        if not samples or not isinstance(checklist.get("started_at"), (int, float)) or checklist["started_at"] < samples[-1]["at"] or not isinstance(checklist.get("ended_at"), (int, float)) or checklist["ended_at"] < checklist["started_at"]:
            errors.append("checklist must follow the resource soak")
    return _criterion("mvp_checklist", "Owner MVP behaviour checklist", not errors, "; ".join(errors) or "all required steps passed", {"results": {r.get("id"): r.get("result") for r in rows if isinstance(r, dict)}})


def verdict(criteria):
    if not criteria or any(c.get("result") not in ("pass", "fail", "pending") for c in criteria):
        return "fail"
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


def _sofa_ambient(state):
    """Use the brain's commissioned distance rule and existing 10 s ambient position bound."""
    occupancy = state.get("occupancy") or {}
    inputs = state.get("inputs") or {}
    age = inputs.get("position_age_s")
    return (occupancy.get("present") is True and inputs.get("at_sofa") is True
            and _finite_number(age) and 0 <= age <= 10 and _ambient_on(state))


# id, instruction, expect(state) or None for an owner confirmation,
# timeout_s, hold_s (the condition must hold this long), optional
CHECKLIST = (
    ("empty_off", "Make sure nobody is in the den. The room should be off and stay off for 60 s.",
     _all_off, 300, 60, False),
    ("sofa_ambient", "Walk in and sit on the sofa. Ambient should fade up near the sofa.",
     _sofa_ambient, 30, 0, False),
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
                      "disable automatic spotlights, hold ambient and preserve manual pauses. Start the brain again.", None, 0, 0, False),
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
        answer = ask("Press Enter to begin, then perform the action now (s=skip) ").strip().lower()
        if answer.startswith("s"):
            results.append({"id": step_id, "result": "skipped", "optional": optional})
            continue
        started = clock()
        held_since, evidence, result = None, None, "fail"
        while clock() - started <= timeout_s + hold_s:
            state = fetch_state()
            if isinstance(state, dict) and state.get("schema") == "activity-lighting-state-1" and state.get("brain") == "online" and isinstance(state.get("generated_at"), (int, float)) and 0 <= clock() - state["generated_at"] <= 3:
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
            else:
                held_since = None
            sleep(1.0)
        elapsed = clock() - started
        log("  -> %s after %.1f s (%s)" % (result, elapsed, evidence))
        results.append({"id": step_id, "result": result, "optional": optional, "elapsed_s": round(elapsed, 1),
                        "evidence": evidence})
    return {"schema": CHECKLIST_SCHEMA, "results": results}


# --- receipts --------------------------------------------------------------

def receipt(cfg, criteria, samples, checklist=None):
    return {
        "schema": SOAK_RECEIPT_SCHEMA, "host": cfg["host"], "release": (cfg.get("_deployment") or {}).get("deployment_id", cfg.get("release")),
        "manifest_sha256": deployment.digest(cfg["_deployment"]) if cfg.get("_deployment") else None,
        "run_id": samples[0].get("run_id") if samples else None,
        "occupied_window": _occupied_window(cfg, samples),
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


def _occupied_window(cfg, samples):
    if not samples:
        return None
    if cfg['host'] == 'victoria-jetson':
        spans = [s for s in _loaded_spans(cfg, samples) if s['seconds'] >= 3600 and s['rolls'] >= 10]
        best = max(spans, key=lambda s: s['seconds'], default=None)
        return None if best is None else {k: best[k] for k in ('started_at', 'ended_at')}
    warm = samples[0]["at"] + float(cfg.get("warmup_s", 1800))
    since, epoch, best, previous_at = None, None, None, None
    for sample in samples:
        if cfg["host"] == "la-host":
            brain = sample.get('brain') or {}
            active = brain.get('present') is True and _occupancy_current(brain.get('occupancy_age_s'))
            this_epoch = "brain"
        else:
            loop = sample.get("loop") or {}
            active, this_epoch = bool(loop.get("session_id")), loop.get("producer_epoch")
        if (sample["at"] < warm or not active or this_epoch != epoch
                or (previous_at is not None and not 0 < sample['at'] - previous_at <= _occupied_sample_gap(cfg))):
            since = None
        previous_at = sample['at']
        epoch = this_epoch
        if sample["at"] >= warm and active:
            if since is None:
                since = sample["at"]
            if sample["at"] - since >= 3600 and (best is None or sample["at"] - since > best["ended_at"] - best["started_at"]):
                best = {"started_at": since, "ended_at": sample["at"]}
    return best


def _occupancy_current(age):
    return _finite_number(age) and 0 <= age <= DEFAULT_FRESHNESS['occupancy_fresh_s']


def _occupied_sample_gap(cfg):
    # Allow collection jitter, but no missing interval inside the occupied hour.
    return min(MAX_SAMPLE_GAP_S, 1.5 * float(cfg.get('interval_s', 60)))


def _finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _valid_receipt(doc):
    if not isinstance(doc, dict) or doc.get("schema") != SOAK_RECEIPT_SCHEMA:
        return False
    if not isinstance(doc.get("run_id"), str) or not doc["run_id"]:
        return False
    if not _finite_number(doc.get("started_at")) or not _finite_number(doc.get("ended_at")) or doc["ended_at"] <= doc["started_at"]:
        return False
    if type(doc.get("samples")) is not int or doc["samples"] <= 0:
        return False
    rows = doc.get("criteria")
    if not isinstance(rows, list) or not rows or any(not isinstance(c, dict) or not isinstance(c.get("id"), str) or not c["id"] or not isinstance(c.get("title"), str) or c.get("result") not in ("pass", "fail", "pending") for c in rows):
        return False
    return doc.get("verdict") == verdict(rows)


def combine(receipts, release=None, manifest=None):
    """Combine exactly the two qualified hosts; labels cannot override provenance."""
    criteria = []
    hosts = [d.get("host") if isinstance(d, dict) else None for d in receipts]
    valid = manifest is not None and len(receipts) == 2 and set(hosts) == deployment.HOSTS
    valid = valid and all(_valid_receipt(d) for d in receipts)
    if manifest is not None:
        deployment.validate(manifest)
        valid = valid and all(d.get("manifest_sha256") == deployment.digest(manifest) and d.get("release") == manifest["deployment_id"] for d in receipts)
        valid = valid and (release is None or release == manifest["deployment_id"])
    starts = [d.get("started_at") for d in receipts if isinstance(d, dict) and _finite_number(d.get("started_at"))]
    ends = [d.get("ended_at") for d in receipts if isinstance(d, dict) and _finite_number(d.get("ended_at"))]
    common_start, common_end = max(starts, default=0), min(ends, default=0)
    valid = valid and len(starts) == len(ends) == 2 and common_end - common_start >= MIN_DURATION_S
    valid = valid and max(starts, default=0) - min(starts, default=0) <= MAX_SAMPLE_GAP_S
    criteria.append(_criterion("combined_identity", "Two hosts bind the same deployment and common 3 h window", bool(valid), "compatible" if valid else "missing/malformed/mismatched receipt, deployment or overlap"))
    for doc in receipts:
        if not isinstance(doc, dict):
            continue
        rows = doc.get("criteria") if isinstance(doc.get("criteria"), list) else []
        for criterion in rows:
            if isinstance(criterion, dict) and isinstance(criterion.get("title"), str):
                criteria.append({**criterion, "title": "%s: %s" % (doc.get("host"), criterion["title"])})
        required = {"duration", "identity", "evidence", "faults", "temperatures", "resource_limits", "memory_flat", "restarts", "monitor", "attempt_complete"}
        required |= {"brain_inputs", "mvp_checklist"} if doc.get("host") == "la-host" else {"occupied_exposure"}
        present = [c.get("id") for c in rows if isinstance(c, dict) and isinstance(c.get("id"), str)]
        criteria.append(_criterion("receipt_complete_" + str(doc.get("host")), "Receipt has every required criterion", len(present) == len(set(present)) and required <= set(present), "required criteria checked"))
    windows = [d.get("occupied_window") if isinstance(d, dict) else None for d in receipts]
    occupied_ok = len(windows) == 2 and all(_valid_receipt(d) for d in receipts) and all(isinstance(w, dict) and _finite_number(w.get("started_at")) and _finite_number(w.get("ended_at")) for w in windows)
    if occupied_ok:
        occupied_ok = all(d["started_at"] <= w["started_at"] < w["ended_at"] <= d["ended_at"] for d,w in zip(receipts,windows))
        occupied_ok = occupied_ok and min(w["ended_at"] for w in windows) - max(w["started_at"] for w in windows) >= 3600
    criteria.append(_criterion("common_occupied", "Both hosts observed the same occupied hour", bool(occupied_ok), "common occupied exposure required"))
    return {"schema": SOAK_RECEIPT_SCHEMA, "host": "+".join(str(h) for h in hosts),
            "release": manifest["deployment_id"] if manifest else release, "manifest_sha256": deployment.digest(manifest) if manifest else None,
            "started_at": common_start, "ended_at": common_end, "samples": sum(d["samples"] for d in receipts if isinstance(d,dict) and type(d.get("samples")) is int),
            "verdict": verdict(criteria), "criteria": criteria, "hosts": hosts}
