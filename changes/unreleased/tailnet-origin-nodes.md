---
title: Add separate tailnet hostnames for the Echo Agent and Victoria sign-in
target: backend
type: added
---

Add bounded userspace tailnet nodes on the LA host for the `echo-agent` and
`victoria-agent` browser origins, plus a daily certificate refresh for Victoria
sign-in that holds restarts during owner sign-in windows. Browser sessions
for the two homes get their own hostnames without retagging or reconfiguring
the existing `home-app` node.
