"""The stepped back-off ladder, as pure functions of (state, reasons, now).

Levels (AGENTS.md always-on policy):
  0 normal
  1 cadence_reduced    lower JEV / Qwen / V-JEPA cadence
  2 deep_stack_paused  pause the deep stack (ambient only)
  3 loop_stopped       stop the activity loop, latched until the owner releases it

- **Escalation:** a threshold reason must hold for ``escalate_checks``
  consecutive checks (default 2) before the level rises; a new fault record or
  a remote host's level rises at once. The level jumps straight to the worst
  reason.
- **Hysteresis:** each threshold clears only ``clear_margin`` below (or above)
  its trigger value, so a reading that hovers at a limit does not flap.
- **Recovery:** one step at a time, after every reason has been clear of the
  current level for ``recover_after_s`` (default 600 s). Level 3 latches: it
  never recovers on its own.
"""
from __future__ import annotations

from . import STEPS
from .signals import pick

DEFAULT_POLICY = {"escalate_checks": 2, "recover_after_s": 600.0, "fault_hold_s": 1800.0, "latch_level": 3}


def _crosses(value, limit, op):
    return value >= limit if op == ">=" else value <= limit


def _fmt(value):
    return ("%.1f" % value).rstrip("0").rstrip(".") if abs(value) < 1e6 else "%.3g" % value


def evaluate_rule(signals, rule):
    """One threshold rule -> a reason {signal, level, hold, value, detail}."""
    op = rule["op"]
    levels = rule["levels"]
    margin = float(rule.get("clear_margin", 0.0))
    label = rule.get("label") or rule["signal"]
    unit = rule.get("unit", "")
    value = pick(signals, rule["signal"], op)
    reason = {"signal": rule.get("id") or rule["signal"], "level": 0, "hold": 0, "value": value, "immediate": False}
    if value is None:
        reason["detail"] = "%s unreadable" % label
        reason["notice"] = bool(rule.get("required", True))
        if reason["notice"]:
            reason.update(level=3, hold=3, immediate=True, evidence_error=True)
        return reason
    level = hold = 0
    for index, limit in enumerate(levels, start=1):
        if limit is None:
            continue
        if _crosses(value, limit, op):
            level = index
        cleared_at = limit - margin if op == ">=" else limit + margin
        if _crosses(value, cleared_at, op):
            hold = index
    reason["level"], reason["hold"] = level, max(level, hold)
    if level:
        limit = levels[level - 1]
        reason["detail"] = "%s %s%s %s %s%s" % (label, _fmt(value), unit, op.replace(">=", "≥").replace("<=", "≤"),
                                                 _fmt(limit), unit)
    else:
        reason["detail"] = "%s %s%s" % (label, _fmt(value), unit)
    return reason


def evaluate_rules(signals, rules):
    return [evaluate_rule(signals, rule) for rule in rules]


def fault_reasons(state, faults, now, policy):
    """New fault records escalate at once and hold their level for
    ``fault_hold_s`` after the latest one."""
    held = dict(state.get("fault_hold") or {})
    if held and now >= float(held.get("until", 0)):
        held = {}
    for fault in faults:
        held = {"level": max(int(held.get("level", 0)), fault["level"]),
                "until": now + float(policy["fault_hold_s"]),
                "classes": sorted(set(held.get("classes", [])) | {fault["class"]})}
    reasons = [{"signal": "fault." + fault["class"], "level": fault["level"], "hold": fault["level"],
                "immediate": True, "detail": "kernel %s record: %s" % (fault["class"], fault["message"][:120])}
               for fault in faults]
    if held and not faults:
        reasons.append({"signal": "fault.hold", "level": 0, "hold": int(held["level"]), "immediate": False,
                        "detail": "holding after %s record(s)" % ", ".join(held.get("classes", []))})
    return reasons, held


def step(state, reasons, now, policy=None):
    """Advance the ladder. Returns (state, transition) where transition is
    None or {"kind": "escalate"|"recover", "from": n, "to": m}."""
    policy = {**DEFAULT_POLICY, **(policy or {})}
    state = dict(state)
    level = int(state.get("level", 0))
    latched = bool(state.get("latched", False))
    desired = max([r["level"] for r in reasons] or [0])
    immediate = max([r["level"] for r in reasons if r.get("immediate")] or [0])
    hold = max([max(r["level"], r.get("hold", 0)) for r in reasons] or [0])
    transition = None

    if desired > level:
        pending = int(state.get("pending", 0)) + 1
        target = desired if pending >= int(policy["escalate_checks"]) else immediate
        if target > level:
            transition = {"kind": "escalate", "from": level, "to": target}
            level, pending = target, 0
            state["since"] = now
            state["below_since"] = None
        state["pending"] = pending
    else:
        state["pending"] = 0

    if transition is None and level > 0:
        if latched or hold >= level:
            state["below_since"] = None
        elif state.get("below_since") is None:
            state["below_since"] = now
        elif now - float(state["below_since"]) >= float(policy["recover_after_s"]):
            transition = {"kind": "recover", "from": level, "to": level - 1}
            level -= 1
            state["since"] = now
            state["below_since"] = now if hold < level else None

    if level >= int(policy["latch_level"]):
        latched = True
    state["level"], state["latched"] = level, latched
    state["step"] = STEPS[level]
    return state, transition


def release(state, policy=None):
    """The owner's release of the monitor's own level-3 latch (after checking
    the host). It drops to the step below the latch; recovery continues from
    there. It clears no system failure and edits no baseline."""
    policy = {**DEFAULT_POLICY, **(policy or {})}
    state = dict(state)
    state["latched"] = False
    state["fault_hold"] = {}
    state["level"] = min(int(state.get("level", 0)), int(policy["latch_level"]) - 1)
    state["step"] = STEPS[state["level"]]
    state["below_since"] = None
    state["pending"] = 0
    return state


HUMAN_STEPS = {
    1: "lower cadence",
    2: "deep stack paused, ambient only",
    3: "activity loop stopped",
}


def message(level, latched, reasons):
    """The one line shown in the Home banner and sent to Home Assistant."""
    active = sorted((r for r in reasons if r["level"] > 0), key=lambda r: -r["level"])
    notices = [r for r in reasons if r["level"] == 0 and r.get("notice")]
    if level == 0:
        if notices:
            return "Den lighting notice: %s" % notices[0]["detail"]
        return "Den lighting ops normal"
    why = active[0]["detail"] if active else "recovering (holding until clear)"
    text = "Den lighting backed off — %s: %s" % (HUMAN_STEPS[level], why)
    if latched:
        text += " · held until the owner releases it"
    return text[:240]
