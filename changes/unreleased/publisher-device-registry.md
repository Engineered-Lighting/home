# Belief publisher: group its entities under one Home Assistant device

The publisher announces sixteen entities. Without a `device` block in their
discovery configs they arrive loose in Home Assistant's registry, and removing
them means deleting sixteen things by hand. They now carry one, so they group
under a single device the owner can find, inspect and delete in one action,
which is what the M4 rollback asks for. The shadow run gets its own device, so
a shadow entity can never be mistaken for a live one and the two can be removed
independently.

Settled now because it cannot be settled cheaply later: a device's identifiers
are its registry key, exactly like a `unique_id`, and changing them after
publication leaves the old device behind holding the entities' history, their
area and any customisation. Nothing has been published yet.

Found by comparing the publisher's discovery payloads against the thirteen
configs this broker already carries and Home Assistant has already accepted.
That comparison also confirmed the payloads are otherwise well formed and that
none of the sixteen topics collides with anything retained.
