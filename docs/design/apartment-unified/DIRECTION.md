# Apartment unified design: direction

One Apartment shell for every home. Choosing a home changes the scene, the data and the available capabilities, but not where anything lives. The room stays central. Lighting, aiming, perception and their provenance sit in one docked column rather than floating panels.

## Information architecture

```
┌ Apartment  [Prototype]  [Echo Park | Victoria]   status summary ▾          [Setup] [Close esc] ┐
├───────────────────────────────────────────────────────────────┬────────────────────────────────┤
│ legend (contextual)                                           │ INSPECTOR  (what you selected) │
│                                                               │  Room · Light · Named aim      │
│                     ROOM CANVAS                               │  Light: Light · Aim · Setup    │
│      labels · reticle · pools · beams · body layer            ├────────────────────────────────┤
│                                                     + −       │ CAMERA DOCK (what Home sees)   │
│                                                               │  frame + overlay · trace       │
│   [scene source | Cloud Photo Mesh | ‹ 3/8 › Fit | Layers ?]  │  detector · pose · JEV · Lab   │
└───────────────────────────────────────────────────────────────┴────────────────────────────────┘
```

**Top bar.** The home switcher is a two-segment control; each segment carries one dot (connected, or a hollow amber dot for unsaved work kept in that home). The status summary is plain language: "Live model · rev 42 · Home Assistant connected · Seed geometry" or "Modeled fixtures · Den camera · analyzed 4 s ago · Authored Den". It opens a popover that says what each source is and what it is connected to. Setup is a toggle. Close names the destination and shows `esc` only when Escape would actually close.

**Canvas.** The room is never covered by a panel on desktop. Fitting accounts for the column (the canvas ends at it) and for the toolbar (bottom inset). Overlays on the canvas are limited to the contextual legend (top left), a single hint bar for sub-modes (top centre: tap-to-aim, camera view), notices for loading or unavailable assets (centre, never switching homes), and the room toolbar.

**Room toolbar.** Scene source chip, then Cloud/Photo/Mesh, view detents (‹ n/8 ›), Fit, Layers and Help. These always sit in this order in this place, in both homes.

**Inspector.** One component with three subjects:
- *Room* (nothing selected): the light list (the keyboard and list equivalent of clicking in the room) and named aims.
- *Light*: header with name, room, live summary and provenance badges; tabs **Light** (everyday channels), **Aim** (where it points, proposals, named aims, command), **Setup** (identity, position, optics and limits).
- *Named aim*: what lights it, aim a light here, geometry.

**Camera dock.** Directly below the inspector, so a selected light and what Home sees are visible together. It has four states: hidden, collapsed (one line), default (frame plus trace) and expanded (the column widens to 500–540 px and the inspector compresses). The frame and its overlay are one element; the overlay can't drift onto newer video.

**Layers.** One menu for everything optional: light pools, beams (selected, all, none), named-aim labels, fixture labels, camera positions, body (observed joints, inferred completion, nominal volume, uncertainty, 2D pose on frame, Follow), and the planned shadow and occlusion layers, shown disabled and labeled "planned".

**Setup.** A left rail over the canvas (Devices and sockets, Named aims, Zones, Fixture position, Fixture links, Cameras) with its inspector in the same right column. The dock collapses while Setup is open and comes back as it was.

## Selection model
A single selection per home: a light, a named aim, or nothing. Hover is separate and never changes the inspector. Selecting an aimable light shows its **reticle** at the current aim, and that reticle is the only drag affordance for aiming. Empty space keeps orbiting. Proposals belong to a light, not to the selection, so they survive selecting something else and switching homes.

## Why this fits Home
- It reuses Home's surfaces as they are: black and near-black, white/grey hierarchy, ice for selection, Geist for interaction and Geist Mono for values. The Lighting inspector's established semantics carry over: cyan means a proposed destination, warm Kelvin is light that is on now, and dashed means simulated or off.
- It follows Home's overlay contract (one Escape stack, topmost first, focus restore) and the Escape-hint honesty rule already in `home-tokens.css`.
- It keeps the existing contracts visible instead of hiding them: revisioned model, local drafts, a loopback-only bench, and a Product Aim request that can't be fabricated.

## Visual direction

**Type.** Geist 17/560 for subjects, 13/560 for section heads, 12.5–13.5 for body, Geist Mono 11.5–12 for values and ids. No informative text below 11.5 px or in `--hg-fg-5`. Labels are sentence case; uppercase mono chrome is reserved for the Prototype tag and code.

**Surfaces.** The column is docked and solid (`--hg-bg-1`), separated by hairlines rather than cards. Cards appear only where a thing has its own on/off (channels) or needs a decision (proposal, conflict, offline). Radii are 8 px for controls, 10 px for groups and 16 px only for the phone sheet.

**Canvas.** The canvas stays a dark room in both themes, because illumination needs a dark ground to read. Everything around it follows the theme tokens. Points use Home's luminous recipe with a dollhouse cutaway: walls between the camera and the room fade, so the interior is readable from any detent.

**Light.** Light is shown where it lands: warm pools on the floor, the desk or the painting, in the light's colour temperature. Beams are restrained, with a thin axis and a faint cone only for the selected light. Proposed aims are cyan rings terminated in the surface plane. Can't-aim states are red dashed rings with the reason beside the cursor.

**Fixtures.** Victoria's fixtures are small white cylinders: six thin strip segments glow behind the diffuser when the radial channel is on, and a small disc glows at the bottom for the downward zone. The robotic spot is never drawn; only its effect and, when selected, its axis are. Echo's ceiling lights are recessed discs. The co-located Engineered profile at Front left adds a small cylinder labeled "simulated".

## State vocabulary
One shape per meaning, so colour is never the only signal.

| Shape | Token | States | Copy pattern |
|---|---|---|---|
| Filled dot | `--apt-ok` | connected, current, saved, applied, reported | Says what: "Home Assistant connected", "Analyzed 4 s ago" |
| Glowing dot | `--apt-ok` | live | Only for an actual live stream |
| Hollow ring | `--apt-proposed` | proposed, draft | "Proposed", "Proposed by perception" |
| Hollow ring (neutral) | `--hg-fg-2` | recorded, replay | "Recorded still", "Replay · Oct 16" |
| Diamond (dashed badge) | `--hg-fg-2` | authored, simulated | "Authored Den", "Engineered · simulated preview" |
| Solid diamond | `--apt-attn` | held, stale, unsaved, changed, disconnected | "Holding · 38 s", "Stale · 6 min", "Check point" |
| Bar | `--apt-blocked` | blocked, conflict, invalid | "Can't aim here", "Conflict" |
| Grey dot | `--hg-fg-4` | unavailable, no session, modeled, loading (spinner) | "No session", "Not commissioned" |

Tokens to add next to `--hg-*`: `--apt-proposed` (#45dfff dark, #0a6f86 light), `--apt-ok` (#91e6bd / #1f6e48), `--apt-attn` (#ffb45f / #8A4E13), `--apt-blocked` (#ff625f / #B33A37), `--apt-warm` (#ffddb0 / #8a5310). These replace the `AIM_*` literals.
