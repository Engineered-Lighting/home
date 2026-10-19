# Room selection interaction slice — APT-R01

This slice completes Victoria N/P cycling, shared list/room selection admission,
supported inspector-tab retention and unclaimed empty-click clearing. Existing
Echo semantic navigation remains available to unpositioned devices. Canvas
double-click no longer invokes a light service; explicit controls retain the
existing command guards. Engine, picking, rig and direct-aim ownership are unchanged.

## Evidence

- `tools/test-apartment-room-selection.mjs`: 59 actual-source callback/effect
  cases pass. These use authored adapters and the real direct-aim controller;
  they do not mount the complete React application.
- `tools/test-apartment-room-selection-browser.mjs`: eight full-Home stages pass
  using authored rooms, the actual renderer and native browser input. The
  candidate component SHA-256 is
  `8bc38ad452af56886ffa738c46747b0516d1848a6b6c4596bebf07e4057ac467`.
  The receipt is retained locally under
  `.tmp/apartment-room-selection-browser/run-1792375523618/receipt.json`.
- Native input exposed a failed stationary empty click: the rig captures the
  pointer on the host, so the browser retargets pointerup/click from canvas to
  host. The regression failed before correction (58/59 focused cases); it now
  accepts only the recorded canvas-origin, stationary, unclaimed pointer with
  uncovered canvas under both release and click. Covered UI, off-canvas,
  cancellation, aim claims and orbit still refuse deselection.
- Browser stages cover unpositioned Echo N/P, Victoria list/N/P wrap and tab
  retention, actual fixture picking, camera orbit versus empty click, active-aim
  Escape cancellation, native post-aim click and exact Undo, double-click
  non-dispatch, and Echo list selection after returning home.
- All eleven mapped harnesses and JSX validation passed, after updating the
  existing semantic source-contract locator to the shared N/P predicate. Its
  unpositioned-device assertions remain. The new browser suite keeps its
  145-second deadline and all service/save/session requests blocked. Known
  tracker read subscriptions are separately recorded and blocked as well.

The initial native-input failures and test readiness failure remain in local
receipts. No failed result was replaced, deadline raised or assertion removed.
Public CI may retain only the allowlisted authored browser receipt.

## Remaining design work

R01 remains partial: interactive in-room labels, their keyboard/hover/emphasis
presentation and the full direct-selection acceptance still need their own
consumer and review. This slice does not introduce room groups, named-aim Edit,
new fixture visuals, radial painting or the aim-proposal lifecycle. Those remain
separate design tasks. F05/F06 remain contract preparation only.

This is authored software verification. Actual accepted Den visual verification
is private and separate; installed behavior and physical acceptance are not
established. No household data, merge, deployment or host operation is included.
