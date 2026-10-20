---
title: Keep Apartment camera and inference viewing responsive during read failures
target: web
type: fixed
---

Apartment now reads the selected analyzed camera frame before waiting for larger inference summaries. An unavailable image or temporary metadata outage keeps independent inference results visible and allows subsequent frames to recover, while authentication and scope failures still clear private data.
