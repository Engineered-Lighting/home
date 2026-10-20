---
title: Keep the Den perception view connected while recording starts
target: web
type: fixed
---

The Den perception viewer now waits through an existing Lab session's requested
and starting states, so it can show the exact camera frame and pose once recording
begins. Reads remain serial and scoped; terminal or unsupported states stop polling.
