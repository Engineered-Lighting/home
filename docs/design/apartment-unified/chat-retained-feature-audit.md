# Retained Chat feature audit

October 18, 2026. Read-only reconciliation of [APT-C01 / #253](https://github.com/Engineered-Lighting/home/issues/253).
Inspected baseline: `0636087c516f5538087f1e9fb216b8c05da55f95`, which composes
the first-wave UI and F04 work. Concurrent C05 header changes are not credited
by this audit. No browser, microphone, camera, model, host or physical trial was
run for this audit. F05/F06 remain contract preparation only.

**C01 remains partial.** The existing mounted Chat and its lifecycle have strong
controlled regression coverage. Two concrete acceptance gaps existed at the inspected baseline: actual
proactive feed entries do not qualify for unread, and the generic Chat output
callbacks do not use the final Travel Mode barrier exercised by Apartment's
guard tests. Feature presence alone does not complete “Every chat feature from
before works in the tab” or “Travel Mode still blocks all light output.”

The subsequent bounded UI change corrects actual `proactive` and `assistant`
unread qualification under every existing guard. Test-first evidence grew from
nine to thirteen deterministic cases and from 62 to 78 mounted browser checks;
the original checks remain intact. The generic Chat final-dispatch Travel gap
below remains open with its owners. C05 header actions add no lighting output.

## Existing implementation and evidence

| Requirement / retained feature | Implementation credit | Evidence and limit |
| --- | --- | --- |
| One mounted Chat, composer and controller | `HomeChatSurface` hides the unified tab without unmounting its children. Chat input and feed remain owned by `HomeNormalApp`. | `test-apartment-home-shell-browser.mjs` checks actual controller/canvas/composer identity and unsent draft continuity across desktop/phone tabs and legacy rollback. It uses the complete application with disposable authored room inputs and fenced transports. |
| Grouped Home/user/external/system feed and streaming | `ApartmentChatTurn` retains `TurnHeader`, `TurnBody`, original events, stable keys and `EventContent`. | Four source-derived presenter tests check callback/event identity and reject lost Undo, cloned events and unstable keys. The authored React proof completes streaming text across layout changes. This does not run a live assistant. |
| Action cards, control-card lifetimes and Undo | The existing confirm, cancel, Undo, control action and lifecycle map reach the same presenter. | Presenter tests verify exact forwarding. Actual React/browser checks consume Undo once, switch layouts and verify it stays consumed. They prove retained local card state, not physical output or complete command qualification. |
| Private preference / lighting review | The same `personal-memory-review` event and `mountReview` callback reach `PersonalMemoryReviewContent`. The existing governed modules retain their exact frame/origin/nonce boundaries. | The focused unread proof and shell's React proof retain one authored review iframe, with one mount/load and no cleanup during layout changes. The negative conditional-component-type fixture reproduces the earlier reset. Authored `srcdoc` is not a live private review or an authorization test. |
| Unsent draft and unread interaction | Opening/closing Chat retains the same draft element and value. Hidden Home/external replies set unread, visible Chat consumes it, and retired eligible replies clear it. | `wave1-qa.md` records nine deterministic tracker cases and 62 source-derived React/browser checkpoints, including credential/conversation isolation, covering overlays, first stream token and phone peek. Those cases do not include the actual `proactive` event kind. |
| Slash menu and dispatcher | Existing `InputRow`, catalog and `handleCommand` remain mounted. | `run-slash-command-tests.js` checks catalog/handler coherence, Up/Down/Tab/Enter completion, callback wiring and Send/Stop selection. Most are source contracts; they do not establish every command's rendered or backend behavior. |
| Mic and Stop | Existing `toggleMic`, voice state, `stopStreaming`, active-run cancellation, timers and stream IDs remain in their owners. | Slash contracts inspect the existing wiring and cancellation body. Full shell acceptance checks the actual phone mic/Send geometry. The focused lifecycle proofs do not acquire audio, open a voice transport or prove remote tool cancellation. |
| Travel access and existing refusal | Lights / Travel Mode remains reachable; the inline light card retains its cached Travel lock. Apartment's existing data/host/Aim dispatch paths retain their stronger live helper barrier. | `run-control-card-tests.js` covers UI lock/cache behavior. `test-apartment-travel-guard.cjs` exercises Apartment dispatch refusal, missing/unknown helper, state changes during awaits, target snapshotting and late reads. Neither establishes the generic Chat callbacks described below. |
| Why / explain and debug | Existing `onWhy` callback reaches the same explain slot; existing event eligibility and interactive-card behavior remain in `EventContent`. | The actual presenter callsite test invokes the explanation callback. It does not inspect a live trace or validate every debug command. C05's header Explain action is separate work. |
| Pending-confirm Escape and guarded clear | The old Chat Escape handler still checks `HomeOverlay.hasBlockingLayer()` before cancelling pending-confirm cards. `/clear` still arms the existing confirmation card; only confirming that card clears the conversation. | F04 adds source-owned operation layers and scoped editable ownership; it does not replace the Chat clear/confirm callbacks. Layer tests establish dispatch ordering, not the result of every pending action. |
| Conversation privacy / clipboard | Existing `home-tauri.jsx` `loadEvents`/`saveEvents` and conversation-ID persistence are session-only stubs. Private review/session credentials remain in their existing owners. | This source audit does not credit C05 clipboard behavior. Any copy action must use its own explicit ordinary-transcript allowlist and tests, excluding private review, private camera context, credentials, incomplete turns and diagnostics. |

The recorded first-wave runs are described in [wave1-qa.md](wave1-qa.md), with
the original design reconciliation in [design-qa.md](../../../design-qa.md).
Their authored checks and bounded private actual-room smoke result are distinct.
This audit does not rerun those browsers or convert their receipts into current
installed, physical or complete C01 acceptance.

## Baseline gap 1: actual proactive unread (corrected in this UI change)

[`home-proactive.jsx`](../../../app/src/home-proactive.jsx) `emitFeed` emits
`{kind: "proactive", text}`. The inspected `createApartmentChatUnreadTracker`
qualifies only `home` and `external`. Consequently a new genuine proactive
entry arriving while Chat is hidden never sets the dot, despite C01's explicit
“Proactive messages set the unread dot on Chat” requirement.

The presentation-only correction admits the existing proactive event kind
under the same stable-ID, scope, visibility, retirement and history rules, then
adds deterministic and rendered regression cases using that producer kind.
Do not replace or restart the proactive coordinator. Lighting articulation
uses a different existing `assistant` kind; this change explicitly includes it
under the same gates and verifies it separately from Home-reply fixtures.

## Gap 2: Travel Mode at generic Chat output

At the inspected commit:

- [`undoAction`](https://github.com/Engineered-Lighting/home/blob/0636087c516f5538087f1e9fb216b8c05da55f95/app/src/home-app.jsx#L7667)
  resolves the original target, then submits the inverse through raw
  `client.call`. Undoing `light.turn_off` can therefore submit `light.turn_on`
  without a live Travel helper check in this callback.
- [`onControlAction`](https://github.com/Engineered-Lighting/home/blob/0636087c516f5538087f1e9fb216b8c05da55f95/app/src/home-app.jsx#L7723)
  validates target presence and submits through the same raw client path.
  `LightControlCard` has a cached UI lock, but the final callback does not
  perform the current helper read or retain a pending-on latch.
- [`HAClient.call`](../../../app/src/home-ha.jsx) is a generic WebSocket
  request/result transport, without a Travel policy check.

A read-only VM probe extracted those two actual callbacks from the pinned
commit and supplied only an authored transport spy. With the same-tab and
cached Travel values both `on`, each callback submitted one `light.turn_on`
payload to the spy. **No real transport or physical command was invoked.** This
reproduces a source-level acceptance gap; it does not assert that a deployed
fixture changed or that any particular host lacks an independent safeguard.

The narrow owning follow-up is a final dispatch guard for these existing
callbacks, agreed with the Chat/control and Repair owners. Preserve the
original targets, callback meaning, error reporting, Undo lifecycle and
explicit off operations. Tests must exercise the actual callbacks with a
controlled helper/transport, including refusal during an outstanding read.

### Reuse boundary for that follow-up

`HomeApartmentData.callService` already snapshots output payloads and uses a
live `get_states` helper read, a four-second timeout, currentness checks and
pending-on/unknown latches through dispatch. It is **not directly interchangeable
with global Chat**: its current contract requires Echo and checks Apartment's
selected viewport and workspace authority. Chat's explicit/default command
home is a separate source of targeting; choosing a visual home cannot grant
or replace that authority.

The owners should agree a shared final guard or a narrowly extracted helper
using the existing Chat client's captured endpoint, connection generation and
command scope, with currentness rechecked after awaits. A payload snapshot must
retain entity/area/device selectors exactly. Missing, malformed, unavailable,
timed-out or changed helper/context must refuse without queuing output; an
observed pending `on` cannot be erased by a later old `off` result. Explicit off
and unrelated media behavior retain their existing semantics.

`HomeLightingControl` is a separately governed cross-home review path; its
public `run/reset/resolveHome` interface is not a generic service guard. This
audit proposes no registry/capability schema and activates no F05/F06 contract.

## Remaining acceptance

- Producer-kind unread is corrected and all retained tracker rules pass the
  expanded deterministic and mounted proofs; retain those regression checks.
- Close the generic Chat final-dispatch Travel gap with its owners and tests;
  do not infer it from Apartment-only guard coverage or action-card suppression.
- Run a bounded authored Chat regression matrix on the final combined commit:
  ordinary and external streaming, action confirm/cancel/consumed Undo,
  controllable cards, slash navigation, mic/Stop callbacks, review identity,
  draft preservation, why/explain and pending-confirm Escape.
- Treat any hardware/audio/backend acceptance separately. Existing private
  actual-room evidence, registration and imagery remain private; no public
  artifact needs household data to test these UI/controller seams.

These are acceptance gaps and a work handoff. They are not new command
authority, a merged release, deployment or permission to operate a host.
