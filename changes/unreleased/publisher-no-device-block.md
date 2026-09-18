# Belief publisher: no device block, because it renames the entities

A `device` block was added to the discovery configs earlier today so the
sixteen entities would group under one Home Assistant device and the rollback
would be a single deletion. Published to the live house, it renamed every
entity: the estimator registered as
`sensor.lighting_publisher_shadow_living_lights_asleep_estimator_shadow`
instead of `sensor.living_lights_asleep_estimator_shadow`, because Home
Assistant builds the entity id from the device name and the object id together.

Those ids are a contract, not a preference. The generator's asleep-mirror
automation watches `sensor.living_lights_asleep_estimator` by name and is the
publisher's only path to the latch, so a prefixed id is an id nothing reads.
Live, the publisher would have spent the week writing to a sensor no automation
was watching, and the story would have failed silently.

The block is removed and a test refuses to let one back in, with the reason and
the observed id written into it. Grouping for easier removal is a convenience;
being read at all is not.

Two things worth knowing for later. Home Assistant pins an entity id at first
registration and preserves it by `unique_id`, so clearing the retained
discovery topics and republishing did not undo the rename; the registry's own
rename did. And no unit test could have caught this, because only Home
Assistant decides what an entity is called. The check belongs in the deploy:
publish, then read back the ids.
