---
title: Applying Grants No Longer Breaks the People Tab on Older Databases
target: backend
type: fixed
---

Re-applying database grants could abort partway on any database that had not yet taken the newest identity revision. Because the script commits as it goes, and one of its first acts is to withdraw the application's read access before granting it back, an abort left that access withdrawn — taking the People tab's ability to read the household down with it, not merely the change being applied.

The step now looks up who currently owns the function it is granting on, rather than assuming. It behaves correctly on databases either side of that revision.
