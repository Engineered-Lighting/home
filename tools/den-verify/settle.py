"""The settle rule: when has the last step that went out landed?

den_lighting fades every command over 2 s, and an "on" target over 5 s while
it restores after a game (`transition = 5 if self._restoring and target.on
else 2`, policy.py:226 on claude/den-core). Rung 8's probes send transition 0.
A Hue bulb reports its target at once, so its read-back says nothing about
landing: a send ends at sent_at + fade + settle_margin_s. An actuator step that
logs its own completion (move, fade, fade_in, fade_out with `result.at`) ends
there. Frames inside any step's window, or inside a person's change, are not
verify frames.
"""

ACTUATOR_KINDS = ("move", "fade", "fade_in", "fade_out")


class Unsettleable(ValueError):
    """A step went out with no way to tell when it landed."""


class Step:
    def __init__(self, decision_id, channel, kind, sent_at, end):
        self.decision_id, self.channel, self.kind = decision_id, channel, kind
        self.sent_at, self.end = sent_at, end
        self.mid_transition = False

    @property
    def reason(self):
        return "mid_move" if self.kind == "move" else "mid_fade"


def fade_s(policy, step, on, m):
    if type(step.get("transition_s")) in (int, float):
        return float(step["transition_s"])
    if step.get("kind") == "probe" or str(policy).startswith("den-light-probe"):
        return float(m["fade_s_probe"])
    if str(policy).startswith("den_lighting@"):
        if step.get("kind") == "restore" and on:
            return float(m["fade_s_den_lighting_restore_on"])
        return float(m["fade_s_den_lighting"])
    return None


def decision_steps(decision, resolve, m):
    """The steps of a Decision that went out, each with its landing time.

    `resolve(value)` turns a logged time into Unix seconds. A step went out
    when it has a `sent_at`; one dropped by a hold or a newer owner has none.
    """
    out = []
    policy = decision.get("policy", "")
    for channel, applied in (decision.get("applied_scene") or {}).items():
        on = ((applied.get("readback") or {}).get("level") or 0) > 0
        for step in applied.get("steps") or []:
            sent = resolve(step.get("sent_at"))
            if sent is None:
                continue
            kind = step.get("kind")
            done = resolve((step.get("result") or {}).get("at"))
            if kind in ACTUATOR_KINDS and done is not None:
                end = max(done, sent)
            else:
                fade = fade_s(policy, step, on, m)
                if fade is None:
                    raise Unsettleable(
                        f"{decision.get('decision_id')}: {channel} step {kind!r} logs no "
                        f"transition_s and policy {policy!r} has no known fade")
                end = sent + fade + float(m["settle_margin_s"])
            out.append(Step(decision.get("decision_id"), channel, kind, sent, end))
    return out


def settled_at(steps, decision_ts):
    return max((s.end for s in steps), default=decision_ts)


def mark_mid_transition(all_steps):
    """Flag a step sent while an earlier step on the same channel was still landing.

    How a Hue bulb lands on a command sent mid-transition is not qualified
    (fixture-capabilities, pending), so such a decision is not admitted.
    """
    by_channel = {}
    for s in sorted(all_steps, key=lambda s: s.sent_at):
        for earlier in by_channel.get(s.channel, []):
            if earlier.sent_at <= s.sent_at < earlier.end and earlier is not s:
                s.mid_transition = True
        by_channel.setdefault(s.channel, []).append(s)


class Windows:
    """Busy windows: steps landing and people changing lights."""

    def __init__(self):
        self.items = []  # (start, end, reason, ref)

    def add_steps(self, steps):
        for s in steps:
            self.items.append((s.sent_at, s.end, s.reason, s.decision_id))

    def add_change(self, ts, settle_s, margin_s, ref):
        self.items.append((ts - (settle_s or 0), ts + margin_s, "mid_change", ref))

    def busy(self, t):
        """The reason a frame at t is excluded, or None when it is settled."""
        for start, end, reason, _ in self.items:
            if start <= t < end:
                return reason
        return None
