# Apartment unified design

## The room view in the prototype is a placeholder
The prototype draws its own stand-in room: authored boxes for walls and furniture, a sampled point cloud and simple fixture orbs. Don't port it. Production keeps the current Apartment renderer in `app/src/home-3d/` as it is: the streamed point cloud and its shader, the splat (posed by `frame.json`), the mesh, wall-fade, orbit and camera behavior, and asset loading. Build the new UI, features and interactions on top of it.
- Map the toolbar's Cloud, Photo and Mesh to the existing point cloud, splat and mesh modes. Lit is new: it relights the existing scan mesh, and the cloud and splat stay the unlit "how it looks" layers.
- In-room overlays render over the existing scene: fixture labels, reticle, Zones | Spot pill, Set aim bar, radial composer, named-aim handles, camera and sensor frustums, body volume.
- Every geometric query the prototype makes against its boxes runs against the real scan mesh through the existing mesh picking (used today for target dragging). That covers aim hits, occlusion, named-aim surfaces, zone facing, coverage and body shadowing.
- Where the prototype's look differs from the current renderer, the current renderer wins.

One Apartment experience across Echo Park and Victoria: the same shell, selection model, inspector, camera dock and status vocabulary. The home you pick changes the scene, the data and what's possible.

| File | What it is |
|---|---|
| [INVENTORY.md](INVENTORY.md) | What was inspected, Echo/Victoria common ground and divergence, current vs planned, and what is retained, merged or replaced |
| [DIRECTION.md](DIRECTION.md) | The recommended information architecture, visual direction and state vocabulary |
| [SPEC.md](SPEC.md) | Layout and responsive rules, selection precedence, gestures, aim lifecycle, dock coexistence, Escape/Return/Close, status semantics, keyboard and touch, persistence |
| [HANDOFF.md](HANDOFF.md) | Component map, capability variants, affected files, data available vs missing, consolidation, migration stages, acceptance examples |
| [prototype/index.html](prototype/index.html) | Interactive design prototype |
| [Viewport review.html](Viewport%20review.html) | The prototype at 1600 × 1000, 1366 × 768, 820 × 1180 and 390 × 844 |

## Running the prototype
Serve the repository root with any static server and open `docs/design/apartment-unified/prototype/index.html`. It loads three.js r149, React 18 and Babel from unpkg; fonts and media are local.

## What is real
- **From the repository:** Echo Park rooms, light, camera and named-target positions (`seed-model.json`); Echo camera stills (`app/src/simulation/cameras`); tokens and fonts; Apartment contracts and inspector conventions.
- **Loaded from your checkout when reachable:** Echo's `app/data/apartment/points.sim.ply`.
- **Authored:** furniture boxes, light states, the Front left Engineered preview, all of Victoria's Den (geometry, sockets, fixtures, named aims, camera pose), the Den frame (rendered from the room model), detector, pose, JEV values and activity labels.
- **Connected:** nothing. The prototype sends no command and starts no session.

## Not reachable in this session
The local checkout's Victoria, perception and Oct 15–17 planning sources. INVENTORY.md lists them, and HANDOFF.md says what to verify and how to swap in real material.
- `LIGHT-AND-BODY-RESEARCH.md`: light simulation and body volume research, with the recommended pipeline
