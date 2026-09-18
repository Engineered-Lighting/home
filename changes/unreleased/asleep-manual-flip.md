# Story S: record a hand flip of the asleep latch, and mirror it to the publisher

The belief publisher's estimator honours a hand flip of the asleep latch for
forty-five minutes, and reads `input_text.living_lights_asleep_writer` to know
one happened. Two things had to be true for that to work and neither was.

Nothing wrote `manual`. Every writer of that helper is an automation naming
itself, and a person toggling the latch in the Home Assistant interface names
nobody, so the helper went on holding whichever automation wrote last. A new
automation now records `manual` when the latch changes with a Home Assistant
context that carries a `user_id`, which is how Home Assistant distinguishes a
change a person caused from one an automation caused. It writes the helper and
nothing else, so it cannot loop.

And the helper was not in the MQTT mirror, so the publisher could never read it
whatever it held. It is now.

Without both, the publisher's manual hold could never fire and the estimator
would have re-asserted the latch against the person who had just cleared it:
the owner turns the lights back on at two in the morning and the house puts
them out again. The simulator covers this on the legacy path as S13; the
publisher path had no equivalent because the input never arrived.

A test pins the generator's literal against the publisher's constant. They live
in different trees, and nothing else would catch them drifting apart; if they
did, a hand flip would be recorded and then ignored.

Needs a deploy: the house is running the M2 packages, which have neither.
