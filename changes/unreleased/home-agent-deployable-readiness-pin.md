---
title: Let the deployment choose which schema revision Core serves
target: backend
type: fixed
---

Core pinned its readiness revision in source (`readiness_migration: str =
"0006a_worker_lease_arbitration"`). Revision `0007` records that this is
deliberate — Core "must fail closed on revision 0007 until the later atomic
finalizer and authoritative-readiness release updates the runtime pin" — but no
such release ever shipped. Once the Phase 3 activation migrated the database
past `0006a`, every Core service failed closed at startup with `database
migration mismatch`, and the activation could not restart the Agent services at
all.

The pin is now an allowlist of exactly the six revisions the image itself
declares it can migrate to, and the deployment picks one:

- `app/config.py` types the field as a `Literal` over those six revisions. An
  unlisted value fails `Settings` at startup rather than silently serving a
  schema the image was never released to handle. The default is unchanged, so
  Core still cannot promote itself — advancing the pin remains a deployment
  decision.
- `home-agent-compose.yml` forwards `HOME_AGENT_READINESS_MIGRATION` to the five
  Core services, defaulting to the pre-Phase-3 revision.
- The activation runner now rewrites that key alongside
  `HOME_AGENT_EXPECTED_DB_REVISION`. It previously rewrote only the latter,
  which the image entrypoint reads for migrations and Core never reads at all,
  so the Agent services would have kept the stale pin and crash-looped at steps
  26 and 33.
- Because the key did not exist before this release, the runner writes it
  whether or not the deployed environment already declares it. Requiring it to
  pre-exist would abort the first rewrite after the upgrade, and every rewrite
  at or after `stop_home_assistant` is contained forward-only — the ceremony
  would strand with the Agent services stopped. Missing pre-existing keys and
  duplicated keys are still refused.

A contract test keeps the allowlist identical to the entrypoint's own
`DEPLOYABLE_MIGRATION_REVISION` and `PHASE3_*_REVISION` values, and asserts that
every revision the runner starts Agent services at is servable.
