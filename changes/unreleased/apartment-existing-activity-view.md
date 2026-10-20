---
title: View the running Apartment analysis alongside camera and model updates
target: web
type: added
---

The camera dock can view the existing Victoria activity loop across recording rolls without starting analysis. It shows supplied approximate body joints and a bounded model-update history, retaining source times and withdrawing the current view when the loop pauses, stops or becomes unavailable.

The live camera also shows the actual daily Qwen structured feed, its model artifact and a separate twenty-update history. Original source times, expiry and daily-camera scope stay distinct from the Lab session, and measured browser receipt cadence is labeled separately from capture or inference latency.

Actual pose and JEV results retain priority when the source catalog exceeds the display bound; uncommissioned catalog entries cannot crowd them out of the model history. Hiding a paused camera dock also stops its activity-loop status reads.
