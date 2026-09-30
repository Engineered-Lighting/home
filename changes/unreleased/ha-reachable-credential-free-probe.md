---
title: Home Assistant reachability probe no longer looks like a failed login
target: internal
type: fixed
---

The LA host's `ha-reachable` probe now checks Home Assistant's login-provider
page instead of `/api/`, so it needs no token and never records "invalid
authentication" from the host that runs the Home gateway and Home Agent. The
probe script and its systemd units are now tracked in the repo.
