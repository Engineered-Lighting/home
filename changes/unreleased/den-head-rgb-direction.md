---
title: Recover coarse head direction from calibrated RGB landmarks
target: backend
type: fixed
---

Den Live can fit coarse head direction from the current RGB face landmarks when stereo facial depth cannot support an axis. The fit uses the existing camera calibration, stays bound to the current person and frame, and abstains on ambiguous or uncertain geometry.
