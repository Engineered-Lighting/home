# Aim overlay: test-facing selectors and ARIA

The in-room aim overlay (`app/src/home-apartment-room-overlay.jsx`, placed by
`app/src/home-3d/apartment-aim-presentation.mjs`) and the lifecycle suite
(`tools/run-apartment-aim-lifecycle-browser.mjs`) build against this one
vocabulary, taken from SPEC.md › Gestures, › Aim lifecycle and › Spot or zones
before any code. The product exports it as `AIM_OVERLAY_VOCABULARY`; both test
suites keep their own literal copy and fail if either side changes alone.

`app/src/home-apartment.jsx` mounts the overlay in the room view and loads the
presentation through the engine's `loadAimPresentation()`. Every fixture change
it makes for a person first calls the one hand-change switch point,
`releaseFixtureForHandChange(fixtureId, { reason })`. That call does nothing on
the UI line until Den binds it (`tools/test-apartment-manual-intent-contract.mjs`).

## Elements

| Selector | Element | `data-state` |
|---|---|---|
| `[data-apt-room-overlay]` | Overlay root | — |
| `[data-apt-fixture-label="<fixture id>"]` | Fixture label (button, `tabindex=-1`; the list is the keyboard path). `aria-current="true"` when selected | `idle`, `hover`, `selected` |
| `[data-apt-fixture-ring="<fixture id>"]` | Hover or selected ring at the fixture | `hover`, `selected` |
| `[data-apt-aim-reticle]` | 44 px reticle (button), the only aim drag start | `current`, `proposed`, `invalid` |
| `[data-apt-aim-chip]` | Drag chip (`role=status`) | `valid`, `invalid` |
| `[data-apt-aim-chip-title]` | Surface, "Snaps to “…”", or the reason it can't aim | — |
| `[data-apt-aim-chip-metrics]` | "d.dd m · N° tilt" | — |
| `[data-apt-aim-chip-note]` | "the pool will be long and dim" (grazing) | — |
| `[data-apt-aim-disclosure="optical-origin"]` | "Modeled from the socket display point · optical pivot not registered" | — |
| `[data-apt-aim-disclosure="travel-limits"]` | "travel limits not commissioned" | — |
| `[data-apt-aim-proposal]` | Cyan proposal marker | `valid`, `invalid` |
| `[data-apt-aim-blocker]` | Red marker at the blocking point | — |
| `[data-apt-aim-bar]` | Room bar (`role=group`, name "Proposed aim") | `valid`, `blocked` |
| `[data-apt-aim-bar-target]` | Target name, or "Blocked" / "Out of reach" | — |
| `[data-apt-aim-action="set"]` | "Set aim" (absent when blocked) | — |
| `[data-apt-aim-action="discard"]` | "Discard" | — |
| `[data-apt-aim-pending]` | Cyan dot in the pill's Spot button; the button's name gains "(aim pending)" | — |

## ARIA names

- Reticle: "Aim handle. Drag onto a surface, or use the arrow keys. Enter keeps the proposal, Escape cancels."
- Bar: "Proposed aim"; buttons "Set aim" and "Discard".

## Keys the overlay answers

| Focus | Key | Result |
|---|---|---|
| Reticle, dragging | Escape | Cancel the drag; the proposal is unchanged |
| Reticle or bar | Escape | Discard the proposal (the host's ⌘Z restores it) |
| Reticle or bar | Enter | Set aim, only for a valid proposal |
| Reticle | Arrows / Shift+Arrows | Nudge the proposal 10 px / 40 px in screen space |

The global Escape ladder stays with the host: reticle drag, tap to aim, camera
view, the selected light's proposal (Cmd/Ctrl+Z restores it), selection, then
nothing. The host also answers Enter (Set aim) when focus is on no control, and
Cmd/Ctrl+Z and Shift+Cmd/Ctrl+Z for the selected light's 30-step aim history.

The fixture label buttons, and the Spot button while an aim waits, carry an
`aria-label`, so their names never come from hidden text.

## Truth rules the chip keeps

- Distance and tilt are measured from the registered optical origin, or from the socket display point when none is registered, which adds the optical-origin disclosure.
- "Out of reach · N° from straight down, limit L°" appears only for commissioned travel. Virtual-preview limits add the travel-limits disclosure and never a reach claim.
- "Blocked by …" needs the canonical beam trace; without one, nothing is called blocked.
- A drag off every surface says "No surface here" and proposes nothing.
