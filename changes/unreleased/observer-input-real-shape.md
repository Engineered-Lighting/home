# Belief publisher: make the observer poll actually work against the observer

Two defects, both found by pointing the publisher's own client at the running
observer before the shadow week rather than after it.

The size cap was 256 KiB. The observer's `/api/state` is its entire state and
measures 717 KiB on this house, 558 KiB of which is the base64 `pose_image` of
each worker's last frame. Every poll would have failed, leaving the observer
permanently stale; since health is mirror fresh and observer fresh and MQTT up,
the publisher would have been unhealthy for the whole shadow week and published
`unknown` for every belief. The observer serves no narrower endpoint and
honours no query parameter that trims the body, so the cap now fits it. A live
poll costs about five milliseconds and nothing but counts survives the parse.
A narrow presence endpoint on the observer is the better fix and is parked: it
is an observer change, so it needs a window that restarts live inference.

The parser read presence from the `cameras` map. On this observer that map
carries stream health and an inference gate; the detector results live under
`latest.<camera>.<worker>`. Against the live observer the old parser found
three cameras and zero readings, so the corroboration the night guard rests on
would never have been satisfied. It now reads the people count from the first
valid detector worker, YOLOX first, then the pose detector, then V-JEPA, and
still accepts the documented flat shapes underneath.

It deliberately does not read `cameras.<name>.inference_gate.occupancy`, the
one field in the payload that says "occupied" in words. Its own reason on this
house is "person detected by Frigate": it is derived from Frigate, and the
estimator asks the observer to corroborate Frigate. Reading it would make that
corroboration agree by construction, so a stuck Frigate zone would read as
confirmed by vision and the night guard would rest on one sensor while
appearing to rest on two.

Verified against the running observer: three polls, no failures, three of three
cameras read, matching what Frigate reported at the same moment.
