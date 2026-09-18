# Publisher: validate the three zone-map keys the stories rest on

`load_zones` checked that every zone named a listed camera, that dominance
rules named known zones, and that activity cameras existed. It did not check
`living_room_camera`, `sofa_zone` or `front_door_zone`, the three keys both
stories actually rest on, and each fell back silently to this house's names.

A zone map from a different house that omits them, or names a room it does not
have, loaded clean. Health stayed ok, the heartbeat kept beating, and the
living room, the sofa and the front door were never occupied: story T would
never reach WATCHING, the estimator would never see an arrival, and every
AWAY_HOLD would expire to UNATTENDED. Nothing anywhere would say why.

All three are now checked against the map, and the sofa must be on the living
room's own camera. A one-camera, one-zone map is still accepted, because the
point is to refuse names that do not exist rather than to refuse small houses.

Found by auditing what a move to a different house does to this system, where
it was the highest-value single validation missing.
