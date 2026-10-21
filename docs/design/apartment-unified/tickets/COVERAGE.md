# Coverage

Adversarial review of the tickets against SPEC.md, HANDOFF.md, the prototype and the original brief. Every row names the ticket that owns it. If something in the design has no row here, it's missing; add a ticket.

## SPEC.md sections
| Section | Tickets |
|---|---|
| Layout and responsive rules | APT-S04, APT-S05, APT-Q01 |
| Selection precedence | APT-R01, APT-R02, APT-R04 |
| Gestures | APT-R01, APT-A01, APT-Z02 |
| Aim lifecycle | APT-A01, APT-A02, APT-A04, APT-A05, APT-A07 |
| Quick controls | APT-R03, APT-L02, APT-L03 |
| Mapping a named aim | APT-U04, APT-U05 |
| Placing cameras and sensors | APT-U06, APT-U07 |
| Spot or zones (cylinders) | APT-A03, APT-A04 |
| Radial zones in the room (composer) | APT-Z01, APT-Z02, APT-Z03, APT-Z04 |
| Lit view | APT-V01, APT-V02 |
| Body volume | APT-V03, APT-V04, APT-P05 |
| Names | APT-U02 |
| Home Assistant links | APT-U03 |
| Home chat | APT-S04, APT-C01 to APT-C05 |
| Command ladder | APT-A06 |
| Inspector and dock coexistence | APT-P01 to APT-P05 |
| Cancel, Return and Escape | APT-F04, APT-A09, APT-S03 |
| Status semantics | APT-F02, APT-F06, APT-S02, APT-P02 |
| Keyboard and touch | APT-F03, APT-A08, APT-S06, APT-Q02 |
| What persists | APT-F05 |

## Prototype components
| Component | Tickets |
|---|---|
| TopBar, status rows | APT-S02 |
| SceneToolbar | APT-S03 |
| LayersPopover, Legend, HelpModal, AboutModal | APT-S06 |
| RoomInspector, QuickControls | APT-R02, APT-R03 |
| TargetInspector | APT-R04 |
| LightInspector, ProvenanceBadges, LightTab, ChannelCard, ToneSliders | APT-L01, APT-L02, APT-L04 |
| RadialRing | APT-L03 |
| AimTab, AimRow, TargetState | APT-A05, APT-A07 |
| CommandInspector | APT-A06 |
| ShadowNote | APT-V04 |
| SetupTab, SetupRail, SetupInspector | APT-U01 |
| NamesTool, HaLinks, TargetMapper, DevicePlacer | APT-U02, APT-U03, APT-U04, APT-U06, APT-U07 |
| PerceptionDock, VictoriaDock, EchoDock, DockHeader | APT-P01 |
| FrameOverlay | APT-P02 |
| DetailSeg | APT-P03 |
| Trace | APT-P04 |
| ChatPanel, ChatCard, ChatEnt | APT-C01 to APT-C05 |
| scene.js reticle, drag chip, snapping | APT-A01 |
| scene.js `_placeAimUI` (pill, aim bar) | APT-A03, APT-A04 |
| scene.js composer, `_fadeTick`, `zoneFacing` | APT-Z01 to APT-Z04 |
| scene.js `_placeRHandles`, `_rectDrag` | APT-U05 |
| scene.js `_drawDevices`, `coverage` | APT-U06, APT-U07 |
| scene.js `_syncLit`, VPL | APT-V01, APT-V02 |
| scene.js `_bodyCaps`, `_syncBodyVolume`, `_bodyBlock` | APT-V03, APT-V04 |
| Toast with Undo | APT-F03 |
| Scenario tweaks (draft, stale, held, replay, loading, failed) | APT-F06 |

## HANDOFF.md data gaps (schema changes need approval)
| Gap | Ticket |
|---|---|
| Per-site `capabilities` | APT-F05 |
| `analyzed_at` and held/stale thresholds | APT-F06 |
| Proposal record (perception → lighting) | APT-P04 |
| Execution receipt | APT-A06 |
| Target state and geometry hash | APT-A07 |
| Quick presets for modeled fixtures | APT-R03 |
| `radial_zone1_heading_deg` | APT-Z04, APT-U08 |
| IES files and lumen curves | APT-V01 (later) |
| Body-shape record | APT-V03 (later) |
| Name and label overrides | APT-U02 |
| Presence sensor record | APT-U07 |
| Hand-placed camera pose apart from extrinsics | APT-U06 |
| Automation reference for a named aim | APT-A10 |
| Composer command rate | APT-Z03 |

## Removals and consolidation
| Removal | Ticket |
|---|---|
| Close button; old menu items need a new home | APT-S01 |
| Anchored control card, `aim light` mode, Victoria bottom-left controls, debug stats behind Details | APT-L01 |
| Lighting target, live and dynamic panels, live room controls, fixture execution panel | APT-L01, APT-A06 |
| Live-feed full-viewport overlay, Victoria's separate perception panel | APT-P01 |
| `AIM_*` color literals, forced dark theme | APT-F02 |

## Acceptance examples (HANDOFF.md)
| Example | Ticket |
|---|---|
| Home isolation | APT-F05 |
| Direct selection | APT-R01 |
| Drag aim | APT-A01 |
| Unreachable | APT-A01 |
| Escape order | APT-F04 |
| Frame honesty | APT-P02 |
| JEV abstains | APT-P03 |
| No session | APT-F06 |
| HA offline (Echo) | APT-F06 |
| Photo missing | APT-S03 |
| Map a named aim | APT-U04 |
| Phone | APT-S05 |

## Corrections made in this review
- SPEC.md › Cancel, Return and Escape and the "Escape order" example still ended in "Close Apartment". Close is gone, so step 6 now does nothing.
- Added tickets for gaps with no owner: APT-F06 (data states and ages), APT-S06 (layers, legend, help, about), APT-R04 (named aim inspector), APT-A09 (Return to Auto), APT-A10 (automation reference), APT-U08 (zone heading commissioning), APT-U09 (fixture position and measurement).
- Folded removals, capability variants, persistence rules, validity reasons, undo keys, N/P cycling, Trace, Simple/Detailed, replay, uncertainty and Follow into the tickets that own them.
- Added spec references to every ticket and wired each acceptance example to a ticket.

## Known limits
- Victoria's ticket details come from the brief, not its source. APT-F05, APT-L02, APT-P02 and APT-P03 must check the real socket map, perception contract and JEV payload first.
- The composer has no keyboard path in the room; the ring panel is the alternative (APT-Z02).
