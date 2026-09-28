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
Retain the exact consent review in an encrypted, bounded recovery journal and
persist dispatch state before writing grants, so restart or repeated confirmation
cannot silently repeat an uncertain operation.
Compose retained review, confirmation, and fresh database outcome lookup with a
dedicated bounded connection pool; repeated confirmation performs lookup only.
Expose optional consent operations through the existing private Core and
authenticated Home gateway routes, preserving origin/CSRF checks and strict
request/response scope validation. Production consent remains unconfigured.
Add a separate sharing review in the Home Agent panel, showing both homes and
the permission expiry, with explicit confirmation and lookup-only recovery.
Allow explicit file-based consent provisioning in the private preference
listener; open its durable journal only after Core admission and close resources
on shutdown. Omitted consent configuration keeps the feature disabled.
