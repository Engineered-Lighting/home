# Renderer overlay bridge: agreed bounded contract

October 18, 2026. Remaining [APT-F01 / #220](https://github.com/Engineered-Lighting/home/issues/220)
work, prepared against `557164592f916cc4f93b79214f560d24d6f797b6`
on the isolated `codex/apartment-ui-overlay-bridge` branch. Repair approved the
new leaf, its controlled tests and the four narrow engine hunks below with the
lifetime, projection and resource-ownership qualifications recorded here.
The engine integration uses the UI integrator's separately agreed window. Repair also
approved a read-only `picking.getHost()` getter for the captured-host check.
This document grants no registration, command, schema or capability authority.
F05/F06 remain contract preparation only.

## Scope and immediate consumer

The immediate consumer is R01's fixture labels: a small owned dot and label
anchored to a fixture's existing canonical `display_anchor_m`, remaining aligned
during orbit and mode changes. The bridge also supplies the transient scene
group and existing presentation pick required by later reticles and handles.
It does not replace the renderer, selector, gestures, fixture inventory or
named-aim model.

The prerequisites already exist:

- `picking.projectToScreen(worldPos)` returns host-local pixels.
- `engine.displaySurfaceHit(clientX, clientY)` returns canonical point/normal
  through the registered collision proxy and current dollhouse visibility
  filter. Independent collision queries retain their different meaning.
- `overlay.overlayRoot` is already attached to the canonical apartment root.
- The existing frame loop publishes `fps`, `p95`, `renderP95`, draw calls and
  performance diagnostics. No second performance sampler is needed.

## Small interface

The leaf `app/src/home-3d/apartment-ui-overlay-bridge.mjs` exports
`createApartmentUiOverlayLayer({THREE, camera, apartmentRoot, overlayRoot, host,
projectWorld, pickSurface, isCurrent, signal, onDispose})`. Three is injected
from the existing renderer; there is no new runtime dependency or import map.
`host` is the current room viewport host. Both the owner-composed `isCurrent`
predicate and an `AbortSignal` are required. The engine's lazy
`createUiOverlayLayer` factory supplies those existing owner bindings; a
consumer predicate may narrow them but cannot replace them. Creating a layer
loads no scene asset and starts no transport, animation loop or poller.

The returned layer has these concrete operations:

| Operation | Contract |
| --- | --- |
| `project(point_m)` | Accept a finite three-number point in the existing canonical apartment frame. Use `apartmentRoot.localToWorld` exactly once for projection, then existing `projectWorld`. Reject behind-camera, near/far-clipped, nonfinite and off-viewport points even though existing picking allows overscan. Return host-local `{x, y, visible: true}`, or `null`. This is not a raw scan transform. |
| `addAnchor({point_m, label})` | Create one owned transient dot plus one owned DOM label, using `textContent` for text. The dot remains at the canonical point under the canonical overlay root; it never receives the projection's world transform. Return a handle with partial `update({point_m, label})` and idempotent `dispose()`, or `null` if refused. Updates reuse the same nodes/resources. Invalid updates withdraw that anchor and return `false`; valid later updates may restore it while the layer is current. No fixture is selected or modified. |
| `pick(clientX, clientY)` | Delegate to `engine.displaySurfaceHit` without a new raycaster, mesh fallback or filter. Preserve its point/normal. Add `surfaceName` from the hit object's nonblank native `name`, otherwise the explicit generic label `Scan surface`. Do not infer furniture names or stable semantic IDs from geometry. Return `null` for no admitted hit. |
| `group` | A new owned `THREE.Group` under the existing overlay root for transient presentation geometry. Existing fixture, collision, perception and registration groups are never reparented here. Borrowed meshes may be attached by a consumer, but their resources remain that consumer's responsibility. |
| `update()` | Called only from the existing engine frame loop. Recheck ownership and update registered anchors independently; return whether all rendered successfully. A failed projection or anchor update withdraws that anchor without stopping another layer or the renderer. No anchors means no projection work. |
| `dispose()` | Return `true` only on first disposal. Withdraw labels/dots/group, invoke `onDispose` once to unregister, and release only explicitly owned anchor geometries/materials. Detach borrowed meshes without traversing or disposing their geometry/materials/textures. Cleanup errors cannot skip other resources or unregistering. |

The bridge keeps a private DOM root with pointer events disabled by default;
it does not claim input, add listeners, set focus or alter pointer precedence.
Interactive handles require their own later, scoped interaction work. Their
existence is not part of this label-first change.

The engine factory captures its existing scene identity and host binding.
Every operation rechecks that guard and a connected, visible, positive-size
viewport. A false or throwing owner predicate retires the layer. The owner must
abort synchronously when the viewport pauses or hands off, so retirement does
not depend on another frame or interaction. An abort immediately detaches and
unregisters the layer, including when rendering is paused. The bridge never
calls `picking.setHost` or adopts a replacement host on its own.

Project/pick callback failures return `null`; owner/host/lifetime failure retires
the complete layer. Calls after retirement cannot revive it. The leaf may
return an already-retired frozen method object when construction loses its
scope. The engine factory uses `onDispose`'s retired flag before registration
and rechecks after the import and construction; its public async result is
`null` on abort/currentness/host/lifetime or module-load failure. No new registry
or wire schema is proposed.

## Exact engine integration window proposed for review

`app/src/home-3d/engine.js` receives these four integration hunks, together with
the separately approved read-only `picking.getHost()` getter. No marker, frame
governor or mode-loader behavior changes. The leaf enriches its own returned
pick with the source object name.
Line references below refer to the pinned commit.

| Existing location | Proposed bounded hunk |
| --- | --- |
| Immediately after `overlay`/`picking` construction, around lines 296–298 | Declare a private set of UI layers. Reuse the existing lifetime/disposed state. No layer, DOM node or Three geometry is created while the set is empty. |
| Existing frame path after `overlay.update(dt)` and before `renderer.render`, around lines 617–618 | Update active layers once per existing render frame, isolating failures per layer. Within a layer, read viewport/matrices once and project all anchors before writing label positions; skip unchanged position/text writes. An empty set adds no callback/event allocation or React state update. |
| Engine API immediately after `displaySurfaceHit`, around lines 658–665 | Add the lazy factory. Resolve with existing `sib()`, then recheck captured host/lifetime/identity before and after construction. Compare the supplied host with the approved read-only picking host getter. Use the constructor's retired flag before Set registration. Inject existing projection/presentation pick/root, and require owner abort on pause/handoff. A late import may not attach to a retired engine. |
| Engine terminal `dispose()`, before `overlay.dispose`, around lines 952–961 | Retire each layer independently and clear the set before shared overlay cleanup. Detach layer groups before shared resource traversal so borrowed resources are untouched. Retain the remaining disposal, asset and resource-governor behavior. |

The first R01 consumer mounts a layer against the current viewport host and
disposes it during its own cleanup. A remounted host receives a fresh layer;
the resident engine's boot-time host is not reused. That consumer will require
a separate, explicit narrow `home-apartment.jsx` window after F04 is green and
the shared edit window has been released. No such consumer edit is made here.

## Required controlled checks

- Real production Three projection under a translated/rotated canonical root,
  host offsets, orbit, resize and host replacement; no double transform.
- Stable anchor DOM/dot identity across updates, invisible/behind-camera
  withdrawal, one-anchor removal and idempotent layer disposal.
- Presentation picks preserve the existing accepted point/normal and cutaway
  refusal. Native mesh name and honest generic fallback are separately tested;
  adding metadata cannot alter independent collision results.
- Changed site/content/registration/frame identity and disposal during the lazy
  import cannot attach or revive old anchors. Existing scene resources remain
  untouched when a layer retires.
- An authored dot-plus-label proof orbits through Cloud, Photo and Mesh using
  the real renderer. An available proxy does not establish that every mode's
  display asset loaded; each mode needs its actual readiness evidence.
- Compare paired warmed baseline/candidate runs using existing frame/render
  statistics, identical orbit input, mode, viewport, DPR/quality budget and
  hardware. Repeat baseline runs to establish observed timing noise before
  judging the delta; do not invent a fixed tolerance or use screenshot time as
  frame cost. Include both an empty bridge and the small fixture-label set.

The final owner-facing check uses the accepted actual Den pack and saved
registration privately, without raw transforms, inventory regeneration or
output commands. Public fixtures and performance receipts contain authored
data only. Authored geometry, private room verification, installed behavior
and physical qualification remain separate results.

## Ordering and ownership

Repair owns engine/picking review and the integration window. UI owns the new
leaf and subsequent label consumer; QA builds the new leaf's controlled tests
independently. Den's accepted composition and all source/geometry contracts
remain intact. The leaf worker edits no existing engine, picking, UI or test
file. The integrator applies only the agreed engine hunks in its released
window, including the separately approved read-only getter. Any additional
consumer hunk requires its own agreed scope.

F01 has no supplied ticket dependency. It directly precedes S01/#226,
S03/#228, R01/#232, A01/#239, Z01/#247, Z04/#251 and U04/#261. S01 already has
implementation credit; this bridge is not a reason to rebuild it. This proposal
does not complete any downstream interaction, lighting or body task.
