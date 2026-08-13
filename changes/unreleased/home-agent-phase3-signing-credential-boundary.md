---
title: Add host-bound Phase 3 signing credentials
target: backend
type: added
---

- Generate distinct review, finalization, writer-freeze, privacy-probe, and
  semantic-cutover signing keys without writing plaintext keys to disk.
- Bind the encrypted credential policy to the hosted-accepted source pack,
  exact Core image and schema, deployment policy, and current durable shadow
  authorization.
- Issue or exactly resume that content-free shadow authorization from the
  reviewed Phase 2 evidence before any private People packet is staged; this
  does not change the configured rollout mode.
- Require a content-free provisioning receipt and all ten protected credential
  blobs before the authoritative activation runner can take a backup or alter
  service state.
- Build and smoke the production Core image only on GitHub-hosted Linux, pin
  its Python base for Linux AMD64, hash-lock its complete Python dependency
  graph, and make the main-branch deployment handoff an attested
  checksum-verified archive instead of an Ubuntu-side build.
