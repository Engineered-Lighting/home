# Chat action refusal verification

This bounded source change follows the Chat presentation work at
`557164592f916cc4f93b79214f560d24d6f797b6`. It changes no renderer, selector,
perception/session, backend schema or physical authority producer. F05/F06
remain contract preparation; C01 remains partial.

## Current behavior and ownership

Repair approved a separate refusal change for the two raw Chat callbacks.
Existing action cards do not identify their originating connection epoch or
carry qualified command-home authority. The current client, a selected home,
Auto location or an old action target cannot supply that missing binding.

- `undoAction` and `onControlAction` no longer call the raw HA transport.
- The sibling `HomeApartmentData.refuseChatServiceEnvelope` reuses the existing
  lighting-output classification for an explicit home-authority refusal.
  Off/media and other physical actions receive an originating-connection
  refusal. There is no transport, currentness/authority option or qualified
  global that can activate this function.
- A missing module or an unexpectedly returning refusal function still throws
  in each caller. Neither a raw fallback nor a successful Undo claim is made.
- HA selectors remain separate from service data. Refusal does not serialize,
  retain or modify those fields, and performs no helper read or network call.
- The existing Apartment `callService`, site policy and live Travel Mode guard
  remain unchanged, including their currentness checks, helper timeout and
  pending-on latch. Existing virtual fixtures and simulated control dispatch
  retain their own paths.

No eager dependency is added. The existing lazy loading remains safe because
the two callers refuse when the optional refusal function is absent. Loading
Apartment later cannot activate these Chat actions.

## Test-first reproduction

The initial `tools/test-chat-output-refusal.cjs` extracted the actual two Chat
callbacks and supplied authored transports only. Both new cases failed with
"Missing expected rejection": raw Chat Undo and inline light controls accepted
the fake lighting-output dispatch. No real client, household data, network or
physical output was involved.

After correction, the expanded actual-callback suite passes seven cases,
including unresolved off/media and restored Undo, complete selector/data
envelopes, replacement client/socket/endpoint/auth/epoch, missing guard and an
unexpectedly returning guard. None produces a raw transport request or a false
successful Undo message.

## Current workstation results

Exact changed production-source SHA-256 values:

- `home-app.jsx`: `5037b300721c0023eba3c158562cd758efac9c248314113b308eca3857e5be97`
- `home-apartment-data.js`: `b3ab0c176f63ddcaaf907fcdbf11c6dc92b0a69561be8dfb37fa37cff650cbc1`

| Check | Result |
| --- | --- |
| `node tools/test-chat-output-refusal.cjs` | 7 passing actual-callback cases |
| `node tools/test-chat-service-envelope.cjs` | 23 passing production refusal cases |
| `node tools/test-apartment-travel-guard.cjs` | 25 passing retained Apartment policy cases |
| `node tools/run-control-card-tests.js` | 58 passing control-card checks, including simulated versus raw-handler dispatch |
| `node tools/run-simulation-control-tests.js` | 30 passing simulated light/media checks |
| `node tools/run-chat-ui-regression.js` | Source checks pass; its live browser mode was not run |
| `node tools/check-jsx.js` | 86 source files parsed; zero errors |

All new tests use authored values and fake transports. The refusal suite checks
lighting, switch, aggregate, scene and script output; unresolved off/media;
later globals/options that must not qualify; and no helper, viewport, storage
or transport access. The current request envelope remains unchanged.

## Independent review

Against `55716459`, the standards review found **0 findings**. The independent
spec review found **0 findings and 0 change-specific blockers**, and independently
re-ran the callback (7), refusal (23), retained Travel (25), control-card (58)
and simulation (30) checks. The spec review also confirmed that rejected calls
reach the existing card error UI and cannot mark Undo consumed or successful.
Neither review treats deterministic evidence as installed or physical acceptance.

The final touched-source batch passed **26 mapped harnesses plus JSX parsing**.
Its mounted header/unread regressions retain their original assertions. The
separate full authored Apartment proof passed all **28 checkpoints and 10
screenshots** in 24.52 seconds, within its unchanged 145-second deadline;
receipt `.tmp/apartment-home-shell-browser/run-1792364615747/receipt.json`.
That room/navigation proof does not exercise a real Chat physical transport.

## Acceptance limits

All currently unbound physical Chat cards and their Undo operations are
unavailable in this change, including off/media. Restored cards do not inherit
today's connection. A future source change needs an accepted originating-card
binding and qualified home authority; this PR adds no dormant admission path,
automatic replay or future global activation switch.

These are deterministic software results. Browser presentation, hosted checks,
installed behavior and physical qualification are separate. This work starts
no browser, camera/model session, host operation, merge or deployment, and
publishes no actual-room image, coordinate or private registration data.
