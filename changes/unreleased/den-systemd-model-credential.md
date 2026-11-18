---
title: Accept protected systemd model credentials
target: backend
type: fixed
---

Den's optional movement-intent bridge now accepts root-owned credentials provided through systemd's protected read-only credential mount. Ordinary token files must still be private to their owner, and credential reads reject symlinks and oversized files.
