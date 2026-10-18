# Apartment Home shell: Phase 1 implementation

Status: implemented and revalidated in an isolated PR worktree on 2026-10-18 UTC. This chat did not deploy. The scoped shell PR is stacked on the separately reviewable accepted frontend prerequisite; GitHub main does not yet contain that foundation or the installed gateway routes.

## Accepted sources and imported reference

The baseline is the shared local `C:\Claude\home` checkout. The owner supplied `HomeRedesign.zip`; its design documents, prototype assets and docs change fragment were imported without overwriting existing files. See `artifacts/apartment-home-shell-20261018/design-package-import-20261018.json` for the inspected ZIP hash, every imported path and per-file SHA256. The original `CLAUDE-DESIGN-HANDOFF.md` remains unchanged.

The imported inventory used GitHub main from September 30 and did not inspect the current local Victoria/perception implementation. The prototype contains authored room geometry, socket positions, camera imagery, pose and inference. It is reference material, not a replacement runtime or execution authority. Prototype scripts have not been launched.

## Local requirements override the reference

- Roll out the new default screen behind a reversible switch; preserve existing Home access, authentication, deep links, Travel Mode and every site, commissioning and command guard.
- Keep the current renderer and canonical geometry/frame bindings. Cloud, Photo and Mesh reuse existing interfaces. Lit, new picking/lighting/occlusion interfaces and physical qualification are later work.
- Preserve four mapped Victoria sockets, the existing white diffuser-cylinder fixtures with six radial zones, one downward zone and a hidden robotic spotlight, click controls, across-room drag aiming and Undo.
- Use one capability-driven UI for both homes while preserving authoritative private saves, unsaved work and site isolation. Prototype hardcoded data and localStorage saves do not replace those contracts.
- Keep presence, live video, analyzed imagery, pose and inference states separate. An overlay belongs only to its exact analyzed frame. Current/stale/unavailable metadata and source expiry follow current contracts, not the prototype's invented thresholds.
- Preserve the "Approximate room placement" label, original frame/depth association and expiry. Missing joints remain missing. Estimated body envelopes and radar targets do not become qualified physical geometry.
- Preserve the existing undeployed night transport and RGB analysis behavior. Sensor installation, live cameras, PCB tests, physical commissioning and completion of Lit mode are not Phase 1 dependencies.

Before overlapping edits or any deployment, agree source ownership and the accepted candidate baseline with the repair and Den integration chats. Distinguish deployed receipts, pending candidates and authored tests.

## Responsive reference

Imported design sizes are layout references: a 52 px top bar; desktop right column 360 px at widths >=1440, 340 px at 1180-1439, and 320 px at 900-1179 (expanded media widths 540/500/440 px). At 600-899 px, a bottom sheet uses inspector and camera panes with peek 150 px, half 50% and full detents. Below 600 px, one sheet pane is visible at a time and controls remain at least 44 px. The coordinating chat will record the Phase 1 tabs and behavior actually implemented; later inspector/media consolidation must not silently replace ongoing repair work.

## Rollback and validation

Rollback: use the reversible shell switch to return to the established Home entry without changing the current site, authoritative saved data or unsaved work. No broad checkout reset, staging or deployment is part of this import.

### Implemented behavior

- Normal startup uses the unified screen. Exact `?apartmentRedesign=1|0` wins over recognized explicit legacy layout links, then `home.apartmentRedesign.v1`, then the unified default. Old `/home2` commands and layout links remain available. The menu offers Previous Home; that screen offers Open redesigned Home. Switching preserves the mounted chat controller and its unsent draft.
- The shared shell owns navigation and layout. Apartment still owns its renderer, scene selection, saves, selection/aim callbacks and status. New UI-only props supply stable panel hosts and state callbacks; there is no new backend schema or renderer API.
- Desktop has Controls and Chat. Tablet uses a sheet with existing controls and camera side by side. Phone has Controls, Camera · JEV and Chat with peek/half/full detents. Original panels are composed through portals, rather than rewritten. The mandatory approximate-placement badge stays visible on the room even with Chat open.
- Menu actions retain existing tools. Lights/Travel Mode opens the existing drawer. Its Ask action reveals the populated Chat draft without sending it. Metrics uses the existing overlay stack for focus and Escape.
- `HomeApartmentData.callService` additionally requires `{siteId:"echo", isCurrent}` for potentially energizing operations. It reads the actual existing Travel helper with a four-second bound, requires confirmed off and rechecks the current client/household/site/connection context before dispatch. Existing state binding observes that helper even without mapped devices; pending on, unknown or deletion events refuse an older off result. There is no automatic command replay. Explicit off and unrelated media operations remain available in a current authorized scope.
- All four saved Victoria fixtures and existing virtual channels/drag/Undo remain intact. No automatic camera analysis start, renewal, physical command, commissioning change, sensor adapter or night transport change was added.

### Verification

The authored browser suite uses the real renderer, private generated test packs and four generated socket records. It blocks external connections, WebSockets, acquisition and writes. It verifies all four fixture clicks, same-fixture selection from Chat, real collision-surface drag and Undo, fixed socket positions, one persistent renderer/controller/composer, unsent draft continuity, desktop/tablet/phone geometry, keyboard tabs, Metrics Escape, slash commands and both rollback routes. Screenshots in `phase-1-evidence/` are **authored room data on the production renderer**, not the private scan or a live inference test.

Earlier focused local suites: shell 11; Travel/actual Aim/actual host 25; Apartment data 74; aiming 33; perception/video 136; equipment/state/gateway 84; chat 46; control cards 58; lights drawer 67; changed-source parsing 4. The initial browser run after integration exceeded its 15-second startup wait without a console error; the final bounded 45-second startup check passed with matching served source pins. CSS differs afterward only by one removed trailing blank line. A final focused Aim fix suppresses stale failure notices after fixture/client changes; its actual sender is covered by the 25-case suite, without changing the rendered UI.

### CI blocker repair

The original PR failed because its temporary base omitted runtime modules and its source assertions described older wrappers or pending local experiments. The prerequisite now restores the accepted ordinary Apartment dependencies from frozen sources; see `../apartment-baseline/README.md` and its per-file provenance. Current accepted primary files replace the older review preimages, excluding pending resident/rehearsal work. The five Phase 1 source postimages are coordinated with the repair owner: the three original candidate outputs plus the agreed Echo-card host/CSS fix. Source hashes are in the refreshed browser evidence.

Bootstrap and simulation guards execute extracted controller expressions and callbacks through JSX wrappers. Mutation tests prove missing priority/auth gates, history-as-online, unsafe bearer persistence, missing manual intent and invalid hook ordering still fail. Inventory checks include actual lazy-feature and reachable ESM entries while rejecting missing or orphan files. 3D contracts describe the accepted ordinary renderer, with real Three.js tests for exact points/normals, ray-range reset and presentation filtering, plus public adaptive-budget tests. Unshipped cross-world/card-focus assertions and the mistaken deployed-backend validation path are reconciled with the accepted source; no production guard is removed to satisfy an assertion.

Touched-file CI now maps the shell and Travel suites, source mutation proofs, inventory mutation proofs and picking/performance tests to their affected surfaces. Missing mapped harnesses fail instead of skipping. The workflow installs lockfile-pinned Playwright/Chromium and runs the authored real-renderer browser from the checkout with no source overlay. The former below-700px gap, desktop/tablet/phone composition, four fixtures, drag/Undo, chat draft continuity and rollback all pass that local browser run. The second cold URL reload needed the same bounded 45-second wait as initial startup; both the original timeout and passing rerun remain in local artifacts. No production source changed for that timing repair. The independent spec review then found an actual Echo card placement regression: accepted cards lack the pending marker used by the original CSS. A shell-only portal wrapper now reflows the existing card. The authored regression failed at desktop before the fix, then passed at desktop and phone sizes; classic cards retain their original DOM and placement. The final browser run has 20 checkpoints and ten screenshots, with no missing code, page errors, external or physical commands.

The first published Ubuntu run then timed out in Chromium's software-GPU screenshot compositor after font readiness. Still capture now pauses only the existing current unified engine and resumes it in `finally`; a rendered-frame counter must advance before interaction continues. Classic capture cannot restart its parked engine. The 145-second work deadline is unchanged, with a separate cleanup buffer. CI retains authored receipts and PNGs on success and failure; no production source, renderer interface or behavior assertion changed for this harness repair.

The next Ubuntu run passed capture/resume but exposed a stale expected ray: hover parallax can move the camera after the test projects a target. The harness now waits for real host/camera/pivot stability and records both the original projection and the actual pointer-up frame. A read-only capture observer records raw and visible canonical collision hits before the production gesture handler runs in that same turn. The committed target must match that input-frame hit within the unchanged 3 cm limit, with the intended fixture selected at down and up. Normal ambient motion remains enabled; no renderer or geometry change was made.

These are focused software checks. They do not turn the incomplete stock-main gateway into the accepted installed gateway, qualify optional world zoom, or establish live/physical acceptance. Consult the latest PR checks for the exact published head rather than treating a historical local receipt as current CI.

Reproduce with `node tools/run-apartment-shell-tests.js`, `node --test tools/test-apartment-travel-guard.cjs` and `node --test tools/test-apartment-home-shell-browser.mjs`. Supporting local logs and exact integration/import receipts are under `artifacts/apartment-home-shell-20261018/`.

### Review and rollback boundaries

The baseline parent now contains the separately reviewable accepted frontend restoration and its test compatibility repairs. The PR diff contains this phase, imported design references, tests, change notes and authored screenshots. The original shared branch/index and all unrelated dirty work remain untouched. Merge the prerequisite PR first, then retarget the shell PR to main and rerun checks. Stock-main gateway route reconciliation remains separate; do not mistake authored loopback handlers for that integration.

UI rollback uses Previous Home or `?apartmentRedesign=0`; it keeps the final Travel guard active. Source rollback must apply only this PR's inverse delta against matching postimages and refuse concurrent edits. It must not reset the checkout or restore over newer repair changes. No deployment or service restart was performed.

The ZIP's three camera JPEGs remain local imports. They are byte-identical to existing simulation assets; the published prototype references those existing paths instead of duplicating household imagery in this PR. The original local design handoff is preserved and excluded from this phase's publication.
