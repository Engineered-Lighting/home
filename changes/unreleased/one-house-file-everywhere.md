# Every tool reads the same house

The zone map existed in five hand-synchronised copies: the simulator harness,
its evening analyser, the replay tool and both post-mortems, with the light
list and the television entity duplicated alongside. Nothing checked that they
agreed. A zone that drifted between them did not fail; it produced a report
about a house that does not exist, which is the worst kind of answer a
measurement tool can give.

`tools/house.py` derives all of it from `ha-config/house.json`, the same file
the generators read: the zone-to-camera map, the cameras, each zone's lights,
the living room's zones and lights including its group entity, the errand
zones, the television entity and the sofa sensors. The five tools import it and
hold nothing of their own.

Verified by running the same evening through the television post-mortem before
and after: identical numbers, down to two errands passing out of fifty-nine. A
test asserts that no tool names a zone of this house in its own source, and
that each obtains the house from the shared file, so the copies cannot come
back quietly.

With this, the vocabulary lives in one place for the generators, the actuators,
the simulator and the measurement tools. What remains house-specific is the
hand-drawn spatial model, which only redrawing can replace.
