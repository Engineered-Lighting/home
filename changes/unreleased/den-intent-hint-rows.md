---
title: Show Den's advisory intent hints in the pipeline
target: web
type: added
---

The Den pipeline's Perception details now show the intent hint Den publishes, labelled advisory and uncalibrated, with its model revision, whether it is bound to the current frame and track, its age and expiry on the producer clock, and Den's disposition, so a completed inference no longer looks like an idle loaded model. Den model-stage reasons such as worker unreachable, Qwen busy and model not resident now read as plain words instead of Unknown.
