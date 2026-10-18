# Home design fidelity verification

final result: passed

The supplied visual reference is `docs/design/apartment-unified/prototype/` from
HomeRedesign.zip (SHA256 `9fcff2f8b0a73d0b0ea00ce9a984550ebec08ea9c94fe83335e63cace80375d8`).
The reference's room and data are authored examples. Verification preserves the
accepted production renderer and uses disposable four-fixture registration data.
It does not qualify camera, perception, physical output or commissioning.

## Comparison history

- Initial implementation used legacy inspector panels and a separate toolbar.
  Replaced the presentation with the supplied layout, component hierarchy and tokens.
- Phone inspector comparison at 390 x 844 exposed retained room-list scroll,
  overlapping range values and incorrectly bound switch styles. These findings
  remain blocked until the frozen correction is captured and compared again.
- A functional phone check found the boot artwork pushed the composer 3px beyond
  the viewport. The unified boot area can now shrink and scroll. The unchanged
  bounds assertion passes after that correction.
- Phone toolbar overflow was corrected by hiding its angle count at the phone
  breakpoint, as the reference does. Orbit arrows remain available.
- Closed camera chrome now uses a truthful closed-view card, with the existing
  perception component and its callbacks retained.

## Final comparison inputs

Source visual truth: `docs/design/apartment-unified/prototype/`, rendered unchanged
in the same browser as the implementation. Source and implementation were both
opened, captured and then combined in comparison HTML before judging fidelity.
The final screenshot comparisons are in
`docs/design/apartment-unified/evidence-20261018/`.

| State | Desktop 1366 x 768 | Tablet 820 x 1180 | Phone 390 x 844 |
| --- | --- | --- | --- |
| Home overview | [Pair](docs/design/apartment-unified/evidence-20261018/desktop-overview-comparison.jpg) | [Pair](docs/design/apartment-unified/evidence-20261018/tablet-overview-comparison.jpg) | [Pair](docs/design/apartment-unified/evidence-20261018/phone-overview-comparison.jpg) |
| Socket 4, Light tab | [Pair](docs/design/apartment-unified/evidence-20261018/desktop-selected-comparison.jpg) | [Pair](docs/design/apartment-unified/evidence-20261018/tablet-selected-comparison.jpg) | [Pair](docs/design/apartment-unified/evidence-20261018/phone-selected-comparison.jpg) |

Every source screenshot and corresponding implementation screenshot has the
listed native pixel dimensions and CSS viewport, devicePixelRatio 1, dark theme,
and half-sheet detent where applicable. No density resampling was applied to
the source images. Comparison HTML displays them at native size; full-page
screenshots include both images and explanatory labels. The desktop combined
image can be downscaled by the viewer, so its
[native-scale inspector crop](docs/design/apartment-unified/evidence-20261018/desktop-inspector-focus.jpg)
was also compared. Tablet and phone controls are legible in their full pairs.

Empty Chat was captured at all three viewports. Committed
[desktop](docs/design/apartment-unified/evidence-20261018/desktop-chat-comparison.jpg)
and [phone](docs/design/apartment-unified/evidence-20261018/phone-chat-comparison.jpg)
pairs compare its shell, typography, scroll area and composer. The implementation
uses the existing explicitly labeled authored simulation. Its clear-conversation
acknowledgement, connection/command-home controls and lack of prototype-only
suggestion chips are expected content differences. This is not authenticated
household chat or model-quality acceptance. The existing offline first-run
connection surface remains available and was inspected separately.

## Findings and post-fix checks

No actionable P0/P1/P2 visual findings remain in the reviewed states.

- Fonts and typography: both use the supplied Geist/Geist Mono families in the
  same browser at density 1, with loaded fonts verified before capture. Titles
  use 17px/560, compact headings 13px/560 and chat prose 13.5px/1.55. Range values
  no longer overlap labels; the short Color temp label matches the reference.
- Spacing and layout: the 52px bar, 340px desktop column, 590px tablet half
  sheet and 422px phone half sheet match. Tablet has equal inspector/camera
  panes; phone has one pane and its segmented tabs. The final native-scale
  inspector comparison confirms card padding, tabs, sliders and switches.
- Colors and tokens: surface, hairline borders, active tabs, warm switch state,
  temperature tracks and disabled controls follow the supplied tokens. Feed
  background and thin dark scrollbars now match the reference treatment.
- Assets and icons: the room remains the current production renderer, with
  canonical geometry and frame bindings unchanged. It intentionally differs
  from the prototype's authored room/orbs. UI SVG paths reuse the supplied
  prototype icons; no generated or placeholder scene replaces the real renderer.
- Copy and state: actual equipment names/levels and current capabilities are
  authoritative. The camera card says view closed until the existing component
  is opened; it does not invent no-session, live-camera or pose state. The
  command-home selector remains explicit. Missing joints, exact analyzed-frame
  association, expiry and Approximate room placement semantics are unchanged.

The initial phone scroll defect, range overlap and switch binding defects are
resolved in the post-fix phone and inspector pairs above. The former toolbar
overflow is resolved with the same phone count-hiding rule as the reference.
The closed camera card is now one continuous panel with its existing CTA.

Behavioral review also found and fixed two regressions before acceptance:

- Moving a focused selector into Setup caused captured blur to cancel a new aim
  drag. Primary canvas pointer-down now transfers focus to the existing room
  host before the unchanged native gesture starts. Strict canonical drag/Undo
  passes after both a focused Setup selector and a focused Spot slider.
- Switching turn component types reset consumed Undo and reattached private
  review iframes. One stable presenter now uses the existing TurnHeader,
  TurnBody and EventContent path in both layouts, changing only presentation
  classes. Actual React tests preserve consumed Undo, the same review iframe
  and streaming updates. A conditional-type mutation reproduces the original
  failure, showing that the regression check detects it.

## Verification and limits

- Public PR source: 18 touched harnesses plus JSX parsing pass. The authored
  browser suite passes 27 checkpoints with screenshots disabled and no source
  overlay. The slash control contract now parses required props instead of
  rejecting an extra optional presentation prop; negative callback/default
  mutations remain rejected.
- Accepted-source composition: 5 tests and 27 browser checkpoints pass; all
  134 source/helper pins remain unchanged. No physical commands, survey writes,
  equipment saves or camera/model session starts occurred.
- Checks include four actual fixture picks, six radial/down/spot independence,
  modeled batch restore, canonical pointer-up geometry within the unchanged
  3cm bound, drag/Undo, phone scroll reset, chat/controller/canvas identity,
  draft continuity, keyboard navigation, Metrics/Escape focus restoration,
  Previous Home and explicit URL rollback. Existing save/conflict/authority and
  perception handlers/props are retained byte-for-byte, with relevant data,
  Travel Mode and source contracts passing. No successful household private
  save, physical commissioning or live perception qualification is claimed.
- Browser console checks found no page errors or missing required code.
  Expected unavailable backend/metadata routes in the disposable preview are
  distinct from runtime acceptance. Historical shared-checkout failures are
  recorded in the Phase 1 evidence; this report does not claim the shared dirty
  checkout or the whole repository has a green baseline.
- Repair released the UI lane. Den was notified; its selector changes are kept
  outside this PR and reconstruct the accepted composition when applied
  separately. Shared branch/index are unchanged. No host deployment occurred.

Original frame/depth associations and permissions remain authoritative. Scene
examples, named-aim mapping, the in-room radial composer, new aim proposals,
body geometry, lighting simulation and night tracking remain the explicitly
deferred work; their scripted prototype output was not ported as real behavior.

## Follow-up polish

P3 only: Close retains a bordered button rather than the prototype ghost icon,
and Aim lacks the small target glyph. Existing send/voice and guarded command-home
controls retain their behavior. These differences do not obstruct the layout or
the reviewed workflows.

## Implementation checklist

- [x] Match the supplied shell and inspector at desktop, tablet and phone sizes.
- [x] Compare actual paired captures and readable control details.
- [x] Preserve renderer, scene bindings, existing feature access and controllers.
- [x] Fix visual and lifecycle regressions and rerun scoped checks.
- [x] Include release fragment and immediate URL/menu rollback instructions.
- [ ] Coordinate a fresh installed baseline before any separate rollout.
