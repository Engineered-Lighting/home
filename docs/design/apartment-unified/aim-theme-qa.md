# Existing Aim inspector theme QA — APT-Q03

This slice changes inline style values in `app/src/home-apartment-aim.jsx` only.
It uses the existing theme tokens for neutral surfaces/dividers, semantic accent
washes, the inspector shadow and informative small text. Radial ON text uses the
strong foreground over its theme-derived warm gradient. Markup, state,
conditions, callbacks, mappings, controls, timing, telemetry, saves and guards
remain unchanged. No shared token definition is changed.

## Focused authored evidence

`tools/test-apartment-aim-theme-browser.mjs` mounts the complete production Aim
inspector, existing shared UI, real theme CSS and real pure aiming/data helpers.
The surrounding fixture inventory and reported states are authored. Telemetry
has an inert start/stop adapter. Mutation, save, physical dispatch and HA-client
access throw if attempted; HTTP and WebSocket requests are refused and recorded.
No room asset, household data, camera/model service or physical output is used.

The matrix covers legacy desktop dark/light (1160 × 900), unified desktop
dark/light (1160 × 900), and unified coarse-pointer phone dark/light (390 × 844).
The unified fixture uses the production shell/panel/controls/advanced-inspector
host classes and responsive CSS: a 320 px desktop column and the existing full
phone sheet. The surrounding shell navigation reserves its actual layout space
but has no Home controller. Legacy desktop retains the floating inspector.

Each scenario checks Both profile view, the Engineered spotlight, expanded
details, radial ON/OFF/unmapped presentation, Setup and blocked calibration,
the existing simulated-current aim state, ordinary-fixture adoption confirmation
without adopting, empty inventory, keyboard focus and viewport bounds. Scroll
resets between main states, and the intended content must be visible before
each screenshot. The complete radial ON control must fit its clipping viewport.

Computed browser colors are composited over ancestor fills and radial-gradient
stops; inset shadows add a conservative background candidate. Every informative
text sample must reach 4.5:1. No threshold or state was removed to pass.

`NODE_PATH=<existing Playwright runtime> node --test tools/test-apartment-aim-theme-browser.mjs`
passed **60 checks** and captured **54 authored screenshots**. Final local receipt:
`.tmp/apartment-aim-theme-browser/run-1792365565152/receipt.json`.

| Scenario | Computed text samples | Minimum contrast |
| --- | ---: | ---: |
| Dark legacy desktop | 354 | 5.839:1 |
| Light legacy desktop | 354 | 4.653:1 |
| Dark unified desktop | 490 | 5.839:1 |
| Light unified desktop | 490 | 4.521:1 |
| Dark unified phone | 490 | 5.839:1 |
| Light unified phone | 490 | 4.521:1 |

The final run records zero HTTP requests, WebSocket attempts and page errors.
Its inert producer adapters recorded no mutations, saves or physical dispatch.
No camera/model session starts. All browsers and contexts were closed. Visual
inspection of the final light desktop, light phone Radials and simulated-current
states, plus the dark phone Radials, confirmed the recorded state is visible.
The earlier legacy-only/framing receipts are superseded for acceptance.

Exact tested source SHA-256 values:

| Source | SHA-256 |
| --- | --- |
| `home-apartment-aim.jsx` | `8efae2057fc8a7d5765d1e3e94ae6518f4b582ce0fc23900ac6091abb1121011` |
| `home-apartment-aiming.js` | `cdad1bf78a85d22919dc14dd4dcb172186bb12f873d1294b765dad66fc102224` |
| `home-apartment-data.js` | `b3910f72d06534eadda34a398ae9a85defa9b4955463007a50af00031a660d3e` |
| `home-apartment-ui.jsx` | `b059a3a6db4ca677bab8a13d6989ef2197ebfac128298a1878bc52b310458e3f` |
| `home-tokens.css` | `0f44fcbeb87b89ee53a4025007f1db337823f8a50d6ad1e9ceee83fb8835fc9f` |

## Baseline sensitivity

A separate throwaway local copy of the final harness substituted only the
public baseline Aim bytes from `557164592f916cc4f93b79214f560d24d6f797b6`
without replacing any checked-in source. Its single light unified scenario
failed the unchanged 4.5:1 check: the existing informative
“live model · fixture-bottom origin” text measured **2.981:1**. Baseline diagnostic:
`.tmp/apartment-aim-theme-browser/baseline-diagnostic-1792365582929/receipt.json`.
This expected red result is distinct from the candidate receipt and is not a CI
mode, environment override or runtime authority path.

Static checks passed: the new browser test parses, `check-jsx` parses all
86 source files, and the retained pure aiming suite passes all 33 cases. An AST
comparison against `557164592f916cc4f93b79214f560d24d6f797b6` found no change
outside existing inline `style` attributes. `git diff --check` passes.

## Remaining Q03 acceptance

Independent standards and spec reviews found **0 findings** for this bounded
slice. Both verified the final receipt/source correspondence and its authored
evidence limits. The independent AST comparison retained all 131 existing style
attributes and found no change outside their values; shared CSS is unchanged.
The CI map runs the new proof for the Aim source, shared token CSS and the test
itself. Its receipt and screenshots are explicitly allowlisted as authored CI
diagnostics; no private room directory is included.

Q03 remains partial. A focused legacy inspector matrix does not establish light
theme main-state acceptance for the complete Home shell, Chat, Setup or camera
panel. Reuse their final scoped evidence where applicable. Owner-facing actual
Den verification uses the accepted composition privately; authored component
screenshots do not establish actual-room, installed or physical acceptance.
F05/F06 remain contract preparation only. No merge or deployment is performed.
