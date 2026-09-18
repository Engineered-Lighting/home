"""The one definition of "the TV is off" shared by every Living Lights generator.

Before 2026-09-17 the classifier said the TV was playing whenever
`media_player.lg_tv` was not in `off`, `unavailable`, `unknown` (so `standby`
counted as playing), while the learning and bias packages used a membership
list `on`, `playing`, `paused`, `buffering` (so `standby` counted as off).
Both generators now import these tuples, and the generated packages carry a
single `binary_sensor.living_lights_tv_playing` template that every consumer
reads instead of re-deriving the predicate.

`unavailable` is listed as off here on purpose: the LG TV integration reports
`unavailable` both when the set is off and, for short blips, while it plays.
The generated `tv_playing` sensor handles the blip with a `delay_off` and,
when the belief publisher is live, with the `tv_watching` belief; this module
only fixes the vocabulary.
"""

# States in which the television is NOT playing anything worth dimming for.
TV_OFF_STATES = ("off", "standby", "unavailable", "unknown")

# States that mean the television is on. Anything not listed in either tuple
# (a future integration state) is treated as ON by the generated sensor, the
# safe direction for a watch zone: dim rather than brighten.
TV_ON_STATES = ("on", "playing", "paused", "buffering", "idle")


def jinja_list(states: tuple[str, ...]) -> str:
    """Render a tuple as a Jinja list literal: ('a', 'b') -> ['a', 'b']."""
    return "[" + ", ".join(f"'{s}'" for s in states) + "]"


TV_OFF_JINJA = jinja_list(TV_OFF_STATES)
TV_ON_JINJA = jinja_list(TV_ON_STATES)
