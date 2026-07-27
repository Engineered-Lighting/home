---
title: Accept the guarded pgBackRest latest link during restore drills
target: backend
type: fixed
---

Allow pgBackRest's standard `backup/home-agent/latest` symlink during isolated
restore validation only when it points directly to an existing full-backup
directory in the same stanza. All other symlinks and special files remain
fail-closed.
