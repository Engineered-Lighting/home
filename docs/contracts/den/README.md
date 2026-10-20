# Den contracts for the Home UI

**Status:** proposed, 2026-10-20. These are read-only contracts that the Home UI
dock and status-row lanes can build against. They describe what Den producers
supply today, and they mark what is **not yet supplied**.

They grant no authority. Nothing here enables physical output, starts a
session, or changes a producer. All examples are synthetic: no household
coordinates, imagery or private digests.

| Document | What it covers | Tickets |
|---|---|---|
| [reason-vocabulary.md](reason-vocabulary.md) + [reason-vocabulary.json](reason-vocabulary.json) | Every reason and status code a dock or status row may show verbatim, each with short user-facing text. The retired fixed-desk-preview codes are marked legacy. | APT-S02, F06 |
| [home-capabilities.md](home-capabilities.md) | The capability and state fields Den supplies per home: Home Assistant (HA), engineered, perception, body, assets and bench, with frame-age and expiry fields. | APT-F05, F06 (#224, #225, crosswalk #281) |
| [named-aim-store.md](named-aim-store.md) | The named-aim record (desk, sofa, later saved places), its revision and conflict rules, and the pinned read-only brain export. | APT-A07 |
| [body.md](body.md) | Pose joints and flags as the activity loop emits them, and the nominal body profile (not yet supplied). | APT-V03, V04, P05 |
| [fixtures/](fixtures/) | Synthetic example documents for each contract. | |

The fixtures are checked by `node --test tools/test-den-contract-docs.mjs`.
That test parses every fixture and checks it against the vocabulary and the
live validators.

## Sources

These contracts summarize the following sources. When a source and a
document disagree, the source wins; fix the document.

- **Activity lighting:** `docs/plans/den-activity-lighting/CONTRACT.md` (local
  plan): the state, events and overrides.
- **Lighting brain:** `stack/services/lighting-brain/` (AL-1, PR #332).
- **Activity loop:** `stack/services/perception-lab/activity_loop*.py`,
  `activity_pose.py` (AL-3).
- **Monitor:** `web-gateway/den-ops-status.mjs` (AL-5, PR #346).
- **Home:**
  - the brain client and follower:
    `app/src/home-lighting-brain-{client,follower}.js`;
  - perception: `app/src/home-apartment-perception-view.js`;
  - the sofa mark: `app/src/home-lighting-sofa-target.js`;
  - the brain export: `tools/den-room/brain-targets.mjs`.
- **UI crosswalk:**
  `docs/design/apartment-unified/FIRST-WAVE-CAPABILITY-STATE-CONTRACT.md`
  (#281, branch `codex/home-ui-capability-state-contract-prep`).

## Rules that hold everywhere

- **Supported, available, permitted.** Supported means the app can do it,
  available means this home can do it now, and permitted means an authority
  allows it now. These are three separate answers. A capability value never
  grants permission.
- **Missing stays missing.** An absent time, joint or reason is shown as
  unavailable, never filled in.
- **Original times.** Producer times are never restamped on receipt. A fresh
  poll does not extend `valid_until`.
- **Home isolation.** Every value is scoped to one home (`site_id`). Another
  home's value is "not applicable", never a fallback.
