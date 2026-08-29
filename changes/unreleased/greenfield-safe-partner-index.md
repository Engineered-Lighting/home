---
title: Fix the Partner Relationship Index on a Fresh Database
target: web
type: fixed
---

The migration chain could not run to completion on a newly created database. Revision 0023 created `uq_active_partner_relationship` with a bare `CREATE UNIQUE INDEX`, but `app/schema.py` already declares that index and the greenfield revision builds its database from that module — so the index existed before 0023 ran and the migration aborted with `relation already exists`. The deployed system was unaffected, because it reached 0023 from a database built by an older version of that module.

This went unseen because the hosted gate stopped migrating at revision 0021: revisions 0022 through 0027 had never been executed by CI at all. The gate now migrates to 0028, and a new check fails any migration that recreates an index the greenfield schema already declares.
