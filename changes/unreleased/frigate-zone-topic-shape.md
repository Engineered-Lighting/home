# Belief publisher: read Frigate zone occupancy from the topic Frigate actually publishes

The publisher looked for per-zone person counts at
`frigate/<camera>/<zone>/person`. Frigate names zones globally and publishes
them at `frigate/<zone>/person`, with the same shape as a camera count, so the
publisher saw no zone occupancy at all on the live broker. Story T rests on the
sofa: the guard that makes UNATTENDED unreachable while someone is sitting
there could never have held, and no living-room zone would have reported.

Found by subscribing to the broker as the publisher's own login before the
first shadow night, rather than after seven of them. Five minutes of live
traffic from Frigate 0.17.2 carried person counts for cameras and zones alike
at two segments and not one message at four.

The parser now tells a camera from a zone by name, with the camera winning, and
matches zone names without case because Frigate keeps the capitalisation the
zone was drawn with (`Whole_Living_Room`). The four-segment form stays accepted
so a Frigate that namespaces zones, or a fixture written that way, still feeds
the publisher. Every test for zone occupancy had been written against the shape
Frigate never sends; the new ones use the real shape and fail eleven ways
against the previous parser.

No deploy: the publisher is not running yet.
