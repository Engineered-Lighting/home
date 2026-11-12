"""Bulb plans for den_lighting 1.1 (coordinator plan C10).

den_lighting stays the only writer of the room lights. The coordinator sends it `den_lighting.coordinator_plan`:
only the lights it has learned something for in the current situation. A light left out follows den_lighting's
own behaviour, which is today's. Each plan carries a rising generation; den_lighting refuses an older one.
- Sent when the scenes change, and renewed every renew_s while there's something to say.
- When a situation ends with nothing learned for the next one, an explicit empty plan clears the last one
  (Codex 10), instead of letting it linger for valid_for_s.
- While the coordinator is Off nothing is sent; den_lighting has already forgotten the plan.

Standard library only. Pure.
"""
SERVICE = "den_lighting.coordinator_plan"


class BulbPlanner:
    def __init__(self, lights, renew_s=10.0, valid_for_s=60, transition_s=2):
        self.lights = list(lights)
        self.renew_s, self.valid_for_s, self.transition_s = renew_s, valid_for_s, transition_s
        self.generation, self.last_scenes, self.last_sent = 0, {}, None

    def scenes(self, key, learner):
        return {light: scene for light in self.lights for scene in [learner.scene(key, light)] if scene}

    def plan(self, now, key, learner, on):
        """The service call to send now, or None."""
        if not on:
            self.last_scenes, self.last_sent = {}, None  # Off: den_lighting forgot it; start clean on On
            return None
        scenes = self.scenes(key, learner)
        changed = scenes != self.last_scenes
        renew = bool(scenes) and self.last_sent is not None and now - self.last_sent >= self.renew_s
        if not changed and not renew:
            return None
        if changed:
            self.generation += 1
        self.last_scenes, self.last_sent = scenes, now
        return {"service": SERVICE, "data": {
            "plan_id": "plan-%d" % self.generation, "generation": self.generation, "situation": key,
            "valid_for_s": self.valid_for_s, "transition_s": self.transition_s, "scenes": scenes}}
