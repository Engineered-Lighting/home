---
title: Back up the LA AI host to the Victoria UNAS every night
target: backend
type: added
---

The LA AI host's encrypted restic backup to the Victoria UNAS now runs nightly,
with a weekly repository check and an hourly freshness alert. It covers the full
Home Assistant backups, Den Live labels and history, and host operator state.
Before this, only 41 HA config files reached the UNAS. The units, scripts and
selection now live in `stack/ops/unas-backup/`.
