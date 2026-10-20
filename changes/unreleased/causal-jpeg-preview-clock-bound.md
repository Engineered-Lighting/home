---
title: Count the causal JPEG preview clock bound once
target: backend
type: fixed
---

The registered JPEG preview now uses the complete validated schema3 camera uncertainty once instead of adding the same host-clock term again. Schema2 retains its independent receiver-clock addition, and original identity, expiry, duplicate and Stop checks remain enforced; the correction is validated offline pending successor activation.
