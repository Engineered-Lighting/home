# Named aims: what the UI writes, reads and proposes

APT-A05, A07, A10 and R04, Echo first. This note covers the data this lane writes and the
interfaces it consumes. Code: `app/src/home-apartment-aim-library.js` (logic),
`app/src/home-apartment-echo-aim.js` (Echo's aim host) and
`app/src/home-apartment-aim-library.jsx` (UI and the view hook). The Setup › Named aims mapper's geometry is the separate `home-apartment-named-aims.js` (`AptNamedAims`, #329).

## Echo: Save as named aim

Echo saves through the existing `apartment_model` save (`saveModel`). It uses the same revision check,
and a 409 still never overwrites. Save as named aim adds one `targets[]` record from a valid proposal:

| Field | Value |
|---|---|
| `id` | `target-<slug>-<base36 time>`; it never changes on rename |
| `name` | what the person typed; names are unique per home (case-insensitive) |
| `category`, `shape` | `custom`, `point` |
| `pos`, `normal`, `up` | the proposal's point (mm) and normal; `up` follows the normal |
| `surface` | the proposal's surface name |
| `source` | `aimed` (SPEC › Aim lifecycle) |
| `aim_basis` | **proposed:** `{ scene, collision, fixture_id, profile }` at save time |

An existing name is a conflict until the person chooses. **Replace existing** keeps the existing id
and name and takes the new spot, so references survive. **Keep both** saves under the next free
name ("painting 2").

`aim_basis` is the proposed hash contract for the `changed` state:
- `scene`: the content revision.
- `collision`: the content pack's `collision.glb` digest, when there is one.
- `fixture_id` and `profile`: the light it was aimed from and its `product_profile_sha256`.

When the current scene or collision differs, the aim reads "predates the latest scan". When the
same light's profile differs, it reads "predates the light's profile change". Either way it offers
**Preview it** (propose it) or **Keep as is** (rewrite `aim_basis` and save through the same path).
Aims without `aim_basis` are never guessed changed. The apartment_model server stores targets as
JSON without per-key validation. This field still needs owner approval as a schema addition before
it is relied on.

### Per-aim state from the model save

| Model save state | Aim state | Copy |
|---|---|---|
| `saving` | saving | Saving |
| `saved` | saved | Saved |
| `offline`, `simulation`, `unsaved`, `reviewed` | local | Saved locally · “Name” is saved in this browser only. |
| `conflict`, `validation` | not_saved | Not saved · “Name” isn't saved. The model changed elsewhere, so nothing was overwritten and your draft is kept. |
| (name exists, before saving) | conflict | Conflict · “Name” already exists. Replace existing / Keep both |

Revision conflicts keep the existing compare-then-publish flow (Compare live, explicit publish).
Explicit keep mine / keep theirs remains an A07 follow-up.

## Echo: engineered aim on the shared proposal path

The engineered light's interaction descriptor is
`HomeApartmentNamedAims.echoAimContext(...)`. It is admitted by the shared
`HomeApartmentAimProposals` store under site `echo`. Its fence is built from:
- the content revision and the pack's digests (or a labelled fence key on a legacy scene);
- the model revision;
- the light's mount and profile.

Limits carry `limit_role: 'engineered_profile'`. The presentation then says "Out of reach · N° from
straight down, limit L°" only past the profile's configured `tilt_min_deg`, and never for
Victoria's virtual-preview limits.

Set is one function, `createEchoAimHost(...).set`. The bar, the Aim tab, Enter and (later) a Chat card
all call it. In Simulation Mode it moves the simulation runtime's destination only. Outside
Simulation Mode the light is physical, so Set refuses and there is no command path.

## Automation reference (A10)

The reference is the target id (`targets[].id`), shown with Copy id in the named-aim inspector.
`aim {fixture_id} → {target_id}` stays a proposal (`proposedFixtureReference`). "Which automations
use it" reads unavailable until a producer exists.

## Den named aims (A07 default)

This follows the A07 default and Den's constraints of 2026-10-19:
- Den named aims live in a Den-owned store bound to the registration revision.
- The configured desk is a linked, read-only aim. It is the same pinned target the preview uses.
- There are no presets.

The UI consumes this adapter interface:

| Member | Shape |
|---|---|
| `view(registrationRevision)` | `{ available, reason, aims: [{ id, name, point_m, normal_m, source, read_only }] }` |
| `save({ name, point_m, normal_m, fixture_id }, { currentRevision, resolution })` | `Promise<{ ok, state:'saved', aim } \| { ok:false, conflict, existing, read_only } \| { ok:false, reason }>` |
| `recall(id, { currentRevision })` | `{ ok, reference: { named_aim_id, point_m, normal_m }, evidence: null }` |
| `subscribe(listener)` | unsubscribe function |

The adapter's guarantees:
- **Revision change:** a different registration revision makes every name unavailable (`view` →
  `available: false`; `save` and `recall` refuse). Nothing is re-projected.
- **Read-only desk:** Replace is refused for the read-only desk; only Keep both saves alongside it.
- **No evidence:** recall returns only a reference. It creates no presence, activity or target evidence.

The authored adapter (`createAuthoredDenNamedAimStore`) is for tests only. Production uses
`denNamedAimsUnavailable()`: no aims, Save shown disabled, and the reason "Den named aims wait on
the Den’s named-aim store." It never writes the desk, the candidate or the registration. The real
store waits on Den's contract.

## Chat aim proposals (C04)

`app/src/home-apartment-chat-aim.js` is the agreed UI-side parser:
- **What it handles:** an aim verb (aim, point, shine, focus, spotlight, light up) plus a named aim of the current home.
- **When it runs:** in `sendInput`, before personal memory, lighting control and the agent.
- **What it never does:** a message it handles goes nowhere else.

The Apartment publishes an aim bridge as `shellState.aimBridge` with `view`, `propose`, `live`,
`set`, `discard`, `preview` and `subscribe`:
- `propose` uses the shared store with origin `"chat"`.
- `set` and `discard` call the Echo host's own Set and Discard, the same ones the room bar calls.

`ApartmentChatTurn` renders `apartment-aim-proposal` events as `AptChatAimCard`, whose states are:
- `Proposed · simulated`
- `Aim set`
- `Discarded`
- `No longer proposed`

Refusals:
- **Physical light:** an Echo light outside Simulation Mode is refused with
  `COPY.chatPhysical`.
- **The Den:** refused with the Den store reason.
