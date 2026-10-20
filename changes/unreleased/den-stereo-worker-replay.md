---
title: Require retained stereo originals before ordinary Den replay
target: backend
type: added
---

Add a local replay entry that verifies pinned RGB/depth files, original Lab
receipts and their committed journal correspondence before invoking ordinary
worker replay. Missing or changed originals remain incomplete; successful
software replay does not qualify perception or physical lighting.
