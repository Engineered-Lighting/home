---
title: Preserve incumbent Lab observation roots in connected Den admission
target: backend
type: fixed
---

Add an explicit private admission successor that uses the same persisted
observation roots as Perception Lab reasoning, including original event IDs when
camera envelopes omit explicit roots. Preserve raw originals, HA snapshot roots,
historical policies and current-work attribution checks; the matching capture
join rejects reuse of an original root for a different frame or observation time.
This source change does not qualify geometry or activate a live service.
