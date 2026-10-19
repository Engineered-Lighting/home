# S03 Photo recovery QA

This bounded #228 change offers an explicit Photo retry and return to Cloud through the existing room-mode owner. S03 remains partial: camera pose selection, feeds and renderer loaders are outside this change. This report establishes authored controller and component evidence; it does not establish actual-room acceptance, deployed behavior or release readiness.

## Source and receipt

The final focused browser run used these exact worktree bytes, based on `557164592f916cc4f93b79214f560d24d6f797b6`:

| Source | SHA-256 |
| --- | --- |
| `app/src/home-apartment.jsx` | `488b07e3c5e367eac32899ae7acb244c99ded8119ecd68040c2a1f98e5fc89ef` |
| `app/src/home-tokens.css` | `94ba2c4e9f191ed45ecb03e106e79c7d7b74a8238355989764c3c7eac83168d8` |
| `app/src/home-overlay.js` | `526732928cc62cc072f557d981b1918ddb117fe5ff6ff962fba3921e9de16975` |
| `app/src/home-3d/modes.js` | `64d141cdb1538177ea9611506c7162cd33f46d02a9496f83636d49d0ca2567d3` |
| `app/src/home-apartment-ui.jsx` | `b059a3a6db4ca677bab8a13d6989ef2197ebfac128298a1878bc52b310458e3f` |

Final browser receipt: `.tmp/apartment-photo-recovery-browser/run-1792366518573/receipt.json`. Earlier 26-test/28-checkpoint evidence predates the cancellation and installed-mode presentation fixes; the later 46-test/44-checkpoint evidence predates the actual Toast composition correction. The preceding 48-checkpoint receipt verified native Cloud recovery; the final receipt also makes native Retry and restored bottom positioning explicit. Earlier receipts remain historical evidence; this final 46-test/48-checkpoint report supersedes them.

## Results

`node --test tools/test-apartment-photo-recovery.cjs`: **46/46 passed** on an independent QA rerun. The suite retains the original 26 tests and adds 20 cancellation/installed-mode cases. It extracts the actual `pickMode` and `cancelSpatialTravel` callbacks, installed-mode presentation predicate/subscription, and renderer `setMode` method using the vendored Babel parser, then invokes them with authored asset Promises and state setters. It covers:

- Current retry success/failure and explicit Cloud selection through one existing `setMode` call.
- Camera locks, points-only placeholders and unavailable interiors.
- Missing or unreadable registry/selection/site and revision mismatch refusing before mode, error, loading or pending-request mutation; existing legacy revision normalization still works.
- Old-home success/failure, closed/revised contexts and A → B → A under the existing lifecycle/travel identities.
- Same-engine Photo → Cloud → Photo completion races retaining the newest exact Promise and loading indicator. Only the current owner publishes mode/error/toast/refit or clears its bookkeeping.
- Synchronous retirement during admission still awaiting both successful and rejected mode Promises.
- Actual same-home cancellation synchronously clearing its owned loading/ref before coordinator cancellation settles, without erasing a later request.
- An unchanged renderer mode method finishing Photo after same-home cancellation. The actual subscription keeps the controls equal to the installed engine; retired request errors, loading, toasts, refits and view persistence stay refused. Manifest registration precedence, registry registration fallback and existing legacy scenes are covered separately.
- Installed-mode publication refusing a closed view, replaced engine, different site, owner revision/frame/registration, mismatched active manifest, absent or unreadable owner, absent manifest API, changed installed frame, old lifecycle and A -> B -> A lifecycle.

Before the correction, the existing callback failed the four delayed-home and A → B → A success/failure cases. The final callback passes those same assertions with its approved existing-identity and exact-Promise publication guard.

`node --test tools/test-apartment-photo-recovery-browser.mjs`: **48/48 checkpoints passed**, across light/dark desktop (1366 × 768) and coarse phone (390 × 844), under the unchanged 60-second test deadline. The first fixture has 32 checkpoints, preserving all original 28 and adding four actual Toast coexistence cases. It mounts the actual `AptUnifiedRoomToolbar`, icon, `pickMode`, request-ref declaration, `showToast`/`dismissToast`, `HomeApartmentUI.Toast`, actual toolbar `onMode`/lock expressions, vendored React/HomeOverlay and production CSS inside the actual `data-home-apartment-view` wrapper. Its engine Promises and registry are authored adapters. It checks:

- Original error text and native recovery controls, visible keyboard focus, viewport bounds and 44px coarse targets.
- Enter activation while pending and successful Photo publication only after resolution.
- Explicit failed retry, including native tap on coarse phones, without a silent fallback.
- Space activation of Back to Cloud and clearing the prior Photo error only through that explicit operation.
- Camera/unavailable refusal, and Cloud remaining available for a points-only placeholder.
- Old-home success/failure preserving destination loading, with no stale recovery group, toast or refit.
- The actual sticky error Toast and recovery panel remaining within the viewport without overlap, with both native pointer targets exposed. Both Back to Cloud and Retry activate by native desktop click or coarse-phone tap while that Toast is present, without a forced click or waiting for expiry. Retry visibly stays pending before resolution, then engine/UI both report Photo. The exact same Toast object and DOM element, content and sticky lifetime are retained. When recovery disappears, the Toast resumes normal bottom anchoring (computed 74px, confirmed by its measured bottom gap) and its actual native dismissal remains reachable; coarse dismissal remains 44px. The receipt records geometry, hit targets, text/font/color observations, post-Retry identity/position results and all four theme/size results.

The new composition case first failed against the previous CSS: `.tmp/apartment-photo-recovery-browser/run-1792366324909/receipt.json` records the actual Toast message intercepting the native Back to Cloud click, with overlapping bounds and a blocked pointer target. The approved position-only CSS correction moves that view's direct Toast to 12px from the top while Photo recovery is present. The final run passes with no Toast component, text, lifetime, action or controller changes. All evidence in these receipts is authored; no room imagery is included.

The second fixture adds 16 mounted assertions using the actual renderer `setMode` method, actual installed-mode predicate/subscription and actual cancellation callback with authored asset-loading Promises. Delayed canceled success keeps Photo pressed only when the renderer committed Photo; failure leaves both at Cloud. Neither path publishes retired error/loading/toast/refit/persistence effects. Delayed old success and failure both preserve a newer real renderer Mesh request and its exact Promise/loading ownership; only the newest request settles the controls and refits. Persistence is an observed adapter behind the retained subscription guard; the full installed-engine binding and real storage are not mounted.

The browser receipt records zero network requests, WebSocket attempts or page errors. It constructs no full renderer and loads no room pack, household data, camera/model session or physical command, and captures no room imagery. The full Home application and real SiteSession lifecycle are not mounted; these cases prove the extracted callback/method boundaries when existing identity inputs change. Broader authored-shell, private Den, hosted CI and review evidence belong to their separate receipts and reviews.

No test deadline, unrelated suite or production file was changed by this QA task. The final focused browser was closed and its serial window released to the root integrator.
