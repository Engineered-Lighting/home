---
title: Contain Home Agent initiative presentation
target: desktop
type: changed
---

Removes initiative listing, claiming, and greeting presentation from the
deployed Home Agent browser/native client. Future initiative domain logic may
remain isolated and testable in Core, but no client capability is exposed
without a separately reviewed initiative-capability and presentation gate. A
native-only selectable card exposes only the installation UUID and public JWK
needed for offline operator enrollment; it performs no enrollment action.
