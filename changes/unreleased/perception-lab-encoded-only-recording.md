---
title: Allow the first Lab recording without the camera JPEG pilot
target: backend
type: added
---

Add explicit encoded-only provisioning for recording, replay and labeling using
the existing RTSP stream. This mode does not create JPEG or numerical readers,
require their clock/token provisioning, or enable inference. Existing auxiliary
capture configuration remains available separately.
