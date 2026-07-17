---
title: Isolate PostgreSQL from async archive children
target: internal
type: fixed
---

Runs the Home Agent PostgreSQL container behind Docker's minimal init process
so detached pgBackRest archive workers are reaped by the init process instead
of PostgreSQL. This prevents an unavailable off-host SFTP repository from
turning an archive timeout into repeated database crash recovery. Deployment
preflight also locks an unlimited, no-drop WAL archive queue and the encrypted
archive spool settings.
