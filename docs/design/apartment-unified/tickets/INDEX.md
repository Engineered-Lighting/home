# Tickets

61 tickets in 11 epics. Work one ticket at a time, in the order below, unless a dependency is already done. COVERAGE.md maps every spec section, prototype component, data gap, removal and acceptance example to its ticket.

## How to work a ticket
1. Read the ticket, its spec section, and the prototype reference (open the URL; read the named functions).
2. Reply with a short plan: files, approach, open questions. Wait for a go-ahead on anything marked "schema gap".
3. Build it on the existing renderer and app patterns. One PR per ticket.
4. Check every "Done when" box in the PR description, with screenshots from the real app.
5. Update the Status column below in the same PR.

## Order
1. **F Foundations**: Shared pieces every later ticket uses. Nothing user-visible changes yet.
2. **S Shell**: Apartment becomes Home's main screen with one layout for both homes.
3. **R Selection and room inspector**: Clicking anything in the room selects it, with list and keyboard equivalents.
4. **L Light inspector**: One inspector for every light, with tabs Light, Aim and Setup.
5. **A Aiming**: Point a spot at a real surface by dragging, then confirm.
6. **Z Radial composer**: Set the six radial zones with one continuous gesture in the room.
7. **C Chat**: The existing Home chat in the right column, bound to the room.
8. **U Setup**: Everything about placing and naming things, kept apart from everyday controls.
9. **P Perception and camera dock**: See what Home sees and thinks, tied to what it proposes.
10. **V Lit view and body**: Simulated light with shadows and the body as an occluder.
11. **Q Quality pass**: Run at the end of each epic, not only once.

## Status
| Ticket | Title | Depends on | Status |
|---|---|---|---|
| APT-F01 | Renderer overlay bridge | - | todo |
| APT-F02 | Status vocabulary and tokens | - | todo |
| APT-F03 | UI primitives | APT-F02 | todo |
| APT-F04 | Escape and focus stack | - | todo |
| APT-F05 | Per-home capability model | - | todo |
| APT-F06 | Data states and ages | APT-F02, APT-F05 | todo |
| APT-S01 | Apartment is the default route | APT-F01 | todo |
| APT-S02 | Top bar and home switcher | APT-S01, APT-F05 | todo |
| APT-S03 | Room toolbar | APT-F01 | todo |
| APT-S04 | Right column: Controls and Chat tabs | APT-S01 | todo |
| APT-S05 | Tablet and phone layouts | APT-S04 | todo |
| APT-S06 | Layers, legend, help and about | APT-S03 | todo |
| APT-R01 | Selection model and labels | APT-F01 | todo |
| APT-R02 | Room inspector | APT-R01, APT-F03 | todo |
| APT-R03 | Quick controls | APT-R02 | todo |
| APT-R04 | Named aim inspector | APT-R01 | todo |
| APT-L01 | Inspector frame | APT-R01, APT-F03 | todo |
| APT-L02 | Cylinder channels: Spot, Down, Radial | APT-L01 | todo |
| APT-L03 | Radial ring panel | APT-L02, APT-Z04 | todo |
| APT-L04 | Echo lights | APT-L01 | todo |
| APT-A01 | Reticle drag on the scan mesh | APT-F01, APT-L01 | todo |
| APT-A02 | Aim tab opens on drag | APT-A01 | todo |
| APT-A03 | Zones | Spot pill | APT-A01, APT-Z02 | todo |
| APT-A04 | Set aim bar in the room | APT-A01 | todo |
| APT-A05 | Aim tab content | APT-A01 | todo |
| APT-A06 | Command ladder | APT-A05 | todo |
| APT-A07 | Named aim save states | APT-A05 | todo |
| APT-A08 | Tap and keyboard aiming | APT-A01 | todo |
| APT-A09 | Return to Auto | APT-L01 | todo |
| APT-A10 | Named aim as an automation reference | APT-A05, APT-U04 | todo |
| APT-Z01 | Composer drawing and abstract fixtures | APT-F01 | todo |
| APT-Z02 | Composer gesture | APT-Z01 | todo |
| APT-Z03 | Fades and command rate | APT-Z02 | todo |
| APT-Z04 | Zone facing | APT-F01 | todo |
| APT-C01 | Chat in the column | APT-S04 | todo |
| APT-C02 | Entity chips | APT-C01, APT-R01 | todo |
| APT-C03 | Selection as context | APT-C01 | todo |
| APT-C04 | Chat aim proposals | APT-C01, APT-A04 | todo |
| APT-C05 | Composer and header | APT-C01 | todo |
| APT-U01 | Setup mode and rail | APT-L01, APT-F04 | todo |
| APT-U02 | Names | APT-U01 | todo |
| APT-U03 | Home Assistant links | APT-U01 | todo |
| APT-U04 | Named aim mapper | APT-U01, APT-F01 | todo |
| APT-U05 | Named aim handles | APT-U04 | todo |
| APT-U06 | Camera placement | APT-U01 | todo |
| APT-U07 | mmWave sensors | APT-U06 | todo |
| APT-U08 | Zone heading commissioning | APT-U01, APT-Z04 | todo |
| APT-U09 | Fixture position and measurement | APT-U01 | todo |
| APT-P01 | Camera dock states | APT-S04 | todo |
| APT-P02 | Exact-frame viewer | APT-P01 | todo |
| APT-P03 | Inference and JEV | APT-P02 | todo |
| APT-P04 | Evidence to proposal link | APT-P03, APT-A05 | todo |
| APT-P05 | Room-space body layer | APT-P02 | todo |
| APT-P06 | Perception Lab handoff | APT-P03 | todo |
| APT-V01 | Lit mode on the scan mesh | APT-S03 | todo |
| APT-V02 | One-bounce light | APT-V01 | todo |
| APT-V03 | Body volume | APT-P05 | todo |
| APT-V04 | Body occlusion in aiming | APT-V03, APT-A04 | todo |
| APT-Q01 | Viewport matrix | - | todo |
| APT-Q02 | Keyboard, touch and motion | - | todo |
| APT-Q03 | Light theme | - | todo |
