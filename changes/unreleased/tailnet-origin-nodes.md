---
title: Add separate tailnet hostnames for the Echo Agent and Victoria sign-in
target: backend
type: added
---

The Echo Agent and Victoria sign-in get their own tailnet addresses,
`echo-agent` and `victoria-agent`, each served by a bounded userspace node on
the LA host. The existing `home-app` node keeps its identity and existing
handlers; its only change is a separately added Victoria Home Assistant
handler on `:10001`. A daily refresh keeps the Victoria sign-in certificate
current and retries any container restart it had to defer during an owner
sign-in window.
