# The packages and the publisher now agree about the television

`tools/living_lights_tv_states.py` is what the generated Home Assistant
packages are built from. `lighting_publisher.stories` is what the belief
publisher's state machine reads. They are in different trees, cannot import
each other, and nothing noticed them drifting apart.

They had. `buffering` and `idle` meant the screen was on for the packages and
were unknown to the machine, so a television reporting either was read there as
a possibly dropped connection and held its previous state for the ten-minute
grace, while the house was already dimming for a film. `idle` is what an Apple
TV, a Chromecast and a Roku all report while powered on and not playing, so it
is an ordinary state rather than an exotic one.

The publisher's on-list now matches the generator's exactly. Its off-list still
does not, deliberately: a template has to decide immediately, so the generator
counts `unavailable` and `unknown` as off, while the machine has a grace window
and can afford to treat them as neither. That single exception is now named in
the code as `TV_GRACE_STATES` and checked, rather than left as an undocumented
difference.

A test holds the two vocabularies together across the trees, the same way the
asleep-writer literal is held to the publisher's constant.

Publisher-side only: no generated package changes and nothing needs deploying.
