# First-wave UI foundations verification

This change builds on unmerged PR #219 at
`b0230dc15034942584836877981d22a6c94da17b`. It adds shared presentation and
migrates the existing Aim status rows and Apartment notice presenter. It does
not change renderer, perception, registration, command or session contracts.

## Behavior and boundaries

- `HomeApartmentUI.Badge` uses a common state vocabulary, explicit text and
  decorative shapes. Callers supply the state; the component does not compute
  age, expiry, availability or permission. Calibration words retain their exact
  source text with a neutral evidence badge. Accepted is never relabeled Reported.
- `HomeApartmentUI.Toast` supports a timed action, dismissal and sticky notices.
  An action is consumed before its callback, including reentry or failure. Missing
  or failing currentness guards refuse the callback. Expiry, replacement and
  unmount cancel retained handles. Focus and pointer hover pause the remaining
  time. Old dismissal callbacks cannot clear a newer notice.
- Existing `showToast(text, opts)` calls retain the 2800 ms default, sticky and
  error behavior. Optional actions require `opts.action` and `opts.isCurrent`.
  The module delegates to the caller's existing guarded operation; this wave
  adds no new command or gesture Undo operation.
- Existing native buttons, segments, sliders and switches remain the controls.
  Both eager and deferred loaders load the shared module before its consumers.

## Verification on October 18, 2026

| Check | Result |
| --- | --- |
| `node tools/run-apartment-ui-tests.js` | 11 passing deterministic lifecycle, guard and source-adapter tests |
| `node --test tools/test-apartment-ui-browser.mjs` | Pass using vendored React and the actual shared module, CSS, Aim status helpers and extracted mounted toast callbacks |
| Badge text contrast | 768 samples: 24 states, four Home surfaces, global and shell consumers, two themes, desktop and coarse-pointer phone |
| Minimum contrast | Dark 5.596:1; light 4.521:1; informative badge text at least 11.5 px |
| Browser interaction | Native Enter activates once; Space dismisses; visible keyboard focus; coarse-pointer toast buttons at least 44 × 44 px |
| State and lifecycle | Semantic colors agree between global and shell consumers; authored badges remain dashed; stale/missing guards refuse; real timer expiry and replacement cancel actions; focus pauses expiry |
| Focused component isolation | Zero HTTP requests, WebSocket sessions and browser errors; no Home initialization or room data |
| `node tools/run-apartment-aiming-tests.js` | 33 passing checks |
| `node tools/run-apartment-3d-contract-tests.js` | 174 passing contracts |
| Full authored shell browser | Pass, 28 checkpoints; room screenshots disabled; zero forbidden requests or browser errors |

The full shell check used `HOME_APARTMENT_BROWSER_SCREENSHOTS=0` and this
worktree's sources, without a candidate overlay. It exercised the existing
authored-room fixture only. The final component repeat covered the subsequent
CSS specificity correction for semantic colors and authored badge borders.

## Public authored component images

These images show generated status and notice fixtures only. They contain no
room, household image, coordinates, registration, camera frame or service data.

| Viewport | Dark | Light |
| --- | --- | --- |
| Desktop, 1160 × 900 CSS viewport | [Component gallery](evidence-wave1-foundations/dark-desktop-authored-components.png) | [Component gallery](evidence-wave1-foundations/light-desktop-authored-components.png) |
| Coarse-pointer phone, 390 × 844 CSS viewport | [Component gallery](evidence-wave1-foundations/dark-phone-coarse-authored-components.png) | [Component gallery](evidence-wave1-foundations/light-phone-coarse-authored-components.png) |

The screenshots are full-page captures; each includes an authored-fixture label
and a focused notice action. The fixture's example Live badge demonstrates the
component appearance and asserts no actual stream.

## Provenance and remaining acceptance

The final focused receipt records these SHA-256 values:

| Source | SHA-256 |
| --- | --- |
| `home-apartment-ui.jsx` | `b059a3a6db4ca677bab8a13d6989ef2197ebfac128298a1878bc52b310458e3f` |
| `home-apartment.jsx` | `ad65206ea1eb42b7750cc0eb8dbebcecc6467f31f03c126613c2017fcfd90169` |
| `home-tokens.css` | `c64314014256581fa0818fffabfd783e576d047db8cf5b608dfaf8875bc1d930` |
| `index.html` | `099d1c3800ac1d8642fb8cc763a97ad8662b7452dc4209a87e8c160d4fff98a2` |
| `home-feature-loader.js` | `083829489ea8fc80db317f18930ce8211345cf46ae836b2fffef20e8152516ab` |

Local receipts are under `.tmp/apartment-ui-browser/run-1792353253298/` and
`.tmp/apartment-home-shell-browser/run-1792353187509/`; temporary output is not
part of the PR. The public images above come from the final focused run.

The component and full-shell results above are authored software verification.
A separate private read-only smoke check composed the narrow unread/foundation
patches onto the accepted Den source, preserving its selector and canonical
asset/registration bindings. It rendered the four saved fixtures at 1920x1080,
1366x768, 768x1024 and 390x844, retained selection and Chat/Controls navigation,
and recorded no page errors, physical output, equipment saves or session starts.
Private images and operational receipt contents are excluded from this PR.
Full owner visual acceptance, installed-source verification, merge, deployment
and physical qualification remain separate. Wider legacy chrome still contains independent
status styling and informative `--hg-fg-5` uses; #221 remains partial outside the
shared component and approved Aim/card migration. This wave does not close
global primitive accessibility acceptance for unrelated legacy controls.
