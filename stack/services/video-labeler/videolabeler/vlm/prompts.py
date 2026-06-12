"""Two-pass prelabel prompts. PROMPT_VERSION participates in the prelabel
job's idempotency key and the per-window done-marker -- bump it whenever the
wording changes enough to invalidate stored prelabels.

Pass 1 is an open description citing ONLY visible evidence (plus room hint
and the previous window's pass-2 summary for temporal continuity). Pass 2
constrains to strict JSON against vlm.schema (ACTIVE_SET + 'other' for
activity; full posture enum; quality_flags subset).
"""
from __future__ import annotations

from .. import ontology
from . import schema

# v2 (2026-06-12): ACTIVE_SET grew (+reading/resting/conversation — the v1
# enum forced confident idle_present mislabels on reading scenes) and the
# multi_person disagreement check now caps confidence on >=2-person windows.
PROMPT_VERSION = "v2"

# filename heuristics -- home camera exports usually carry the room name
ROOM_WORDS = (
    "kitchen", "living", "livingroom", "lounge", "bedroom", "bathroom",
    "office", "study", "hall", "hallway", "entry", "entrance", "dining",
    "balcony", "garage", "laundry",
)


def room_hint(filename) -> str:
    """Best-effort room/camera name from the filename; falls back to the
    stem so the model at least sees the source name."""
    stem = (filename or "").rsplit(".", 1)[0]
    tokens = "".join(c if c.isalnum() else " " for c in stem.lower()).split()
    for tok in tokens:
        if tok in ROOM_WORDS:
            return tok
    return stem or "unknown camera"


def pass1_messages(room: str, window_start: float, window_end: float,
                   prev_summary, image_parts: list) -> list:
    """Open-description pass. image_parts are OpenAI-style content parts
    (full frame + person crop per keyframe, in time order)."""
    intro = [
        f"Camera/room: {room}.",
        f"These keyframes cover seconds {window_start:.1f}-{window_end:.1f}"
        " of a home camera recording (single-occupant apartment).",
        "Images alternate: full frame, then a zoomed crop of the person"
        " (when one was detected), for each keyframe in time order.",
    ]
    if prev_summary:
        intro.append(f"Previous window summary: {prev_summary}")
    intro.append(
        "Describe what the person is doing in 2-4 sentences. Cite ONLY"
        " evidence visible in these images (objects held, surfaces touched,"
        " body position, gaze). Do not guess identity, emotion, or anything"
        " off-screen. If no person is clearly visible, say so.")
    content = [{"type": "text", "text": "\n".join(intro)}] + list(image_parts)
    return [
        {"role": "system",
         "content": "You are a precise video annotation assistant for home"
                    " activity footage. You only state what is visibly"
                    " supported by the provided frames."},
        {"role": "user", "content": content},
    ]


def _pass2_instructions() -> str:
    return (
        "Convert the description and frames into ONE JSON object, exactly"
        " this shape and nothing else:\n"
        "{\n"
        f'  "activity_primary": one of {list(schema.VLM_ACTIVITY_VALUES)},\n'
        '  "activity_other_hint": short string when activity_primary is'
        ' "other", else null,\n'
        f'  "posture": one of {list(schema.VLM_POSTURE_VALUES)},\n'
        f'  "quality_flags": subset of {list(ontology.QUALITY_FLAGS)}'
        " (empty list when none apply),\n"
        '  "uncertainty": {"activity": 0..1, "posture": 0..1},\n'
        '  "evidence": one sentence citing the visible evidence,\n'
        '  "evidence_timestamps": [seconds within the video that support'
        " the label, taken from the keyframe timestamps]\n"
        "}\n"
        "Use \"other\" ONLY when the activity is clear but not in the list;"
        " use \"unknown\" when you cannot tell. Higher uncertainty means"
        " less sure. Reply with the JSON object only."
    )


def pass2_messages(pass1_text: str, room: str, window_start: float,
                   window_end: float, keyframe_times: list,
                   image_parts: list, prior_response=None,
                   validation_error=None) -> list:
    """Strict-JSON pass. On retry, prior_response + validation_error are
    appended so the model can correct itself."""
    times = ", ".join(f"{t:.1f}" for t in keyframe_times)
    text = (
        f"Camera/room: {room}. Window: seconds"
        f" {window_start:.1f}-{window_end:.1f}."
        f" Keyframe timestamps: [{times}].\n"
        f"Open description from a first look:\n{pass1_text}\n\n"
        + _pass2_instructions())
    if prior_response is not None and validation_error is not None:
        text += (
            "\n\nYour previous reply was:\n"
            f"{prior_response}\n"
            f"It failed validation: {validation_error}\n"
            "Reply again with ONLY a corrected JSON object.")
    content = [{"type": "text", "text": text}] + list(image_parts)
    return [
        {"role": "system",
         "content": "You output strict JSON labels for home activity"
                    " footage. No prose, no markdown fences, JSON only."},
        {"role": "user", "content": content},
    ]
