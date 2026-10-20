---
title: Add one-shot camera host-clock evidence production
target: backend
type: added
---

Add bounded read-only NTP sampling and an explicit Linux one-shot publisher for camera host-clock evidence. Preserve original expiry, enforce protected atomic publication and cross-invocation polling/refusal state, and require commissioned trust and uncertainty inputs. No time daemon, clock change or production configuration is enabled.
