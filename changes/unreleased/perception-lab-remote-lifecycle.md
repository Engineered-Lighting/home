---
title: Drain remote Lab execution and recheck grants
target: stack
type: added
---

Close remote admission during ASGI shutdown and retain owned storage until
in-flight work drains. Recheck grant revisions before dispatch and result delivery;
revoked requests do not receive results. No remote grant-management API is exposed.
