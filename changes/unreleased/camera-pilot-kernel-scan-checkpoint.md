---
title: Camera pilot kernel-fault scan resumes from a checkpoint and never orphans scans
target: backend
type: fixed
---

The LA camera pilot's kernel-fault scan now runs in its own session. A scan that times out is ended through `sudo`, so it no longer leaves root `journalctl` processes running in the pilot's memory cgroup. After a failed scan, no new one starts for 30 s. Each clean scan records where it ended in `private/kernel-fault-checkpoint.json`, so restarts and long idles resume from there in bounded 6-hour chunks instead of re-reading weeks of journal. A fault still refuses admission until the owner records it as inspected in `private/kernel-fault-receipt.json`; no release is needed. A `sudo -n` refusal no longer reads as a clean journal.
