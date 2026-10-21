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
import math

from . import CONFIG_SCHEMA, STATE_SCHEMA, STATUS_SCHEMA, STEPS, UNITS_BASELINE_SCHEMA
from . import policy as ladder
from . import signals as sig
from .hostio import read_private, read_token, write_atomic

# Exact LA system non-dependencies; mirrored by home_shared.camera_pilot_service.
DEN_NON_DEPENDENCY_UNITS = {
    "ha-health.service": "LA HAOS availability; Den uses Victoria occupancy and direct model transport",
    "ha-reachable.service": "LA HAOS availability; no Den model or camera dependency",
    "haos-precious-sync.service": "LA HAOS backup; no Den execution dependency",
    "home-agent-bff-egress-verify.service": "Home BFF OAuth boundary; Den model transport does not traverse BFF",
    "home-agent-lighting-egress-verify.service": "Core-to-HA physical lighting boundary; Den publishes virtual intent only",
    "fwupd-refresh.service": "Firmware metadata refresh; hardware faults remain guarded by kernel and sensors"
}

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
    if not isinstance(cfg.get("rules"), list) or not cfg["rules"]:
        raise ValueError("required safety rules missing")
    if not isinstance(cfg.get("sources"), dict):
        raise ValueError("required sources missing")
    for rule in cfg.get("rules", []):
        if not isinstance(rule, dict):
            raise ValueError("bad rule")
        if set(rule) - _RULE_KEYS or rule.get("op") not in (">=", "<=") or not isinstance(rule.get("levels"), list) \
                or len(rule["levels"]) != 3 or not rule.get("signal"):
            raise ValueError("bad rule: %r" % (rule,))
        limits = [v for v in rule["levels"] if v is not None]
        if not limits or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in limits):
            raise ValueError("rule thresholds must be finite")
        ordered = sorted(limits) if rule["op"] == ">=" else sorted(limits, reverse=True)
        if limits != ordered:
            raise ValueError("rule %s: levels must get stricter step by step" % rule["signal"])
    policy = cfg.get("policy") or {}
    if set(policy) - set(ladder.DEFAULT_POLICY):
        raise ValueError("unknown policy keys")
    for action in ((cfg.get("cadence") or {}).get("reduce", []) + (cfg.get("cadence") or {}).get("restore", [])):
        if action.get("method") not in ("POST", "PUT", "PATCH") or not str(action.get("url", "")).startswith("https://"):
            raise ValueError("cadence actions are POST/PUT/PATCH to https URLs")
    urls = [(cfg.get("loop") or {}).get(key) for key in ("status_url", "stop_url", "resume_url", "cadence_url")]
    urls += [(cfg.get("remote") or {}).get("url"), (cfg.get("gateway") or {}).get("status_url")]
    for url in [u for u in urls if u is not None]:
        if not _secure_url(url):
            raise ValueError("%s must be https, or http to loopback" % url)
    ha = cfg.get("home_assistant")
    if ha and not (_secure_url(ha.get("url", "")) or (ha.get("allow_plain_http") is True
                                                       and str(ha.get("url", "")).startswith("http://"))):
        raise ValueError("home_assistant.url must be https (or set allow_plain_http for a LAN/tailnet-only path)")
    zones = cfg["sources"].get("thermal_zones")
    if zones:
        if not isinstance(zones, dict) or not isinstance(zones.get("required_types", []), list) or not isinstance(zones.get("exclude_types", []), list):
            raise ValueError("thermal inventory must be explicit lists")
        if set(zones.get("required_types", [])) & set(zones.get("exclude_types", [])):
            raise ValueError("a required thermal sensor cannot be excluded")
    hwmon = cfg['sources'].get('hwmon')
    if hwmon:
        _hwmon_inputs(hwmon)
    return cfg


def _hwmon_inputs(spec):
    rows=spec.get('required_inputs',[]) if isinstance(spec,dict) else None
    if not isinstance(rows,list) or len(rows)>128:
        raise ValueError('bounded required hwmon inventory needed')
    paths=[]
    for row in rows:
        if (not isinstance(row,dict) or set(row)!={'path','chip','label'}
                or not isinstance(row['path'],str) or not re.fullmatch(r'hwmon[0-9]+/temp[0-9]+_input',row['path'])
                or any(not isinstance(row[k],str) or not 0<len(row[k])<=64 for k in ('chip','label'))):
            raise ValueError('hwmon input identity required')
        paths.append(row['path'])
    if len(paths)!=len(set(paths)):
        raise ValueError('duplicate required hwmon input')
    return rows


def _secure_url(url):
    url = str(url)
    return url.startswith("https://") or url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))


def state_dir(cfg):
    return cfg.get("state_dir") or os.environ.get("DEN_OPS_STATE_DIR") or os.path.join(os.getcwd(), "state")


def _validate_state_fields(state):
    def number(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    for key in ('since', 'below_since', 'last_check_at'):
        if state.get(key) is not None and not number(state[key]):
            raise ValueError('invalid state timestamp')
    if type(state.get('pending', 0)) is not int or state.get('pending', 0) < 0:
        raise ValueError('invalid pending count')
    for key in ('stopped_by_monitor', 'cadence_reduced', 'loop_cadence_reduced'):
        if key in state and type(state[key]) is not bool:
            raise ValueError('invalid control state')
    if not isinstance(state.get('history', []), list):
        raise ValueError('invalid incident history')
    for key in ('journal', 'fault_hold', 'notified'):
        if not isinstance(state.get(key, {}), dict):
            raise ValueError('invalid nested state')
    journal = state.get('journal', {})
    for key in ('cursor', 'boot_id'):
        if journal.get(key) is not None and (not isinstance(journal[key], str) or not journal[key]):
            raise ValueError('invalid journal identity')
    if journal.get('since') is not None and not number(journal['since']):
        raise ValueError('invalid journal time')
    matches = journal.get('baseline_matches', 0)
    if type(matches) is not int or matches < 0:
        raise ValueError('invalid baseline count')
    held = state.get('fault_hold', {})
    if held and (type(held.get('level')) is not int or not 0 <= held['level'] <= 3
                 or not number(held.get('until')) or not isinstance(held.get('classes'), list)
                 or any(not isinstance(v, str) for v in held['classes'])):
        raise ValueError('invalid held fault')
    notified = state.get('notified', {})
    if not number(notified.get('at', 0)) or not isinstance(notified.get('notices', []), list) or any(
            not isinstance(v, str) for v in notified.get('notices', [])):
        raise ValueError('invalid notification state')


def load_state(path, cfg):
    state = None
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
        if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA or state.get("host") != cfg["host"] or type(state.get("level")) is not int or not 0 <= state["level"] <= 3 or type(state.get("latched")) is not bool:
            raise ValueError("invalid monitor state")
        _validate_state_fields(state)
        return state
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        return {"schema": STATE_SCHEMA, "host": cfg["host"], "level": 3, "step": STEPS[3], "latched": True,
                "since": None, "pending": 0, "below_since": None, "fault_hold": {}, "journal": {},
                "stopped_by_monitor": False, "cadence_reduced": False, "notified": {}, "history": [],
                "state_error": "existing monitor state unreadable or malformed",
                "invalid_state": state}
    return {"schema": STATE_SCHEMA, "host": cfg["host"], "level": 0, "step": STEPS[0], "latched": False,
            "since": None, "pending": 0, "below_since": None, "fault_hold": {}, "journal": {},
            "stopped_by_monitor": False, "cadence_reduced": False, "notified": {}, "history": []}


def _evidence_error(signal, detail):
    return {"signal": signal, "level": 3, "hold": 3, "immediate": True, "notice": True,
            "evidence_error": True, "detail": detail}


def evidence_report(reasons):
    errors = [{"source": r["signal"], "detail": r["detail"]} for r in reasons if r.get("evidence_error")]
    return {"ok": not errors, "errors": errors}


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
        try:
            inventory=_hwmon_inputs(hwmon)
            if not inventory and any(r['signal'].startswith('temp.hwmon.') and r.get('required',True) for r in cfg['rules']):
                notices.append(_evidence_error('hwmon.inventory','required hwmon input inventory missing'))
            for row in inventory:
                if not sig.hwmon_input_available(hwmon.get('root','/sys/class/hwmon'),row):
                    notices.append(_evidence_error('hwmon.'+sig._key(row['path']),'required hwmon input unreadable or identity changed'))
        except ValueError:
            notices.append(_evidence_error('hwmon.inventory','required hwmon input inventory invalid'))
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
    # An aggregate maximum cannot prove every commissioned critical sensor survived.
    if zones and any(r["signal"].startswith("temp.zone") and r.get("required", True) for r in cfg.get("rules", [])):
        required = zones.get("required_types", [])
        if not required:
            notices.append(_evidence_error("thermal.inventory", "critical thermal sensor inventory missing"))
        for kind in required:
            if "temp.zone." + sig._key(kind) not in out:
                notices.append(_evidence_error("thermal." + sig._key(kind), "required thermal sensor unreadable: " + kind))
        for kind in zones.get("exclude_types", []):
            if not (zones.get("exclusion_reasons") or {}).get(kind):
                notices.append(_evidence_error("thermal.exclusions", "sensor exclusion has no reviewed reason: " + kind))
    for rule in cfg.get("rules", []):
        reason = ladder.evaluate_rule(out, rule)
        if reason.get("evidence_error"):
            notices.append(reason)
    return out, notices


def failed_unit_notices(cfg, host):
    spec = (cfg.get("sources") or {}).get("failed_units")
    if not spec:
        return [], {}
    baseline, notices = {}, []
    try:
        doc = json.loads(read_private(spec["baseline"]))
        if not isinstance(doc, dict) or doc.get("schema") != UNITS_BASELINE_SCHEMA or doc.get("host") != cfg["host"] or not isinstance(doc.get("units"), dict):
            raise ValueError("schema")
        for scope in spec.get("scopes", ["system"]):
            if not isinstance(doc["units"].get(scope), list) or any(not isinstance(u, str) or not u for u in doc["units"][scope]):
                raise ValueError("baseline scope missing or malformed")
        baseline = {scope: set(doc.get("units", {}).get(scope, [])) for scope in ("system", "user")}
    except (OSError, ValueError, TypeError, PermissionError) as error:
        notices.append(_evidence_error("units.baseline", "failed-unit baseline unreadable (%s); every failed unit is reported"
                               % type(error).__name__))
        baseline = {"system": set(), "user": set()}
    current = {}
    for scope in spec.get("scopes", ["system"]):
        argv = ("systemctl",) + (("--user",) if scope == "user" else ()) + sig.SYSTEMCTL_FAILED_ARGS
        code, text = host.run(argv)
        if code != 0:
            notices.append(_evidence_error("units.%s.unreadable" % scope, "%s failed units unreadable (exit %s)" % (scope, code)))
            continue
        try:
            current[scope] = sig.parse_failed_units(text, strict=True)
        except ValueError:
            notices.append(_evidence_error("units.%s.malformed" % scope, "%s failed-unit output malformed" % scope))
            continue
        for unit in current[scope]:
            if cfg['host'] == 'la-host' and scope == 'system' and unit in DEN_NON_DEPENDENCY_UNITS:
                notices.append(_notice('unit.non_dependency.' + unit,
                    unit + ' failed (outside Den inference dependency scope): ' + DEN_NON_DEPENDENCY_UNITS[unit]))
            elif unit not in baseline.get(scope, set()):
                notices.append(_evidence_error("unit.%s" % unit, "%s unit %s failed (not in the recorded baseline)" % (scope, unit)))
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
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot_id):
            raise ValueError("invalid boot ID")
    except (OSError, ValueError):
        return [], [_evidence_error("host.boot_id", "boot ID unreadable or malformed")]
    try:
        baseline = sig.load_fault_baseline(spec["baseline"], cfg["host"])
    except (OSError, ValueError, TypeError) as error:
        notices.append(_evidence_error("faults.baseline", "fault baseline unreadable (%s); no record is ignored"
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
        argv = base + ("-b",)  # inspect the current boot; never discard unreviewed pre-monitor faults
    elif journal.get("cursor"):
        argv = base + ("--after-cursor=" + journal["cursor"],)
    else:  # watching, but the journal had no record yet to anchor a cursor
        argv = base + ("--since=@%d" % int(journal["since"]),)
    code, text = host.run(argv)
    if code != 0:
        notices.append(_evidence_error("faults.unreadable", "kernel journal unreadable (exit %s)" % code))
        return [], notices
    try:
        entries = sig.parse_journal_json(text, strict=True)
        if first and not entries:
            raise ValueError("initial kernel journal has no anchor record")
    except (ValueError, TypeError):
        return [], notices + [_evidence_error("faults.malformed", "kernel journal malformed or initial anchor absent")]
    # Historical patterns apply only to the initial inspected boot, not recurring new events.
    faults, ignored, cursor = sig.new_faults(entries, baseline if first and not rebooted else [], spec.get("levels"))
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
        return [_evidence_error("remote.token", "remote status token unreadable")]
    code, doc = host.http("GET", remote["url"], token=token, ca_file=remote.get("ca_file"),
                          timeout_s=float(remote.get("timeout_s", 10)))
    if code != 200 or not isinstance(doc, dict):
        return [_evidence_error("remote.unreachable", "other hosts' status unreachable (HTTP %s)" % code)]
    reasons = []
    statuses = doc.get("hosts")
    if not isinstance(statuses, list):
        return [_evidence_error("remote.malformed", "other hosts status malformed")]
    for expected in remote.get("required_hosts", ["la-host"]):
        matches = [s for s in statuses if isinstance(s, dict) and s.get("host") == expected]
        if len(matches) != 1 or matches[0].get("stale") is not False or type(matches[0].get("level")) is not int:
            reasons.append(_evidence_error("remote." + expected, "required host status missing, stale or malformed"))
    for status in statuses:
        if not isinstance(status, dict):
            continue
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


# The activity loop's control routes (AL-3): every monitor call names its
# reason, so the loop can tell the monitor's stop from the owner's.
MONITOR_STOP = {"reason": "monitor_backoff"}
CADENCE_FOR_LEVEL = ("normal", "reduced", "reduced", "reduced")


def enforce(cfg, state, host):
    """Make the loop and cadence match the level. Returns (effects, notices)."""
    effects, notices = [], []
    level = state["level"]
    loop = cfg.get("loop") or {}
    cadence = cfg.get("cadence") or {}
    if level >= 1 and not cadence.get("reduce") and not loop.get("cadence_url"):
        notices.append(_notice("actuator.cadence", "no cadence control configured; level 1 records and alerts only",
                               alert=False))
    # Extra cadence controls beyond the loop (optional plain actions).
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

    if not loop or (level == 0 and not state.get("stopped_by_monitor") and not state.get("loop_cadence_reduced")
                    and not state.get("owner_held")):
        return effects, notices
    try:
        token = read_token(loop["token_file"])
    except (OSError, ValueError, PermissionError):
        notices.append(_notice("actuator.loop_token", "activity-loop token unreadable"))
        return effects, notices
    timeout = float(loop.get("timeout_s", 10))

    def post(url, body):
        return host.http("POST", url, token=token, body=body, ca_file=loop.get("ca_file"), timeout_s=timeout)

    code, doc = host.http("GET", loop["status_url"], token=token, ca_file=loop.get("ca_file"), timeout_s=timeout)
    if code != 200 or not isinstance(doc, dict):
        notices.append(_notice("actuator.loop_unreachable", "activity loop status unreachable (HTTP %s)" % code))
        return effects, notices
    loop_state, reason = doc.get("state"), doc.get("stopped_reason")

    def qwen_control(value):
        deep = value.get('deep_stack') if isinstance(value, dict) else None
        return deep.get('qwen_control') if isinstance(deep, dict) else None

    qwen = qwen_control(doc)
    controlled_qwen = (type(doc.get('monitor_control_version')) is int
                       and doc['monitor_control_version'] == 2 and isinstance(qwen, dict))

    def cadence_confirmed(value, want):
        return (isinstance(value, dict) and type(value.get('factor')) is int
                and value['factor'] == (2 if want == 'reduced' else 1)
                and type(value.get('interval_seconds')) in (int, float)
                and .5 <= value['interval_seconds'] <= 20.)

    # Step 1: cadence. Re-asserted every check (a restarted service comes back
    # at normal); restored only if the monitor reduced it.
    if loop.get("cadence_url"):
        want = CADENCE_FOR_LEVEL[level]
        applied = doc.get("cadence") == want and (not controlled_qwen or cadence_confirmed(qwen, want))
        if (level >= 1 or state.get("loop_cadence_reduced")) and not applied:
            code, reply = post(loop["cadence_url"], {"level": want})
            applied = code == 200 and (not controlled_qwen or cadence_confirmed(
                reply.get('qwen') if isinstance(reply, dict) else None, want))
            if applied:
                effects.append("cadence_reduce" if want == "reduced" else "cadence_restore")
            else:
                notices.append(_notice("actuator.cadence_failed", "activity loop cadence %s failed (HTTP %s)"
                                       % (want, code)))
        if applied:
            state["loop_cadence_reduced"] = want == "reduced"

    # Steps 2-3: stop with the monitor's reason, preserving the owner's stop.
    if level >= 2:
        if loop_state != "stopped":
            code, _ = post(loop["stop_url"], MONITOR_STOP)
            if code == 200:
                state["stopped_by_monitor"] = True
                effects.append("loop_stop")
            else:
                notices.append(_notice("actuator.loop_stop_failed", "activity loop stop failed (HTTP %s)" % code))
        elif reason == MONITOR_STOP["reason"]:
            state["stopped_by_monitor"] = True
        else:
            state["stopped_by_monitor"] = False
            state["owner_held"] = "activity_loop_stopped_by_%s" % reason   # reported after recovery too
            # v2 can independently quiesce the controller-owned Qwen pump while
            # retaining operator_stop. Legacy actors must not receive this call.
            if controlled_qwen and qwen.get('blocked') is not True:
                code, reply = post(loop['stop_url'], MONITOR_STOP)
                confirmed = qwen_control(reply)
                if (code == 200 and isinstance(reply, dict) and reply.get('state') == 'stopped'
                        and reply.get('stopped_reason') == reason and isinstance(confirmed, dict)
                        and confirmed.get('blocked') is True):
                    effects.append('qwen_backoff')
                else:
                    notices.append(_notice('actuator.qwen_backoff_failed',
                        'Qwen back-off did not confirm the preserved owner stop (HTTP %s)' % code))
            notices.append(_notice("actuator.owner_stop", "activity loop already stopped by the owner (%s)" % reason,
                                   alert=False))
    elif state.get("stopped_by_monitor"):
        state["stopped_by_monitor"] = False
        if loop_state == "stopped" and reason == MONITOR_STOP["reason"]:
            code, reply = post(loop["resume_url"], MONITOR_STOP)
            if code == 200:
                effects.append("loop_resume")
            elif code == 409:
                state["owner_held"] = (reply or {}).get("detail") if isinstance(reply, dict) else None
                state["owner_held"] = state["owner_held"] or "activity_loop_stopped_by_operator_stop"
            else:
                state["stopped_by_monitor"] = True   # retry on the next check
                notices.append(_notice("actuator.loop_resume_failed", "activity loop resume failed (HTTP %s)" % code))
        elif loop_state == "stopped":
            state["owner_held"] = "activity_loop_stopped_by_%s" % reason
    # The owner's stop outlived the back-off: report it every check until the
    # owner resumes the loop (the monitor never does).
    if level < 2 and state.get("owner_held"):
        if loop_state == "stopped" and not state.get("stopped_by_monitor"):
            notices.append(_owner_held({"detail": state["owner_held"]}))
        else:
            state["owner_held"] = None
    return effects, notices


def _owner_held(reply):
    """The loop refused the monitor's resume: the owner stopped it. Recorded,
    and the loop is left stopped for the owner to resume."""
    detail = reply.get("detail") if isinstance(reply, dict) else None
    return _notice("actuator.owner_stop", "activity loop left stopped: the owner stopped it (%s); resume it from Home"
                   " or the Lab when ready" % (detail or "activity_loop_stopped_by_operator_stop"))


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
    if state.get("state_error"):
        reasons.append(_evidence_error("monitor.state", state["state_error"]))
    for source in ("faults", "failed_units"):
        if not (cfg.get("sources") or {}).get(source):
            reasons.append(_evidence_error("source." + source, "required source not configured: " + source))
    fault_rs, state["fault_hold"] = ladder.fault_reasons(state, faults, now, policy)
    reasons += fault_rs + remote_reasons(cfg, host)

    state, transition = ladder.step(state, reasons, now, policy)
    effects, action_notices = ([], []) if dry_run else enforce(cfg, state, host)
    reasons += action_notices
    status = build_status(cfg, state, reasons, now)
    report = {"transition": transition, "effects": effects, "faults": faults, "failed_units": failed,
              "evidence": evidence_report(reasons)}
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
    released.pop("state_error", None)
    released["history"] = (state.get("history", []) + [{"at": None, "transition": {"kind": "release",
                           "from": state["level"], "to": released["level"]}, "effects": [], "message": "owner release"}])[-50:]
    write_atomic(path, released)
    return released


def record_units_baseline(cfg, host, *, write, now, reviewed_units=()):
    """Record the failed-unit baseline once, at install (``den_ops baseline-units --write``).
    Refuses to overwrite an existing baseline: the monitor never rewrites it."""
    spec = (cfg.get("sources") or {}).get("failed_units")
    if not spec:
        raise SystemExit("this config has no sources.failed_units")
    units = {}
    for scope in spec.get("scopes", ["system"]):
        argv = ("systemctl",) + (("--user",) if scope == "user" else ()) + sig.SYSTEMCTL_FAILED_ARGS
        code, text = host.run(argv)
        if code != 0:
            raise SystemExit("cannot baseline unreadable failed units")
        units[scope] = sig.parse_failed_units(text, strict=True)
    actual = {scope + ":" + u for scope, names in units.items() for u in names}
    if write and actual != set(reviewed_units):
        raise SystemExit("--reviewed-unit entries must exactly match individually diagnosed failed units")
    doc = {"schema": UNITS_BASELINE_SCHEMA, "host": cfg["host"], "recorded_at": now, "units": units}
    if write:
        if os.path.exists(spec["baseline"]):
            raise SystemExit("%s exists; the baseline is recorded once and never rewritten by this tool" % spec["baseline"])
        write_atomic(spec["baseline"], doc)
    return doc
