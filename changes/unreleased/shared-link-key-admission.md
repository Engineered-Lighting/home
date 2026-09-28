---
title: Operator command to admit the account-linking key
target: backend
type: added
---

Account linking could not issue a ceremony in a fresh deployment: Core refused
every issuance with `shared_link_key_not_admitted` because nothing recorded the
linking coordinator's commitment key. `app.shared_link_key_admission` records
that key once, as the database owner, and refuses a different or blocked key.
The cutover prerequisites now include this step.
