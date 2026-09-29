---
title: Remove camera and MQTT credentials from the Frigate example and bench configs
target: internal
type: fixed
---

The Frigate example config and the frigate-bench configs contained the camera
RTSP login and the Frigate MQTT password. They now use Frigate's
`{FRIGATE_RTSP_USER}`, `{FRIGATE_RTSP_PASSWORD}` and `{FRIGATE_MQTT_PASSWORD}`
placeholders. The old values remain in git history, which is now public, so the
camera and MQTT passwords should be rotated.
