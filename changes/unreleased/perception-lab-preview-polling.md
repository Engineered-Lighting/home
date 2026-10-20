---
title: Poll registered camera previews for recording-owned perception
target: backend
type: added
---

Add a fixed authenticated local camera client and bounded preview polling that
continues independently of Home navigation. Stop accepts no late frame, and
controller shutdown drains source requests before releasing storage ownership.
