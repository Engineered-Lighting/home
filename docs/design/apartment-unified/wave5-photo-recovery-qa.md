# S03 Photo recovery QA

This bounded #228 change offers an explicit Photo retry and return to Cloud through the existing room-mode owner. S03 remains partial: camera pose selection, feeds and renderer loaders are outside this change. This report establishes authored controller and component evidence; it does not establish actual-room acceptance, deployed behavior or release readiness.

## Source and receipt

The final focused browser run used these exact worktree bytes, based on `557164592f916cc4f93b79214f560d24d6f797b6`:

| Source | SHA-256 |
| --- | --- |
| `app/src/home-apartment.jsx` | `488b07e3c5e367eac32899ae7acb244c99ded8119ecd68040c2a1f98e5fc89ef` |
| `app/src/home-tokens.css` | `e218c870a08599c86706de9b008291da45f9b426a0bc813ceb188a1ab99eda8a` |
| `app/src/home-overlay.js` | `526732928cc62cc072f557d981b1918ddb117fe5ff6ff962fba3921e9de16975` |
| `app/src/home-3d/modes.js` | `64d141cdb1538177ea9611506c7162cd33f46d02a9496f83636d49d0ca2567d3` |

Final browser receipt: `.tmp/apartment-photo-recovery-browser/run-1792365674038/receipt.json`. The earlier 26-test/28-checkpoint report and receipts predate the final cancellation and installed-mode presentation fixes. They remain historical evidence; this final 46-test/44-checkpoint run supersedes their source pins.

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

`node --test tools/test-apartment-photo-recovery-browser.mjs`: **44/44 checkpoints passed**, across light/dark desktop (1366 × 768) and coarse phone (390 × 844), under the unchanged 60-second test deadline. The first fixture preserves all original 28 assertions: it mounts the actual `AptUnifiedRoomToolbar`, icon, `pickMode` and request-ref declaration, actual toolbar `onMode`/lock expressions, vendored React/HomeOverlay and production CSS. Its engine Promises and registry are authored adapters. It checks:

- Original error text and native recovery controls, visible keyboard focus, viewport bounds and 44px coarse targets.
- Enter activation while pending and successful Photo publication only after resolution.
- Explicit failed retry, including native tap on coarse phones, without a silent fallback.
- Space activation of Back to Cloud and clearing the prior Photo error only through that explicit operation.
- Camera/unavailable refusal, and Cloud remaining available for a points-only placeholder.
- Old-home success/failure preserving destination loading, with no stale recovery group, toast or refit.

The second fixture adds 16 mounted assertions using the actual renderer `setMode` method, actual installed-mode predicate/subscription and actual cancellation callback with authored asset-loading Promises. Delayed canceled success keeps Photo pressed only when the renderer committed Photo; failure leaves both at Cloud. Neither path publishes retired error/loading/toast/refit/persistence effects. Delayed old success and failure both preserve a newer real renderer Mesh request and its exact Promise/loading ownership; only the newest request settles the controls and refits. Persistence is an observed adapter behind the retained subscription guard; the full installed-engine binding and real storage are not mounted.

The browser receipt records zero network requests, WebSocket attempts or page errors. It constructs no full renderer and loads no room pack, household data, camera/model session or physical command, and captures no room imagery. The full Home application and real SiteSession lifecycle are not mounted; these cases prove the extracted callback/method boundaries when existing identity inputs change. Broader authored-shell, private Den, hosted CI and review evidence belong to their separate receipts and reviews.

No test deadline, existing suite or production file was changed by this QA task. The final focused browser was closed and its serial window released to the root integrator.
