---
title: Bind preference-sharing consent to the reviewed accounts and scope
target: backend
type: added
---

Define a separate, expiring owner review for sharing confirmed preferences across
the two linked homes. Bind confirmation to the exact session, link generation,
source revisions, exact grant identities and revisions, and displayed grant expiry. This contract does not
create grants; account linking alone still provides no shared-memory access.
Add the dedicated-role transaction writer and read-only outcome lookup for the
four reviewed grants. Database role provisioning and hosted validation remain
required before this writer can be enabled. Add owner-only preparation of the
dormant role with fixed preference-source row policies; preparation does not
enable login or create any grants for a person.
