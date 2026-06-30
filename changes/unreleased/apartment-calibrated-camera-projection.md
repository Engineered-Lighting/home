---
title: Improve Apartment calibrated camera overlays
target: web
type: fixed
---

Apartment camera snap views now use solved camera centers and calibrated
projection matrices when calibration metadata is available, enrich incomplete
Home Assistant apartment models from the tracker's live calibration cache, and
clearly label uncalibrated camera snaps as estimated previews instead of exact
overlays.
