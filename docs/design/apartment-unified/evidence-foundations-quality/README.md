# Foundations and quality: authored shell audit evidence

Authored software evidence only. These captures use the authored test room (two synthetic packs and an authored registration from `tools/fixtures/apartment-home-shell-fixtures.mjs`). They contain no household imagery, live camera, model session, physical command or private registration. Nothing here is actual-room, installed or physical acceptance.

Source: `tools/fixtures/apartment-shell-audit.mjs`, which runs as three test files, each with its own page load and 145 s deadline: `test-apartment-shell-audit-echo-browser.mjs` (Echo surfaces), `test-apartment-shell-audit-browser.mjs` (Victoria surfaces, the Phone example and Tab order) and `test-apartment-shell-audit-behaviour-browser.mjs` (motion, status rows, unsaved-work isolation, the sheet handle). Each mounts the actual unified Home shell from this worktree in one Chromium page. It changes viewport and pointer in place (CDP touch emulation turns the pointer coarse).

## What the audit checks

The audit covers both homes (Echo Park, Victoria) in both themes, at these sizes:

| Profile | Pointer | Surfaces |
|---|---|---|
| 1366x768 | fine | room, Layers, Help, Setup, Chat; Victoria light inspector (Light and Aim tabs) |
| 390x844 | coarse | the desktop set plus Camera · JEV |
| 768x1024 | coarse | room, Chat |
| 820x1180 | coarse | room, Chat |

Each state is checked for:

- **Text (APT-F02):** every visible text run, composited over its declared backgrounds, has at least 4.5:1 contrast (3:1 for large text). No informative text uses `--hg-fg-5`, and none is smaller than 11.5 px.
- **Targets (APT-F03, Q02):** on a coarse pointer, every visible enabled target is at least 44 px. The size is measured from the area that actually receives the pointer (`elementFromPoint`), with a 44 px cross for SVG ring sectors.
- **Surfaces and type (APT-F03, D19):** these are checked in the Q01 matrix sizes listed above:
  - the column is solid `--hg-bg-1`, with a hairline on desktop and a 16 px radius and hairline as a sheet;
  - sections are hairline-separated, not cards;
  - subjects are Geist 17/560 and section heads 13/560;
  - body text is 12.5–13.5 px, and values are Geist Mono 11.5–12;
  - radii are 8 px for controls and 10 px for groups and cards that own on/off;
  - badges use sans text at 11.5 px.
- **Stage (APT-Q03):** the room stage stays dark in both themes.
- **Motion (APT-F03, Q02):**
  - buttons press to 0.97 over 100 ms;
  - switch thumbs ease over 0.16 s and disclosure chevrons over 0.15 s;
  - the loading badge spins at 0.8 s and the live dot glows at 7 px;
  - under `prefers-reduced-motion: reduce`, all of it snaps (0.01 ms).
- **Keyboard (APT-Q02):** every visible Tab stop shows the focus ring, and Tab moves header → room → inspector → dock (SPEC › Keyboard and touch).
- **Non-drag room controls and collisions (APT-Q02, S05):** at every size, zoom in/out, orbit ‹ › and Fit are visible buttons. The toolbar, zoom pair, toasts, legend and the approximate-placement badge never overlap, and they stay on the stage.
- **Status (APT-S02):** the status popover has rows for room, lights, saved work, camera and capabilities, all using the shared badge vocabulary.
  - Echo names its Home Assistant connection.
  - Victoria reads "No light entities for Den fixtures" and shows no Home Assistant state.
  - Capability rows read "Not available yet" until the APT-F05 contract exists.
- **Home isolation (APT-S02):**
  - An authored Echo draft is parked at Echo's own scene scope and restored through the real path, so the active Echo segment shows its mark.
  - Switching to Victoria toasts "Echo Park · your unsaved work here is kept".
  - The Echo segment keeps its hollow mark while Victoria is shown, and Victoria shows none of Echo's state.
- **Sheet (APT-S05):** on the phone, real touch drags on the handle move half → full → peek → half. Arrow keys and a tap reach every detent. The sheet eases over 0.22 s and sits on the bottom edge with its safe-area padding.
- **HANDOFF › Acceptance examples › Phone:**
  - the sheet has a light/camera switch;
  - the room toolbar sits above the sheet;
  - every audited phone control is at least 44 px;
  - the page has no horizontal scroll.

## Result on this branch (local run, Windows Chromium)

| | Before (main `dfd6c613` tokens) | After |
|---|---|---|
| Findings | 133 | 0 |
| States audited | | 64 |
| Text runs checked | | 3,603 |
| Coarse targets checked | | 1,134 |
| Lowest text contrast, dark / light | | 5.20:1 / 4.85:1 |

Before the fix, the main findings were:

- In light theme, the room toolbar had dark text on dark glass (about 1.1–1.3:1).
- Light muted text on selected rows and segment tracks was at 4.3–4.36:1.
- The Chat setup form and the keyboard navigator had 8–10.5 px text with faint tokens.
- The status summary, sheet handle, Layers, the source chip, sliders, checkbox rows, radial zone sectors and Chat controls were under 44 px.
- Badges were in Geist Mono with 5 px static markers.
- Disclosures had no chevron motion, and the loading spinner did not spin.

## Recorded, not gated (other lanes)

- **Setup rail (APT-U01):** on the phone, the classic Setup editor renders in the stage under the sheet, so its fields are covered and Controls is empty. The audit records this under `known`; the Setup rail lane closes it when the editor moves into the column.

- **Label placement (APT-R01, deferred polish):** after #314, the in-room fixture labels and the composer's mode pill can overlap one another or the top bar on phone and tablet stages. Each label now has a 44 px touch area (`::after`) on coarse pointers, but overlapping labels still cover each other. The audit records these under `known` rather than gating them, until label collision stacking lands.

## Captures

- `phone-390x844-victoria-dark.png`: HANDOFF "Phone" example. The sheet has a light/camera switch, the toolbar is above the sheet, and the targets are 44 px. The toolbar's second row keeps ‹ › and Fit, and the zoom pair sits at the right edge.
- `desktop-1366x768-victoria-dark-status.png`: Victoria's status rows. Lights reads "No light entities for Den fixtures", with no Home Assistant state.
- `phone-390x844-echo-light-status.png`: Echo's status rows on a phone in light theme.
- `phone-390x844-victoria-light-light.png`: light theme on a phone. The room stays dark, and the inspector's switches, sliders and buttons are touch-sized.
- `phone-390x844-echo-dark-chat.png`: the Chat pane on a phone, with readable connection text and 44 px controls.
- `desktop-1366x768-victoria-light-aim.png`: light theme on desktop. The toolbar, labels and zoom stay dark and readable on the stage.
- `tablet-768x1024-victoria-light-room.png`: tablet sheet with side-by-side panes and the 44 px handle row.
