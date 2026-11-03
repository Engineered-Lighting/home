"""A synthetic den evening in the real formats. It exercises plumbing only and is never evidence.

Fact lines are built with the perceiver's own builders (qwen_scene, la_fact_log),
so every adapter and policy sees the real la-fact-log-1 shape. The only
randomness is random.Random(seed); nothing reads the clock.
"""
import json
import math
import random

from events import LIGHTS
from sources import fact_time, lab

T0 = 1793764800.0  # 2026-11-04T04:00:00Z, 20:00 Pacific on 2026-11-03
# reading-shadow-script-1: (block, kind, minutes)
SCRIPT = (("b1", "rest", 3), ("b2", "reading", 5), ("b3", "phone_held", 3), ("b4", "reading", 5),
          ("b5", "book_closed", 2), ("b6", "computer_work", 4), ("b7", "rest", 2))
SESSION = "5e" * 16
READING = {"people_visible": "one", "subject_posture": "sitting", "held_object": "book", "book_state": "open",
           "gaze_target": "book", "typing_visible": "no", "screen_facing_subject": "not_visible",
           "activity_hypothesis": "reading", "visible_basis": ["person_visible", "book_in_hands"],
           "change_vs_prior": "no_prior"}
TEMPLATES = {
    "reading": READING,
    "phone_held": {**READING, "held_object": "phone", "book_state": "not_visible", "gaze_target": "phone",
                   "activity_hypothesis": "phone_use", "visible_basis": ["person_visible", "phone_in_hands"]},
    "rest": {**READING, "held_object": "none", "book_state": "not_visible", "gaze_target": "elsewhere",
             "activity_hypothesis": "resting", "visible_basis": ["person_visible", "seated_posture", "hands_resting"]},
    "book_closed": {**READING, "book_state": "closed", "gaze_target": "elsewhere", "activity_hypothesis": "resting",
                    "visible_basis": ["person_visible", "book_closed"]},
    "computer_work": {**READING, "held_object": "none", "book_state": "not_visible", "gaze_target": "screen",
                      "typing_visible": "yes", "screen_facing_subject": "yes", "activity_hypothesis": "computer_work",
                      "visible_basis": ["person_visible", "hands_on_keyboard", "head_toward_monitor_side"]},
}


def fact_line(index, observed, available, facts=None, text=None, outcome="completed"):
    """One real-format la-fact-log-1 line (the brief's RECIPE)."""
    la_fact_log, scene = lab()
    request_id = scene.digest({"fixture": index})
    frames = [{"media_id": "ring-%064x" % (index*10+k), "frame_id": "cap-1-0-%d" % (index*10+k),
               "capture_epoch": "cap-1", "observed_at": observed - 3 + 1.5*k} for k in range(3)]
    receipt = None
    if outcome == "completed":
        draft = scene.build_observation(
            site_id="victoria", camera_id="den", session_id=SESSION, request_id=request_id, frames=frames,
            generated_at=available - 0.1, prompt_version=scene.PROMPT_VERSION,
            context=scene.model_context("evening 20:00"), body_sha256=scene.digest({"body": index}),
            engine_epoch="la-epoch", artifact_sha256="a"*64, content=json.dumps(facts) if text is None else text,
            finish_reason="stop", prior_supplied=False)
        receipt = scene.first_receipt(draft, available)
    line, _ = la_fact_log.line(request_id=request_id, session_id=SESSION, observed_at=observed,
                               sent_at=observed + 0.1, available_at=available, engine_epoch="la-epoch",
                               prompt_version=scene.PROMPT_VERSION, outcome=outcome, observation=receipt)
    return line


def evening(seed=20261103, unclear_rate=0.05, invalid_rate=0.02, fail_rate=0.02, touch=False, game=False):
    """(inputs, truth, fact_lines): time-ordered (t, kind, data), reading-shadow-truth-1, la-fact-log-1 lines."""
    rng = random.Random(seed)
    lit = {"on": True, "brightness": 180, "kelvin": 2700, "context": "unknown"}
    inputs = [(T0 - 600, "light_observed", {"entity_id": e, **lit}) for e in LIGHTS]
    inputs += [(T0 + dt, "occupancy", {"value": value, "via": "synthetic"})
               for dt, value in ((-600, "on"), (-590, "off"), (-60, "on"), (1450, "off"))]
    blocks, start = [], T0
    for block, kind, minutes in SCRIPT:
        blocks.append({"block": block, "kind": kind, "reading": kind == "reading", "start": start,
                       "end": start + minutes * 60})
        start += minutes * 60
    fact_lines, index = [], 0
    while T0 + 2 + 4 * index < T0 + 1440:
        observed = T0 + 2 + 4 * index
        template = TEMPLATES[next(b["kind"] for b in blocks if b["start"] <= observed < b["end"])]
        available = observed + max(0.2, rng.lognormvariate(math.log(1.17), 0.317))
        u = rng.random()
        if u < fail_rate:
            line = fact_line(index, observed, available, outcome="timeout")
        elif u < fail_rate + invalid_rate:
            line = fact_line(index, observed, available, text="not json")
        elif u < fail_rate + invalid_rate + unclear_rate:
            line = fact_line(index, observed, available, facts={**template, "activity_hypothesis": "unclear"})
        else:
            line = fact_line(index, observed, available, facts=template)
        fact_lines.append(line)
        value = json.loads(line)
        inputs.append((fact_time(value["available_at"]), "facts", value))
        index += 1
    if touch:
        inputs.append((T0 + 720, "correction", {"correction_id": "syn-touch-1", "entity_id": "light.den_rear_left",
                                                "after": {"on": True, "brightness": 80, "kelvin": 2202},
                                                "source": "synthetic"}))
    if game:
        samples = [(dt, "playing") for dt in range(1080, 1191, 10)] + [(dt, "idle") for dt in range(1200, 1261, 10)]
        inputs += [(T0 + dt, "gaming_sample", {"status": status, "sampled_at": T0 + dt - 0.5})
                   for dt, status in samples]
    truth = {"schema": "reading-shadow-truth-1", "script": "reading-shadow-script-1", "blocks_planned": len(SCRIPT),
             "complete": True, "clock": {"server": "synthetic", "before_s": 0.0, "after_s": 0.0},
             "source": "synthetic", "blocks": blocks}
    return sorted(inputs, key=lambda item: item[0]), truth, fact_lines
