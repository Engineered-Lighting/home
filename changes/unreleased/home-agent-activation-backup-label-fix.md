---
title: Read the activation backup label from the preflight backup report
target: backend
type: fixed
---

The Phase 3 activation runner read `latest_full_backup_label` from the root of
the preflight report instead of its `backup` mapping, so the local-backup and
restore-drill steps failed closed immediately after taking a valid backup.
