---
title: Prepare isolated Victoria camera pilot releases
target: backend
type: added
---

Prepare a source-and-dependency manifest for the supervised Victoria JPEG pilot
without changing the running camera environment. Record the read-only clock and
camera prerequisite evidence separately from live acceptance.

Stage protected one-image timing configuration separately from the running
camera, with offline validation on the Jetson before service activation.
Add a fixed-release service override helper with baseline checks and retryable
rollback after reload failure; activation remains an explicit privileged step.
