# Internal spatial renderer gate

This subtree is isolated, nonshipping test instrumentation. It is not a product
screen and must never be linked from the Apartment tab. The shipping product
keeps the existing Apartment chrome and controls; only its current viewport may
hand off at the exterior zoom boundary. This lab tests the contract around
a future outer-world renderer without importing or changing Apartment View.
Everything runs as precompiled native ESM from `app/src`; there is no runtime
transpilation. CesiumJS 1.144.0 and MapLibre GL JS 6.6.0 are pinned, vendored,
and compared against the same synthetic inputs. The deterministic DOM adapter
has no runtime dependency and remains the canvas-free contract fixture.

## Open the harness

The production-shaped path is the feature- and environment-gated Tauri window:

```powershell
$env:HOME_SPATIAL_SPIKE = "1"
cargo run --manifest-path app/src-tauri/Cargo.toml --features spatial-spike
```

The host loads `frame.html` with exactly `sandbox="allow-scripts"` and
`referrerpolicy="no-referrer"`. The native custom protocol serves only exact
frontend allowlist entries and exact files in the checked-in vendor manifest.
See `docs/HOME-SPATIAL-PHASE0.md` for the complete evidence runbook.

The frame can also run by itself. Its local controls remain useful before the
host channel connects.

## Boundaries

- `index.html` and `main.js` are the protocol-driving host harness.
- `frame.html` and `frame-main.js` are the isolated renderer frame.
- `protocol.js` defines exact, versioned host/frame message schemas.
- `fixtures.js` contains two broad synthetic US/Canada anchors and the fixed
  planet-to-interior camera journey.
- `state.js` owns latest-intent-wins navigation, reversal, reduced motion, and
  degraded-state snapshots.
- `candidate-adapter.js` defines the renderer-neutral adapter interface and the
  candidate manifest.
- `adapter-loader.js` is the only adapter selection seam.
- `deterministic-adapter.js` is the dependency-free fallback renderer used by
  the harness.
- `cesium-adapter.js` and `maplibre-adapter.js` load their pinned local ESM,
  worker, CSS, and synthetic raster through the native protocol only.
- `benchmark.js` provides corroborating in-frame lifecycle evidence; WPR/ETW
  remains authoritative for process memory and GPU accounting.
- `vendor/` contains checksummed runtime inputs, notices, an SBOM, and the
  first-party synthetic offline raster. The deterministic adapter does not
  import or execute vendor code.

The one initial `window.postMessage` transfers a dedicated `MessagePort` into
the opaque-origin sandbox. After that handshake, all traffic uses the port.
The host accepts only messages from that port, and the frame accepts the
handshake only from `parent`. Exact schemas reject unknown fields, cycles,
sensitive key names, and precise geometry in every frame-to-host message.
Coordinates are accepted only in validated `privacyClass: "synthetic"` fixture
records sent from host to frame.

## Candidate adapter interface

An adapter must expose:

- `apiVersion`
- `id`
- `mount(host)`
- `setSites(syntheticSites)`
- `render(publicStateSnapshot)`
- `getSnapshot()`
- `dispose()`

`frame-main.js` is the sole lifecycle owner. It calls `mount`, `setSites`,
`render`, and `dispose`; renderer-specific UI stays behind the adapter. The
protocol must be versioned before adding adapter-specific host messages, and
renderer objects or precise camera coordinates must never enter public
postMessage state.

The current comparison set is a separate Cesium outer-world renderer versus a
sandboxed MapLibre outer-world renderer. The deterministic DOM adapter remains
the always-available offline and contract fixture.

## Accessibility and resilience fixtures

- Home targets are available through a roving-tabindex listbox. Canvas and
  decorative globe markers are removed from the accessibility tree.
- Arrow keys, Home, End, Enter, and Space work in the site list.
- Reduced motion resolves an absolute intent at its destination immediately.
- Nominal, fully offline, provider-degraded, one-site-offline, and AI-offline
  presets remain navigable and announce their state.
- The range rail and text readouts expose the current scale and renderer owner
  without relying on color or motion.

## Tests

Run:

```text
node app/src/spatial-spike/tests/run-spatial-spike-tests.mjs
```

The suite covers fixtures, exact protocol parsing, privacy rejection, adapter
shape, deterministic journey/reversal, reduced motion, degraded states,
static sandbox attributes, CSP, vendored-runtime integrity, native routing,
and accessibility hooks. Passing it does not select a renderer; selection
requires the packaged Windows/GPU/accessibility evidence in the runbook.
