---
title: Add a sofa check that tests whether the den camera sees a reader on the sofa
target: internal
type: added
---

Adds `tools/den-live/sofa_check.py`, the first step of the live den view, which the owner runs as one paste on the workstation.
- It speaks cues while the owner reads on the sofa, then holds a phone, then leaves. It reads the den camera's frames with den-take's own client and keeps them in a private folder outside every checkout.
- It runs the den simulation's detector on those frames, and den-observe's depth lift on the torso joints, using the camera's own calibration through den-register's reader.
- It asks Qwen six times through the den simulation's two-visit ask, unchanged.
- It prints counts and a verdict: SOFA SEEN, PERSON ONLY, NOT SEEN, CHECK AGAIN or DETECTOR ONLY. It never prints a box, a joint, a coordinate or a reply's text.
- Its tests run on the simulation's rendered takes through a fake camera and the simulation's fake vLLM, with no camera, inference host or ssh.
