# Spatial handoff lab

This subtree is an isolated Phase 0 frontend spike. It tests the contract around
a future outer-world renderer without importing or changing Apartment View.
Everything runs as precompiled native ESM from `app/src`; there is no build step
and the deterministic adapter has no runtime dependency.

## Open the harness

Run the repository's normal static development server and open
`/spatial-spike/`. The host loads `frame.html` with exactly
`sandbox="allow-scripts"` and `referrerpolicy="no-referrer"`.

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
- `vendor/` is separately owned. The fallback does not import or execute it.

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

To add a real candidate after its reviewed local runtime exists:

1. Implement the adapter beside `deterministic-adapter.js`; it must not fetch
   remote scripts, credentials, addresses, or provider configuration.
2. Register its factory in `adapter-loader.js`.
3. Change its candidate status only after its local artifacts and tests pass.
4. Keep `frame-main.js` as the sole lifecycle owner. It calls `mount`,
   `setSites`, `render`, and `dispose`; renderer-specific UI must stay behind
   the adapter.
5. Version the protocol before adding adapter-specific host messages. Do not
   put renderer objects or precise camera coordinates into postMessage state.

The current comparison set is a separate Cesium outer-world renderer versus a
sandboxed MapLibre outer-world renderer. The deterministic DOM adapter remains
the always-available offline and contract fixture.

## Accessibility and resilience fixtures

- Home targets are available as a roving-tabindex listbox and as focusable
  visual markers.
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
static sandbox attributes, CSP, and accessibility hooks.

