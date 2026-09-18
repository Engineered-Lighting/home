# The generator holds no house

Every name belonging to one building has moved out of
`tools/build-living-lights-yaml.py` and into `ha-config/house.json`: the zones
and which camera sees each, the living room camera, the sofa and front door
zones, the television and gaming entities, the four zone sets, the asleep
blockers, the mirrored attributes and the dimmable light list. A different
house is a different file, chosen with `LIVING_LIGHTS_HOUSE`.

Before this, a second house meant a second copy of a 2,300 line generator, and
then two copies drifting apart. That was the single largest obstacle to running
this anywhere else, and it is the reason a portability audit counted
eighty-seven ways the system fails silently in a new building.

**The acceptance test is that nothing changed.** This house regenerates byte
for byte, verified against both the committed package and the file deployed on
the Home Assistant host. One line of hand wrapping in the colour temperature
sweep is reproduced deliberately, rather than reformatted, so the property
holds exactly.

The file is validated on load, because every mistake it can carry is silent
downstream. A zone that is not a Frigate zone renders "off" in the generated
template, the classifier reads the room as vacant for ever, and the light
simply never responds: no error, no log line, valid YAML. The loader refuses a
living room camera no zone names, a sofa or front door zone that is not a zone,
a sofa on a different camera from the living room, a zone set naming an unknown
zone, a house with no zones, and a wrong schema.

`ha-config/house.victoria-office.example.json` is a worked second house, one
camera and three zones, and a test generates it: a complete valid package with
fourteen automations and not one light entity from this building.

Still house-specific and not yet extracted: the actuator generator's light
targets, the hand-drawn spatial model, and the simulator's two copies of the
zone map. Those are the next files to do the same to.
