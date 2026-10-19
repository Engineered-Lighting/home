# Directional ambient controls

This slice adds the selected fixture's six-zone ambient composer and native
inspector ring. It retains the admitted diffuser cylinders, four fixtures and
existing spotlight/downward channels. The renderer, picking, direct-aim
algorithm, equipment schema and command transport are unchanged.

## Behavior and ownership

The Light tab selects Zones; Aim selects Spot. The in-room pill and unmodified
A/Z shortcuts use the same guarded owner. Native fields and controls keep their
typing and keyboard behavior. The pill sits above the selected fixture and
withdraws during gestures. Initial and newly admitted bindings start in Spot
and Aim together; selecting another fixture preserves the chosen tool.

The composer draws a soft polar shape at the descriptor's fixture height, using
all six admitted azimuths. Pointer inversion uses that same last committed SVG
plane. Outward motion brightens, inward motion dims, and a fast sweep fills
skipped zones. Pickup does not jump to the initial cursor radius. Only radial
power and brightness change; spotlight aim, downward output, targets and aim
history remain with their existing owners.

The existing 80 ms HUD cadence projects the shape and flushes cumulative modeled
patches. There is no additional RAF, timer, renderer subscription or hardware
stream. Ordinary camera hover can keep settling without canceling painting;
exact navigation, projection, viewport and root changes still retire it. Input
uses the frame published after the matching portal commits, not an undrawn
timer result.

Each gesture pins the fixture, mount, generation, equipment revision/digest,
complete binding, anchor and radial directions. Lifecycle changes, hidden
layers, Setup, another pointer, lost capture, Escape, blur or wheel discard
unflushed intent and release owned capture. Already applied modeled levels stay
applied. No stale restoration crosses a scene or home boundary.

The six inspector zones support native Enter/Space, individual or all-zone
brightness and temperature, power and preserved Off levels. The existing
all-zero radial On restoration remains 0.5. The inspector remains a non-drag
alternative when the projected plane is unavailable.

## Verification

- Pure geometry/gesture suite: 39 passing cases, including perspective and
  orthographic fixture-height inversion, canonical angles, pickup, sweep,
  cumulative output and exact identity retirement.
- Actual-source integration suite: 93 passing cases with authored event/hook
  adapters. These execute the actual callbacks and actual `createRig`, but do
  not mount the full Home app. Regressions cover real ambient camera settling,
  committed-frame publication, tab/tool wiring and same-ID cache retirement.
- Existing room-selection and room-inspector source suites: 122 passing cases.
- Escape stack: 49 passing cases; mounted overlay browser: 35 passing checks.
- Native full Home directional browser: all 12 stages pass on the actual
  renderer, including painting during ambient camera settling, six zone
  selectors, tabs/pill/A/Z, canceled gestures, exact layer/Setup reconciliation,
  retained tool after Setup, original spotlight Undo and home retirement.
- Full touched-surface run: all 22 mapped suites plus all 87 JSX/classic parse
  checks pass. Runtime: Edge 155.0.4283.62, Playwright 1.62.1, Node 24.14.1.
- Passing directional receipt:
  `.tmp/apartment-directional-browser/run-1792380920896/receipt.json`.
  Apartment SHA-256: `f987179cacbcc574270a357c28d466318d031b68276c9e88e315eb6370413fdd`;
  Cards: `2cfe71c98219eb46569d0eb4cbaf842d1648f2664698176c2654c16d45d6304c`;
  geometry/controller: `763526e7d2e3dbe1c0d7cc51b4814a6ca7a3b94ebd87f3bf945eb5c1d5f5cda6`.
  This final 12-stage run used the exact staged Git bytes after normalizing only
  new-file CRLF endings, and completed in 13.94 seconds.

Independent production Standards review reported no findings. Independent Spec
review found four issues in the first integration: exact camera-matrix equality
rejected ordinary settling, tabs did not select the matching tool, cached
geometry could outlive its identity, and the pill did not hide above the fixture
during painting. The corrected source cleared all four findings. Production
reviews exclude each reviewer's own authored tests.

The first native directional run passed eight stages, then correctly exposed
an overly broad test assumption: hiding the layer rehydrates the existing
modeled states and target. The revised oracle observes every modeled call and
permits exactly four full-state replays plus the one known target, from the
actual reconciliation invocation, with exact pre-click values, identity,
binding and call order. Every extra, partial or combined late gesture patch
fails. Full descriptor preservation is checked when that transaction completes.
Gesture/output checks and the 145-second deadline remain. The original failed
receipt remains in
`.tmp/apartment-directional-browser/run-1792380241349/receipt.json`.

The next native run caught an actual Setup-return bug: remounting the inspector
reprocessed a retained old aim notice and selected Aim/Spot over the user's
Light/Zones choice. The inspector now responds only to changed notices. The
unchanged native expectation and two actual-source lifecycle cases pass after
the correction. The red receipt remains in
`.tmp/apartment-directional-browser/run-1792380595274/receipt.json`.

The existing Escape adapters needed the new nullable gesture ref; the original
assertions remain, with additional radial-before-selection cancellation cases.
These adapter failures were not production Escape regressions.

## Ticket coverage and retained limits

| Ticket | Covered in this slice | Still open |
| --- | --- | --- |
| APT-Z01 / #247 | Admitted cylinders retained; fixture-height lobes, ticks, nodes and outward fade | Broader visual/responsive acceptance |
| APT-Z02 / #248 | Pickup, radial paint, interpolated sweep, guarded flush and cancellation | Broader touch/keyboard journey acceptance |
| APT-A03 / #249 | Pill, synchronized tabs, A/Z, above-fixture placement and gesture withdrawal | Pending dot requires a real proposal lifecycle |
| APT-L03 / #252 | Six native zone selectors, all-zone selection, power, brightness, warmth and Show in room | Commissioned facing and wall shortcuts depend on Z04 |
| APT-Z03 / #250 | Existing 80 ms modeled update path reused | Temporal easing and any future transport contract |
| APT-Z04 / #251 | Explicit modeled orientation disclosure | Authoritative facing/commissioning contract |

An existing direct-aim target is not a pending proposal, so no cyan pending dot
is fabricated. Numeric radial azimuths do not establish commissioned physical
facing. F05/F06 remain contract preparation only. No ticket is closed by this
bounded slice.

Public browser evidence uses generated rooms and registration on the real Home
renderer. Actual-room browser verification, imagery, coordinates and saved
registration remain private and separate. Neither evidence kind establishes
installed behavior, physical illumination, merge or deployment.
