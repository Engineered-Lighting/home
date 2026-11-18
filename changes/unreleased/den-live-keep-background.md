---
title: Keep the Den figure's learned room across a restart
target: backend
type: changed
---

den-live now keeps the empty den it learned. After a restart or an install while someone is in the den, the figure shows at once instead of waiting until the room is empty for a few seconds. The kept room is used only for the same scan and the same camera geometry, and only within a day of learning it. The next empty spell learns it afresh, as before.
