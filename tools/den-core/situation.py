"""The situation the coordinator is in: an activity and a part of the day (coordinator plan C2).

Activity, first match wins: gaming (HASS.Agent says a game is running and the den is occupied), task (someone
doing a close-up task), sitting (someone seated), present (occupied), vacant. `unknown` when a source that
would decide the case is unavailable: lights carry on, and learning pauses.

Part of day, in the den's local time (the offset comes from the deployment's config, never from the repo):
day 07-18, evening 18-23, night 23-07.

Standard library only. Pure.
"""
from datetime import datetime, timedelta, timezone

ACTIVITIES = ("gaming", "task", "sitting", "present", "vacant", "unknown")
LEARNABLE = ("gaming", "task", "sitting", "present")


def part_of_day(t, utc_offset_h=0.0):
    hour = datetime.fromtimestamp(t, timezone(timedelta(hours=utc_offset_h))).hour
    if 7 <= hour < 18:
        return "day"
    return "evening" if 18 <= hour < 23 else "night"


def activity(occupied, game_running, tasks, seated, qwen_ok=True):
    """occupied/game_running: True, False or None (unknown). tasks, seated: counts."""
    if occupied is None:
        return "unknown"
    if not occupied:
        return "vacant"
    if game_running is None:
        return "unknown"
    if game_running:
        return "gaming"
    if tasks:
        return "task"
    if seated:
        return "sitting" if qwen_ok else "unknown"
    return "present"


def key(act, part):
    return "%s/%s" % (act, part)
