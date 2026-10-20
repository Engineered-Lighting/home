"""One monitor pass: read the host, step the back-off ladder, act, alert.

Run by ``den-ops-monitor.timer`` every minute. The only side effects are:
- the activity loop's own routes (stop / resume) and any configured cadence
  calls, through ``loop`` and ``cadence`` in the config;
- POSTing this host's status to the gateway (the Home banner);
- a Home Assistant notify call on a level change or a new notice;
- the monitor's own state file.
It never clears a failed unit, restarts a service, or writes a baseline.
"""
from __future__ import annotations

import json
import os
import re

from . import CONFIG_SCHEMA, STATE_SCHEMA, STATUS_SCHEMA, STEPS, UNITS_BASELINE_SCHEMA
from . import policy as ladder
from . import signals as sig
from .hostio import read_private, read_token, write_atomic

_SIGNAL_SAFE = re.compile(r"[^a-z0-9_.:-]+")
_RULE_KEYS = {"id", "signal", "op", "levels", "clear_margin", "label", "unit", "required"}
_TOP_KEYS = {"schema", "host", "role", "state_dir", "policy", "sources", "rules", "loop", "cadence", "remote",
             "gateway", "home_assistant", "_note"}


def load_config(path):
    with open(path, encoding="utf-8") as handle:
        cfg = json.load(handle)
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    if not isinstance(cfg, dict) or cfg.get("schema") != CONFIG_SCHEMA:
        raise ValueError("config must be %s" % CONFIG_SCHEMA)
    unknown = set(cfg) - _TOP_KEYS
    if unknown:
        raise ValueError("unknown config keys: %s" % ", ".join(sorted(unknown)))
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", str(cfg.get("host", ""))):
        raise ValueError("host must be a short lower-case name (it is the gateway status key)")
    for rule in cfg.get("rules", []):
        if set(rule) - _RULE_KEYS or rule.get("op") not in (">=", "<=") or not isinstance(rule.get("levels"), list) \
                or len(rule["levels"]) != 3 or not rule.get("signal"):
            raise ValueError("bad rule: %r" % (rule,))
        limits = [v for v in rule["levels"] if v is not None]
        ordered = sorted(limits) if rule["op"] == ">=" else sorted(limits, reverse=True)
        if limits != ordered:
            raise ValueError("rule %s: levels must get stricter step by step" % rule["signal"])
    policy = cfg.get("policy") or {}
    if set(policy) - set(ladder.DEFAULT_POLICY):
        raise ValueError("unknown policy keys")
    for action in ((cfg.get("cadence") or {}).get("reduce", []) + (cfg.get("cadence") or {}).get("restore", [])):
        if action.get("method") not in ("POST", "PUT", "PATCH") or not str(action.get("url", "")).startswith("https://"):
            raise ValueError("cadence actions are POST/PUT/PATCH to https URLs")
    urls = [(cfg.get("loop") or {}).get(key) for key in ("status_url", "stop_url", "resume_url")]
    urls += [(cfg.get("remote") or {}).get("url"), (cfg.get("gateway") or {}).get("status_url")]
    for url in [u for u in urls if u is not None]:
        if not _secure_url(url):
            raise ValueError("%s must be https, or http to loopback" % url)
    ha = cfg.get("home_assistant")
    if ha and not (_secure_url(ha.get("url", "")) or (ha.get("allow_plain_http") is True
                                                       and str(ha.get("url", "")).startswith("http://"))):
        raise ValueError("home_assistant.url must be https (or set allow_plain_http for a LAN/tailnet-only path)")
    return cfg


def _secure_url(url):
    url = str(url)
    return url.startswith("https://") or url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))


def state_dir(cfg):
    return cfg.get("state_dir") or os.environ.get("DEN_OPS_STATE_DIR") or os.path.join(os.getcwd(), "state")


def load_state(path, cfg):
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
        if state.get("schema") == STATE_SCHEMA and state.get("host") == cfg["host"]:
            return state
    except (OSError, ValueError):
        pass
    return {"schema": STATE_SCHEMA, "host": cfg["host"], "level": 0, "step": STEPS[0], "latched": False,
            "since": None, "pending": 0, "below_since": None, "fault_hold": {}, "journal": {},
            "stopped_by_monitor": False, "cadence_reduced": False, "notified": {}, "history": []}


def _notice(signal, detail, alert=True):
    """A level-0 reason shown in the status. ``alert=False`` keeps it out of
    Home Assistant notifications (it is informational)."""
    return {"signal": signal, "level": 0, "hold": 0, "immediate": False, "notice": True, "quiet": not alert,
            "detail": detail}


# --- collection ------------------------------------------------------------

def collect_signals(cfg, host):
    sources = cfg.get("sources") or {}
    out, notices = {}, []
    zones = sources.get("thermal_zones")
    if zones:
        out.update(sig.read_thermal_zones(zones.get("root", "/sys/class/thermal"), zones.get("exclude_types", ())))
    hwmon = sources.get("hwmon")
    if hwmon:
        out.update(sig.read_hwmon(hwmon.get("root", "/sys/class/hwmon")))
    if sources.get("meminfo"):
        try:
            out.update(sig.parse_meminfo(host.read_text(sources["meminfo"])))
        except OSError:
            notices.append(_notice("mem.unreadable", "memory counters unreadable"))
    if sources.get("nvidia_smi"):
        code, text = host.run(("nvidia-smi",) + sig.NVIDIA_SMI_ARGS)
        gpu = sig.parse_nvidia_smi(text) if code == 0 else {}
        if gpu:
            out.update(gpu)
        else:
            notices.append(_notice("gpu.unreadable", "nvidia-smi unreadable"))
    if sources.get("tegrastats"):
        line = host.first_line(("tegrastats", "--interval", "1000"))
        tegra = sig.parse_tegrastats(line) if line else {}
        if tegra:
            out.update(tegra)
        else:
            notices.append(_notice("tegrastats.unreadable", "tegrastats unreadable"))
    if sources.get("disks"):
        out.update(sig.read_disks(sources["disks"]))
    return out, notices


def failed_unit_notices(cfg, host):
    spec = (cfg.get("sources") or {}).get("failed_units")
    if not spec:
        return [], {}
    baseline, notices = {}, []
    try:
        doc = json.loads(read_private(spec["baseline"]))
        if doc.get("schema") != UNITS_BASELINE_SCHEMA:
            raise ValueError("schema")
        baseline = {scope: set(doc.get("units", {}).get(scope, [])) for scope in ("system", "user")}
    except (OSError, ValueError, PermissionError) as error:
        notices.append(_notice("units.baseline", "failed-unit baseline unreadable (%s); every failed unit is reported"
                               % type(error).__name__))
        baseline = {"system": set(), "user": set()}
    current = {}
    for scope in spec.get("scopes", ["system"]):
        argv = ("systemctl",) + (("--user",) if scope == "user" else ()) + sig.SYSTEMCTL_FAILED_ARGS
        code, text = host.run(argv)
        if code not in (0, 1) and not text:
            notices.append(_notice("units.%s.unreadable" % scope, "%s failed units unreadable" % scope))
            continue
        current[scope] = sig.parse_failed_units(text)
        for unit in current[scope]:
            if unit not in baseline.get(scope, set()):
                notices.append(_notice("unit.%s" % unit, "%s unit %s failed (not in the recorded baseline)" % (scope, unit)))
    return notices, current


def fault_records(cfg, host, state, now=None):
    """New kernel fault records since the last check. Updates state["journal"]."""
    spec = (cfg.get("sources") or {}).get("faults")
    if not spec:
        return [], []
    notices = []
    journal = dict(state.get("journal") or {})
    try:
        boot_id = host.read_text(spec.get("boot_id", "/proc/sys/kernel/random/boot_id")).strip()
    except OSError:
        boot_id = None
    try:
        baseline = sig.load_fault_baseline(spec["baseline"])
    except (OSError, ValueError) as error:
        notices.append(_notice("faults.baseline", "fault baseline unreadable (%s); no record is ignored"
                               % type(error).__name__))
        baseline = []
    base = ("journalctl", "-k", "-o", "json", "-q", "--no-pager")
    now = host.now() if now is None else now
    first = not journal.get("cursor") and journal.get("since") is None
    rebooted = bool(journal.get("boot_id") and boot_id and boot_id != journal["boot_id"])
    if rebooted:
        argv = base  # the whole current boot is new
        notices.append(_notice("host.rebooted", "host rebooted since the last check"))
    elif first:
        argv = base + ("-n", "1")
    elif journal.get("cursor"):
        argv = base + ("--after-cursor=" + journal["cursor"],)
    else:  # watching, but the journal had no record yet to anchor a cursor
        argv = base + ("--since=@%d" % int(journal["since"]),)
    code, text = host.run(argv)
    if code != 0:
        notices.append(_notice("faults.unreadable", "kernel journal unreadable (exit %s)" % code))
        return [], notices
    entries = sig.parse_journal_json(text)
    faults, ignored, cursor = sig.new_faults(entries, baseline, spec.get("levels"))
    if first and not rebooted:
        faults, ignored = [], 0  # start watching from now; earlier records predate the monitor
    journal.update({"cursor": cursor or journal.get("cursor"), "boot_id": boot_id or journal.get("boot_id"),
                    "since": now,
                    "baseline_matches": int(journal.get("baseline_matches", 0)) + ignored})
    state["journal"] = journal
    return faults, notices


def remote_reasons(cfg, host):
    remote = cfg.get("remote")
    if not remote:
        return []
    try:
        token = read_token(remote["token_file"])
    except (OSError, ValueError, PermissionError):
        return [_notice("remote.token", "remote status token unreadable")]
    code, doc = host.http("GET", remote["url"], token=token, ca_file=remote.get("ca_file"),
                          timeout_s=float(remote.get("timeout_s", 10)))
    if code != 200 or not isinstance(doc, dict):
        return [_notice("remote.unreachable", "other hosts' status unreachable (HTTP %s)" % code, alert=False)]
    reasons = []
    for status in doc.get("hosts", []):
        if status.get("host") == cfg["host"] or status.get("stale") or not isinstance(status.get("level"), int):
            continue
        level = max(0, min(3, status["level"]))
        if level:
            reasons.append({"signal": "remote." + status["host"], "level": level, "hold": level, "immediate": True,
                            "detail": "%s: %s" % (status["host"], str(status.get("message", ""))[:160])})
    return reasons


# --- actions ---------------------------------------------------------------

def _call(host, action):
    try:
        token = read_token(action["token_file"]) if action.get("token_file") else None
    except (OSError, ValueError, PermissionError):
        return 0
    code, _ = host.http(action["method"], action["url"], token=token, body=action.get("body"),
                        ca_file=action.get("ca_file"), timeout_s=float(action.get("timeout_s", 10)))
    return code


def enforce(cfg, state, host):
    """Make the loop and cadence match the level. Returns (effects, notices)."""
    effects, notices = [], []
    level = state["level"]
    cadence = cfg.get("cadence") or {}
    if level >= 1 and not cadence.get("reduce"):
        notices.append(_notice("actuator.cadence", "no cadence control configured; level 1 records and alerts only",
                               alert=False))
    if level >= 1 and not state.get("cadence_reduced") and cadence.get("reduce"):
        codes = [_call(host, action) for action in cadence["reduce"]]
        if all(200 <= c < 300 for c in codes):
            state["cadence_reduced"] = True
            effects.append("cadence_reduce")
        else:
            notices.append(_notice("actuator.cadence_failed", "cadence reduce failed (HTTP %s)" % codes))
    elif level == 0 and state.get("cadence_reduced"):
        codes = [_call(host, action) for action in cadence.get("restore", [])]
        if all(200 <= c < 300 for c in codes):
            state["cadence_reduced"] = False
            effects.append("cadence_restore")
        else:
            notices.append(_notice("actuator.cadence_failed", "cadence restore failed (HTTP %s)" % codes))

    loop = cfg.get("loop")
    if not loop or (level < 2 and not state.get("stopped_by_monitor")):
        return effects, notices
    try:
        token = read_token(loop["token_file"])
    except (OSError, ValueError, PermissionError):
        notices.append(_notice("actuator.loop_token", "activity-loop token unreadable"))
        return effects, notices
    timeout = float(loop.get("timeout_s", 10))
    code, doc = host.http("GET", loop["status_url"], token=token, ca_file=loop.get("ca_file"), timeout_s=timeout)
    if code != 200 or not isinstance(doc, dict):
        notices.append(_notice("actuator.loop_unreachable", "activity loop status unreachable (HTTP %s)" % code))
        return effects, notices
    loop_state = doc.get("state")
    if level >= 2 and loop_state != "stopped":
        code, _ = host.http("POST", loop["stop_url"], token=token, ca_file=loop.get("ca_file"), timeout_s=timeout)
        if code == 200:
            state["stopped_by_monitor"] = True
            effects.append("loop_stop")
        else:
            notices.append(_notice("actuator.loop_stop_failed", "activity loop stop failed (HTTP %s)" % code))
    elif level < 2 and state.get("stopped_by_monitor"):
        if loop_state == "stopped":
            code, _ = host.http("POST", loop["resume_url"], token=token, ca_file=loop.get("ca_file"), timeout_s=timeout)
            if code != 200:
                notices.append(_notice("actuator.loop_resume_failed", "activity loop resume failed (HTTP %s)" % code))
                return effects, notices
            effects.append("loop_resume")
        state["stopped_by_monitor"] = False
    return effects, notices


# --- status and alerts -----------------------------------------------------

def _safe_signal(name):
    text = _SIGNAL_SAFE.sub("_", str(name).lower()).strip("_.:-")[:64]
    return text if text and text[0].isalnum() else "signal"


def build_status(cfg, state, reasons, now):
    shown = [r for r in reasons if r["level"] > 0 or r.get("notice")]
    shown.sort(key=lambda r: (-r["level"], r["signal"]))
    return {
        "schema": STATUS_SCHEMA, "host": cfg["host"], "role": str(cfg.get("role", ""))[:32],
        "level": state["level"], "step": STEPS[state["level"]], "latched": bool(state["latched"]),
        "since": state.get("since"), "checked_at": now,
        "message": ladder.message(state["level"], state["latched"], reasons),
        "reasons": [{"signal": _safe_signal(r["signal"]), "level": int(r["level"]), "detail": str(r["detail"])[:240]}
                    for r in shown[:20]],
    }


def post_status(cfg, host, status):
    gateway = cfg.get("gateway")
    if not gateway:
        return None
    try:
        token = read_token(gateway["token_file"])
    except (OSError, ValueError, PermissionError):
        return "token_unreadable"
    code, _ = host.http("POST", gateway["status_url"], token=token, body=status, ca_file=gateway.get("ca_file"),
                        timeout_s=float(gateway.get("timeout_s", 10)))
    return code


def maybe_notify(cfg, host, state, status, transition, now, quiet=()):
    """Home Assistant notify on a level or latch change, or on a new notice
    (rate-limited by ``min_interval_s``)."""
    ha = cfg.get("home_assistant")
    last = state.get("notified") or {}
    notices = sorted(r["signal"] for r in status["reasons"] if r["level"] == 0 and r["signal"] not in quiet)
    level_changed = transition is not None or bool(last) and (last.get("level") != status["level"]
                                                              or last.get("latched") != status["latched"])
    new_notices = sorted(set(notices) - set(last.get("notices", [])))
    due = level_changed or (new_notices and now - float(last.get("at", 0)) >= float((ha or {}).get("min_interval_s", 900)))
    if not due:
        # Forget notices that cleared (so they alert again if they return);
        # keep rate-limited new ones "new" until they are sent.
        state["notified"] = {"level": status["level"], "latched": status["latched"], "at": float(last.get("at", 0)),
                             "notices": sorted(set(last.get("notices", [])) & set(notices))}
        return None
    state["notified"] = {"level": status["level"], "latched": status["latched"], "notices": notices, "at": now}
    if not ha:
        return None
    try:
        token = read_token(ha["token_file"])
    except (OSError, ValueError, PermissionError):
        return "token_unreadable"
    title = "Den lighting: %s" % status["step"].replace("_", " ") if status["level"] else "Den lighting notice"
    url = "%s/api/services/notify/%s" % (ha["url"].rstrip("/"), ha.get("notify_service", "persistent_notification"))
    code, _ = host.http("POST", url, token=token, body={"title": title, "message": status["message"]},
                        ca_file=ha.get("ca_file"), timeout_s=float(ha.get("timeout_s", 10)),
                        allow_plain=ha.get("allow_plain_http") is True)
    return code


def check(cfg, host, *, dry_run=False):
    """One pass. Returns (state, status, report)."""
    directory = state_dir(cfg)
    state_path = os.path.join(directory, "state.json")
    state = load_state(state_path, cfg)
    now = host.now()
    policy = {**ladder.DEFAULT_POLICY, **(cfg.get("policy") or {})}

    signals, notices = collect_signals(cfg, host)
    unit_notices, failed = failed_unit_notices(cfg, host)
    faults, fault_notices = fault_records(cfg, host, state)
    reasons = ladder.evaluate_rules(signals, cfg.get("rules", [])) + notices + unit_notices + fault_notices
    fault_rs, state["fault_hold"] = ladder.fault_reasons(state, faults, now, policy)
    reasons += fault_rs + remote_reasons(cfg, host)

    state, transition = ladder.step(state, reasons, now, policy)
    effects, action_notices = ([], []) if dry_run else enforce(cfg, state, host)
    reasons += action_notices
    status = build_status(cfg, state, reasons, now)
    report = {"transition": transition, "effects": effects, "faults": faults, "failed_units": failed}
    if not dry_run:
        report["gateway"] = post_status(cfg, host, status)
        quiet = {_safe_signal(r["signal"]) for r in reasons if r.get("quiet")}
        report["notify"] = maybe_notify(cfg, host, state, status, transition, now, quiet)
        if transition or effects:
            state["history"] = (state.get("history", []) + [{
                "at": now, "transition": transition, "effects": effects, "message": status["message"]}])[-50:]
        state["last_check_at"] = now
        write_atomic(state_path, state)
        write_atomic(os.path.join(directory, "status.json"), {**status, "signals": signals, "report": report})
    return state, status, report


def release(cfg, *, confirm):
    """The owner's release of the monitor's own latch (``den_ops release --confirm``)."""
    if not confirm:
        raise SystemExit("release needs --confirm, after you have checked the host (journal, temperatures, units)")
    path = os.path.join(state_dir(cfg), "state.json")
    state = load_state(path, cfg)
    released = ladder.release(state, cfg.get("policy"))
    released["history"] = (state.get("history", []) + [{"at": None, "transition": {"kind": "release",
                           "from": state["level"], "to": released["level"]}, "effects": [], "message": "owner release"}])[-50:]
    write_atomic(path, released)
    return released


def record_units_baseline(cfg, host, *, write, now):
    """Record the failed-unit baseline once, at install (``den_ops baseline-units --write``).
    Refuses to overwrite an existing baseline: the monitor never rewrites it."""
    spec = (cfg.get("sources") or {}).get("failed_units")
    if not spec:
        raise SystemExit("this config has no sources.failed_units")
    units = {}
    for scope in spec.get("scopes", ["system"]):
        argv = ("systemctl",) + (("--user",) if scope == "user" else ()) + sig.SYSTEMCTL_FAILED_ARGS
        units[scope] = sig.parse_failed_units(host.run(argv)[1])
    doc = {"schema": UNITS_BASELINE_SCHEMA, "host": cfg["host"], "recorded_at": now, "units": units}
    if write:
        if os.path.exists(spec["baseline"]):
            raise SystemExit("%s exists; the baseline is recorded once and never rewritten by this tool" % spec["baseline"])
        write_atomic(spec["baseline"], doc)
    return doc
