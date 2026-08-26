---
title: Install the source-projection loader the freeze observer imports
target: backend
type: fixed
---

The writer-freeze observer runs on the Home Assistant host and imports
`migrate_legacy_identity` from `/config/home-agent-operator`, then hashes that
module's bytes into its observation. **Nothing ever deployed that directory.**
The string appeared exactly once in the repository — the constant itself — and
`ls` on the live host confirmed it absent.

So the freeze step raised `source projection could not be verified`, at step 20,
immediately after step 19 had stopped Home Assistant and past the runner's
forward-only containment boundary. Its tests never caught it because they pass
`operator_root` pointing at the Ubuntu host's operator directory, so the
production default was never exercised.

`install-ha-operator-module.sh` installs the module and verifies the copy is
byte-identical to the pinned operator source.

The runner now refuses to proceed unless that copy is present **and** matches.
Existence alone would not be enough: the observation embeds a digest of the
module, so a stale copy would record a digest describing code that did not run.
A mismatch pauses the activation before Home Assistant is stopped, rather than
failing after.

Re-run the installer after any source pin that changes that module.
