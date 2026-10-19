/* home-3d/rig.js — the constrained camera rig.
 *
 * Quantized navigation per the plan: 8 azimuth stops (45°), 2 elevation stops
 * (35°/60°), 5 zoom detents (overview, three room steps, close), rubber-band
 * drag previews with snap-back, and the engineered.lighting micro-pivot
 * (double-lerp drift, dt-corrected). No free-fly, ever.
 *
 * Inside the overview, a wheel zooms toward the point under the cursor: the
 * orbit centre moves along the pick ray exactly as far as keeps that point
 * still on screen. Detent 0 always orbits the fitted centre, so the world pose
 * derived from it (and the outward handoff) never depends on where the user
 * looked inside.
 *
 * Works in three.js world space (Y-up; the apartmentRoot converts the Z-up data).
 */
import * as THREE from 'three';

const AZ_STOPS = 8;
const ELEVATIONS = [35, 60];          // degrees
/* × fitDistance. Detent 0 is unchanged; the old 0.55/0.3 pair became four
 * roughly geometric steps so each notch is a similar perceived zoom. */
export const ZOOM_FACTORS = Object.freeze([1.0, 0.72, 0.52, 0.38, 0.28]);
const HOME = { az: 1, el: 0, zoom: 0 };
/* Pull-back ceiling for the reversible pre-zoom band, as a fraction of the
 * overview radius. At 0.25 the Apartment reads as clearly receding (apparent
 * scale 0.8) while staying well inside the camera's 200 m far plane. */
const MAX_OVERSHOOT = 0.25;
const WORLD_UP = new THREE.Vector3(0, 1, 0);

const MAX_PREVIEW = THREE.MathUtils.degToRad(20);
const DRAG_RESIST = 0.35;
const COMMIT_PX = 24;
const ELEV_PX = 36;
const PIVOT_MAX = 0.010;              // rad — EL feel, slightly amplified
const TAU_PIVOT = 0.55;               // s (EL 0.003/frame ≈ 5.5 s is too dreamy for a dashboard at first paint; 10× faster, still soft)
const TAU_DRIFT = 1.1;
/* Pan (Shift-drag or right-drag). Inside the Apartment the orbit centre
 * follows the cursor across the floor plane. At the overview detent the
 * centre must stay fitted (see the header), so there the same gesture is a
 * resisted preview that springs back on release, like the orbit preview. */
const PAN_PREVIEW_MAX = 0.12;         // x fitDistance, overview only
/* Pinch: each factor of 1.3 in finger spread steps one zoom detent, so a
 * pinch walks the same five detents as the wheel and the zoom buttons. */
export const PINCH_STEP = 1.3;

const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function boxCorners(box) {
    const min = box.min, max = box.max;
    return [
        new THREE.Vector3(min.x, min.y, min.z),
        new THREE.Vector3(min.x, min.y, max.z),
        new THREE.Vector3(min.x, max.y, min.z),
        new THREE.Vector3(min.x, max.y, max.z),
        new THREE.Vector3(max.x, min.y, min.z),
        new THREE.Vector3(max.x, min.y, max.z),
        new THREE.Vector3(max.x, max.y, min.z),
        new THREE.Vector3(max.x, max.y, max.z),
    ];
}

function fitViewport(aspect) {
    // Portrait mobile has a very narrow horizontal FOV and persistent top/bottom
    // HUD. Keep the whole apartment inside the usable center band instead of
    // reusing the desktop's tight dollhouse crop.
    if (aspect < 0.55) return { safeX: 0.82, safeY: 0.58, extra: 1.04 };
    if (aspect < 0.78) return { safeX: 0.86, safeY: 0.64, extra: 1.02 };
    return { safeX: 0.94, safeY: 0.84, extra: 1.0 };
}

function projectedFitDistance(box, target, az, el, fovV, aspect, safeX, safeY) {
    const dir = new THREE.Vector3(
        Math.cos(el) * Math.sin(az),
        Math.sin(el),
        Math.cos(el) * Math.cos(az),
    ).normalize();
    const forward = dir.clone().multiplyScalar(-1);
    let right = forward.clone().cross(WORLD_UP);
    if (right.lengthSq() < 1e-6) right = new THREE.Vector3(1, 0, 0);
    right.normalize();
    const up = right.clone().cross(forward).normalize();
    const tanV = Math.tan(fovV / 2) * safeY;
    const tanH = Math.tan(fovV / 2) * aspect * safeX;
    let required = 0;
    for (const corner of boxCorners(box)) {
        const rel = corner.clone().sub(target);
        const along = rel.dot(forward);
        required = Math.max(
            required,
            Math.abs(rel.dot(right)) / Math.max(1e-6, tanH) - along,
            Math.abs(rel.dot(up)) / Math.max(1e-6, tanV) - along,
        );
    }
    return Math.max(1, required);
}

export function createRig(camera) {
    const state = {
        az: HOME.az, el: HOME.el, zoom: HOME.zoom,
        target: new THREE.Vector3(0, 0.9, 0),
        fitDistance: 18,
        previewAz: 0, previewEl: 0,
        pivot: { tx: 0, ty: 0, cx: 0, cy: 0, dx: 0, dy: 0 },
        locked: false,        // true while snapped to a real camera (P4)
        /* Continuous pull-back past the outermost detent, as a multiplier on the
         * detent radius. It deliberately does NOT live in cur.radius: goTo()
         * overwrites that from zoomRadius(), and update() rewrites it every frame
         * during a tween, so an overshoot stored there is destroyed by any
         * rotation. The eight azimuth dots stay live during the pre-zoom band, so
         * "drag to look around while pulled back" must not silently inflate the
         * Apartment back to full size. */
        overshoot: 0,
        /* True once the owner has orbited, tilted, zoomed or panned since the
         * last fit. Layout changes (window resize, sheet, dock, toolbar inset)
         * then keep the owner's pose instead of refitting (SPEC Layout >
         * Fitting). An explicit fit or snapHome clears it. */
        ownerPose: false,
    };

    const cur = { az: azRad(HOME.az), el: elRad(HOME.el), radius: 18 };
    let tween = null;
    let poseTween = null;   // absolute-pose flight (camera snap)
    let heldPose = null;    // held camera pose after arrival
    let editReturn = null;  // detented pose restored when Apartment editing ends
    let pan = null;         // { from } while a pan drag is live
    const BASE_FOV = camera.fov;
    /* Where detent 0 orbits (fitBounds() sets it), the world-space box the
     * zoom focus may not leave, and the cursor of the wheel event being
     * handled right now (normalized device coordinates, or null). */
    const homeTarget = state.target.clone();
    let targetBounds = null;
    let zoomFocus = null;
    const raycaster = new THREE.Raycaster();

    function applyCustomProjection(matrix) {
        if (!matrix) return false;
        camera.projectionMatrix.copy(matrix);
        camera.projectionMatrixInverse.copy(matrix).invert();
        return true;
    }

    function azRad(i) { return (i / AZ_STOPS) * Math.PI * 2; }
    function elRad(i) { return THREE.MathUtils.degToRad(ELEVATIONS[i]); }
    function zoomRadius(i) { return state.fitDistance * ZOOM_FACTORS[i]; }

    function shortestAngle(from, to) {
        let d = (to - from) % (Math.PI * 2);
        if (d > Math.PI) d -= Math.PI * 2;
        if (d < -Math.PI) d += Math.PI * 2;
        return d;
    }

    /* prefers-reduced-motion: camera flights are full-viewport vestibular
     * triggers CSS can't reach (WebGL) — collapse every tween to a jump cut
     * (1ms keeps the tween-completion bookkeeping intact) and disable the
     * ambient hover micro-pivot. Same pattern as home-3d/markers.js. */
    function reducedMotion() {
        try {
            return !!(window.matchMedia
                && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
        } catch (_) { return false; }
    }

    function startTween(toAz, toEl, toRadius, dur = 450, toTarget = state.target) {
        if (reducedMotion()) dur = 1;
        tween = {
            t: 0, dur,
            fromAz: cur.az, dAz: shortestAngle(cur.az, toAz),
            fromEl: cur.el, dEl: toEl - cur.el,
            fromR: cur.radius, dR: toRadius - cur.radius,
            /* The centre moves on the same eased clock as the radius. That is
             * what keeps a cursor-focused point still for the WHOLE tween, not
             * just at its ends: eye = focus + (1 - k*s) * (eye0 - focus). */
            fromTarget: state.target.clone(), toTarget: toTarget.clone(),
        };
    }

    /* Land an in-flight tween now. A pan starts from what the user sees, and
     * update() would otherwise keep lerping the centre underneath the drag. */
    function finishTween() {
        if (!tween) return;
        cur.az = ((tween.fromAz + tween.dAz) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
        cur.el = tween.fromEl + tween.dEl;
        cur.radius = tween.fromR + tween.dR;
        state.target.copy(tween.toTarget);
        tween = null;
    }

    /* The world point under a cursor, for zoom-to-cursor.
     *
     * The pick ray meets the horizontal plane through the orbit centre, which
     * is where the eye reads "the room" at every elevation stop; a ray that
     * never descends to it (a cursor above the horizon) falls back to the
     * plane facing the camera. Clamped to the fitted bounds, so a cursor over
     * empty space zooms toward the nearest part of the Apartment instead of
     * walking the orbit centre out of it. */
    function zoomFocusPoint(ndc) {
        if (!ndc) return null;
        camera.updateMatrixWorld(true);
        raycaster.setFromCamera(new THREE.Vector2(ndc.x, ndc.y), camera);
        const hit = new THREE.Vector3();
        const floor = new THREE.Plane(WORLD_UP.clone(), -state.target.y);
        if (!raycaster.ray.intersectPlane(floor, hit)) {
            const facing = new THREE.Plane().setFromNormalAndCoplanarPoint(
                camera.getWorldDirection(new THREE.Vector3()),
                state.target,
            );
            if (!raycaster.ray.intersectPlane(facing, hit)) return null;
        }
        if (targetBounds) hit.clamp(targetBounds.min, targetBounds.max);
        return hit;
    }

    /* The orbit centre for a detent change.
     *
     * Detent 0 is the fitted centre, exactly. Inward with a focus, the centre
     * moves toward it by 1 - r'/r, measured from the LIVE pose, which keeps
     * that point fixed under the cursor even when a notch lands mid-tween.
     * Outward, the offset from home shrinks in proportion to (1 - factor), the
     * exact inverse of zooming in on one point, so it reaches zero at detent
     * 0 by construction. Anything else keeps the pending goal: a rotation
     * during a zoom must not freeze the centre wherever the tween had got to. */
    function detentTarget(fromZoom, toZoom, goal, focus) {
        if (toZoom === 0) return homeTarget.clone();
        if (toZoom > fromZoom && focus) {
            const share = THREE.MathUtils.clamp(
                1 - zoomRadius(toZoom) / Math.max(1e-6, cur.radius), 0, 1);
            const next = state.target.clone().lerp(focus, share);
            return targetBounds ? next.clamp(targetBounds.min, targetBounds.max) : next;
        }
        if (toZoom < fromZoom) {
            const fromWeight = 1 - ZOOM_FACTORS[fromZoom];
            const scale = fromWeight > 1e-9 ? (1 - ZOOM_FACTORS[toZoom]) / fromWeight : 0;
            return homeTarget.clone().addScaledVector(goal.clone().sub(homeTarget), scale);
        }
        return goal.clone();
    }

    function quadraticBezier(out, a, b, c, t) {
        const u = 1 - t;
        return out.copy(a).multiplyScalar(u * u)
            .addScaledVector(b, 2 * u * t)
            .addScaledVector(c, t * t);
    }

    function cameraSwoopMidpoint(from, to) {
        const mid = from.clone().lerp(to, 0.5);
        const distance = from.distanceTo(to);
        if (!(distance > 0.001)) return mid;
        const lift = THREE.MathUtils.clamp(distance * 0.18, 0.45, 3.1);
        const lateral = new THREE.Vector3().subVectors(to, from).cross(WORLD_UP);
        if (lateral.lengthSq() > 1e-6) {
            lateral.normalize().multiplyScalar(THREE.MathUtils.clamp(distance * 0.045, 0, 0.85));
            mid.add(lateral);
        }
        return mid.addScaledVector(WORLD_UP, lift);
    }

    function applyOrbitPose() {
        const p = state.pivot;
        const az = cur.az + state.previewAz;
        const el = THREE.MathUtils.clamp(cur.el + state.previewEl, 0.05, Math.PI / 2 - 0.02);
        const r = cur.radius * (1 + state.overshoot);
        const t = state.target;
        camera.position.set(
            t.x + r * Math.cos(el) * Math.sin(az),
            t.y + r * Math.sin(el),
            t.z + r * Math.cos(el) * Math.cos(az),
        );
        camera.lookAt(t);
        camera.rotateY(p.dx);
        camera.rotateX(p.dy);
        camera.updateMatrixWorld(true);
    }

    function clearAbsolutePose() {
        poseTween = null;
        heldPose = null;
        state.locked = false;
        camera.fov = BASE_FOV;
        camera.updateProjectionMatrix();
    }

    /* `target` restores a centre saved from an earlier pose (edit mode's
     * return). Detent 0 still lands on the fitted centre, and the saved centre
     * is clamped to the current bounds in case the Apartment was refitted. */
    function goTo({ az = state.az, el = state.el, zoom = state.zoom, dur = 450, focus = null, target = null }) {
        const fromZoom = state.zoom;
        const goalTarget = tween ? tween.toTarget : state.target;
        state.az = ((az % AZ_STOPS) + AZ_STOPS) % AZ_STOPS;
        state.el = THREE.MathUtils.clamp(el, 0, ELEVATIONS.length - 1);
        state.zoom = THREE.MathUtils.clamp(zoom, 0, ZOOM_FACTORS.length - 1);
        const toAz = azRad(state.az);
        const toEl = elRad(state.el);
        const toRadius = zoomRadius(state.zoom);
        let toTarget;
        if (target && state.zoom > 0) {
            toTarget = target.clone();
            if (targetBounds) toTarget.clamp(targetBounds.min, targetBounds.max);
        } else {
            toTarget = detentTarget(fromZoom, state.zoom, goalTarget, focus);
        }
        if (dur <= 0) {
            tween = null;
            cur.az = toAz;
            cur.el = toEl;
            cur.radius = toRadius;
            state.target.copy(toTarget);
            applyOrbitPose();
        } else {
            startTween(toAz, toEl, toRadius, dur, toTarget);
        }
    }

    const rig = {
        state,
        goTo,
        stepAzimuth(dir, dur = 450) { if (!state.locked) { state.ownerPose = true; goTo({ az: state.az + dir, dur }); } },
        stepElevation(dir, dur = 450) {
            if (state.locked) return;
            if (THREE.MathUtils.clamp(state.el + dir, 0, ELEVATIONS.length - 1) !== state.el) state.ownerPose = true;
            goTo({ el: state.el + dir, dur });
        },
        stepZoom(dir, dur = 350) {
            if (state.locked) return { accepted: false, reason: 'locked', zoom: state.zoom };
            const next = THREE.MathUtils.clamp(state.zoom + dir, 0, ZOOM_FACTORS.length - 1);
            if (next === state.zoom) {
                return {
                    accepted: false,
                    reason: dir < 0 ? 'world-boundary' : 'interior-boundary',
                    zoom: state.zoom,
                };
            }
            // Only a wheel supplies a cursor; buttons and keys zoom on the centre.
            const focus = next > state.zoom ? zoomFocusPoint(zoomFocus) : null;
            state.ownerPose = true;
            goTo({ zoom: next, dur, focus });
            return { accepted: true, zoom: state.zoom };
        },
        /* The cursor of the wheel event currently being dispatched, as
         * normalized device coordinates, or null. attachInput() sets it around
         * the synchronous zoom call and clears it straight after, so a later
         * button or keyboard step can never inherit a stale cursor. */
        setZoomFocus(ndc) {
            const x = Number(ndc?.x);
            const y = Number(ndc?.y);
            zoomFocus = Number.isFinite(x) && Number.isFinite(y)
                ? { x: THREE.MathUtils.clamp(x, -1, 1), y: THREE.MathUtils.clamp(y, -1, 1) }
                : null;
        },
        detentCount() { return ZOOM_FACTORS.length; },
        detentRadius(i) {
            return zoomRadius(THREE.MathUtils.clamp(Math.trunc(Number(i) || 0), 0, ZOOM_FACTORS.length - 1));
        },
        homeTarget() { return homeTarget.clone(); },
        snapHome(dur = 500) { if (!state.locked) { state.ownerPose = false; goTo({ ...HOME, dur }); } },
        ownerPose() { return state.ownerPose === true; },

        /* Fit the apartment bounds (world space). Desktop keeps the established
         * tight dollhouse crop; portrait mobile uses projected box corners so
         * the whole apartment lands inside the usable HUD-safe viewport.
         *
         * `frame` is the visible part of the canvas ({ fovV radians, aspect })
         * when the engine has a view offset (toolbar inset); without it the
         * camera's own frustum is the frame, exactly as before. `keepPose`
         * refreshes the fit (distance, fitted centre, bounds) without moving
         * the camera: the owner has orbited or zoomed, so a layout change must
         * not throw the view back to the default zoom and centre. */
        fitBounds(box3, { dur = 700, frame = null, keepPose = false } = {}) {
            const sphere = box3.getBoundingSphere(new THREE.Sphere());
            const aspect = frame && Number.isFinite(frame.aspect) && frame.aspect > 0
                ? frame.aspect : (camera.aspect || 1);
            const fovV = frame && Number.isFinite(frame.fovV) && frame.fovV > 0
                ? frame.fovV : THREE.MathUtils.degToRad(camera.fov);
            const fovH = 2 * Math.atan(Math.tan(fovV / 2) * aspect);
            const fitted = sphere.center.clone();
            fitted.y = Math.min(Math.max(0.9, sphere.center.y * 0.6), sphere.center.y);
            if (!keepPose) state.target.copy(fitted);
            homeTarget.copy(fitted);
            targetBounds = box3.clone();
            const viewport = fitViewport(aspect);
            const sphereFit = (sphere.radius * 0.85) / Math.sin(Math.min(fovV, fovH) / 2);
            const viewFit = projectedFitDistance(
                box3,
                fitted,
                azRad(state.az),
                elRad(state.el),
                fovV,
                aspect,
                viewport.safeX,
                viewport.safeY,
            );
            state.fitDistance = Math.max(sphereFit, viewFit) * viewport.extra;
            if (keepPose) return;
            state.ownerPose = false;
            goTo({ zoom: HOME.zoom, dur });
        },

        /* pointer-driven previews (rubber band) ------------------------------ */
        dragPreview(dxPx, dyPx, elWidth) {
            if (state.locked) return;
            const full = (dxPx / elWidth) * Math.PI; // full width ≈ 180°
            state.previewAz = THREE.MathUtils.clamp(full * DRAG_RESIST, -MAX_PREVIEW, MAX_PREVIEW);
            state.previewEl = THREE.MathUtils.clamp((-dyPx / 600) * DRAG_RESIST, -0.12, 0.12);
        },
        dragRelease(dxPx, dyPx, vxPxPerMs) {
            const capturedPreviewAz = state.previewAz;
            const capturedPreviewEl = state.previewEl;
            state.previewAz = 0;
            state.previewEl = 0;
            if (state.locked) return;
            // Fold the on-screen preview offset into the rig's current pose so
            // the settle tween starts FROM what the user sees — zeroing the
            // preview alone would jump the camera backward by the whole offset
            // in one frame before the tween begins.
            cur.az += capturedPreviewAz;
            cur.el = THREE.MathUtils.clamp(cur.el + capturedPreviewEl, 0.05, Math.PI / 2 - 0.02);
            if (Math.abs(dyPx) > Math.abs(dxPx) && Math.abs(dyPx) > ELEV_PX) {
                rig.stepElevation(dyPx < 0 ? 1 : -1);
                return;
            }
            if (Math.abs(dxPx) > COMMIT_PX || Math.abs(vxPxPerMs) > 0.6) {
                const extra = Math.abs(vxPxPerMs) > 1.4 ? 2 : 1;
                rig.stepAzimuth(dxPx < 0 ? extra : -extra);
            } else {
                goTo({ dur: 300 }); // snap back
            }
        },
        /* A second finger turns an orbit drag into a pinch: drop the preview
         * without committing a step. */
        dragCancel() {
            const hadPreview = state.previewAz !== 0 || state.previewEl !== 0;
            state.previewAz = 0;
            state.previewEl = 0;
            if (hadPreview && !state.locked) applyOrbitPose();
        },

        /* Pan: the orbit centre moves across the floor plane so the room
         * follows the cursor. Screen x maps to the camera's horizontal right
         * vector and screen y to its horizontal forward vector scaled by
         * 1/sin(elevation), which is what one pixel covers on the floor. One
         * pixel spans the same angle with or without the engine's view offset,
         * so the canvas height is the right scale either way. */
        panStart() {
            if (state.locked) return false;
            finishTween();
            pan = { from: state.target.clone() };
            return true;
        },
        panMove(dxPx, dyPx, viewportHeightPx) {
            if (!pan || state.locked) return;
            const r = cur.radius * (1 + state.overshoot);
            const perPx = (2 * r * Math.tan(THREE.MathUtils.degToRad(BASE_FOV) / 2))
                / Math.max(1, Number(viewportHeightPx) || 800);
            const az = cur.az + state.previewAz;
            const el = THREE.MathUtils.clamp(cur.el + state.previewEl, 0.05, Math.PI / 2 - 0.02);
            const right = new THREE.Vector3(Math.cos(az), 0, -Math.sin(az));
            const forward = new THREE.Vector3(-Math.sin(az), 0, -Math.cos(az));
            const delta = new THREE.Vector3()
                .addScaledVector(right, -(Number(dxPx) || 0) * perPx)
                .addScaledVector(forward, ((Number(dyPx) || 0) * perPx) / Math.max(0.2, Math.sin(el)));
            if (state.zoom === 0) {
                delta.multiplyScalar(DRAG_RESIST);
                const max = PAN_PREVIEW_MAX * state.fitDistance;
                if (delta.length() > max) delta.setLength(max);
            }
            const next = pan.from.clone().add(delta);
            if (state.zoom > 0 && targetBounds) {
                next.x = THREE.MathUtils.clamp(next.x, targetBounds.min.x, targetBounds.max.x);
                next.z = THREE.MathUtils.clamp(next.z, targetBounds.min.z, targetBounds.max.z);
            }
            state.target.copy(next);
            applyOrbitPose();
        },
        panEnd() {
            if (!pan) return;
            const moved = state.target.distanceToSquared(pan.from) > 1e-10;
            pan = null;
            if (state.locked) return;
            if (state.zoom === 0) goTo({ dur: 300 }); // the overview springs back to the fitted centre
            else if (moved) state.ownerPose = true;
        },
        isPanning() { return !!pan; },
        hoverPivot(nx, ny) { // normalized -1..1
            if (reducedMotion()) { nx = 0; ny = 0; } // no ambient parallax
            state.pivot.tx = nx * PIVOT_MAX;
            state.pivot.ty = ny * PIVOT_MAX;
        },

        /* per-frame ----------------------------------------------------------- */
        update(dt) {
            // absolute-pose flight takes priority over the spherical rig
            if (poseTween) {
                const p = poseTween;
                p.t += dt * 1000;
                const k = easeInOutCubic(Math.min(1, p.t / p.dur));
                if (p.midPos) quadraticBezier(camera.position, p.fromPos, p.midPos, p.toPos, k);
                else camera.position.lerpVectors(p.fromPos, p.toPos, k);
                camera.quaternion.slerpQuaternions(p.fromQuat, p.toQuat, k);
                const fk = Math.max(0, (k - 0.6) / 0.4); // fov morphs in the last 40%
                camera.fov = p.fromFov + (p.toFov - p.fromFov) * fk;
                camera.updateProjectionMatrix();
                if (p.t >= p.dur) {
                    if (p.hold) {
                        heldPose = { pos: p.toPos, quat: p.toQuat, fov: p.toFov, projection: p.projection || null };
                        applyCustomProjection(heldPose.projection);
                    }
                    else { state.locked = false; camera.fov = p.toFov; camera.updateProjectionMatrix(); }
                    poseTween = null;
                }
                return;
            }
            if (heldPose) {
                camera.position.copy(heldPose.pos);
                camera.quaternion.copy(heldPose.quat);
                applyCustomProjection(heldPose.projection);
                return;
            }
            if (tween) {
                tween.t += dt * 1000;
                const k = easeInOutCubic(Math.min(1, tween.t / tween.dur));
                cur.az = tween.fromAz + tween.dAz * k;
                cur.el = tween.fromEl + tween.dEl * k;
                cur.radius = tween.fromR + tween.dR * k;
                state.target.lerpVectors(tween.fromTarget, tween.toTarget, k);
                if (tween.t >= tween.dur) {
                    cur.az = ((tween.fromAz + tween.dAz) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
                    // Land exactly: detent 0 must be the fitted centre, not a lerp away from it.
                    state.target.copy(tween.toTarget);
                    tween = null;
                }
            }
            // double-lerp micro-pivot (dt-corrected EL recipe)
            const p = state.pivot;
            const k1 = 1 - Math.exp(-dt / TAU_PIVOT);
            const k2 = 1 - Math.exp(-dt / TAU_DRIFT);
            p.cx += (p.tx - p.cx) * k1;
            p.cy += (p.ty - p.cy) * k1;
            p.dx += (p.cx - p.dx) * k2;
            p.dy += (p.cy - p.dy) * k2;

            applyOrbitPose();
        },

        isTweening() { return !!tween; },
        azimuthIndex() { return state.az; },
        elevationIndex() { return state.el; },
        zoomIndex() { return state.zoom; },
        atWorldBoundary() { return !state.locked && state.zoom === 0; },
        currentRadius() { return cur.radius * (1 + state.overshoot); },

        /* Continuous pre-zoom past the overview detent. `k` is a fraction of the
         * detent radius; 0 restores the detent exactly. */
        setOvershoot(k) {
            if (state.locked) return state.overshoot;
            const next = Number(k);
            state.overshoot = Number.isFinite(next) ? THREE.MathUtils.clamp(next, 0, MAX_OVERSHOOT) : 0;
            applyOrbitPose();
            return state.overshoot;
        },
        overshoot() { return state.overshoot; },
        maxOvershoot() { return MAX_OVERSHOOT; },

        /* Everything needed to put the camera back exactly where it left.
         *
         * goTo() cannot express this: it takes quantized detent indices, and at
         * dur <= 0 it overwrites cur.* from zoomRadius(state.zoom), discarding
         * overshoot, drag previews, and any mid-tween pose. The handoff can begin
         * mid-tween, because atWorldBoundary() says nothing about isTweening(). */
        snapshotPose() {
            return {
                az: state.az, el: state.el, zoom: state.zoom,
                curAz: cur.az, curEl: cur.el, curRadius: cur.radius,
                previewAz: state.previewAz, previewEl: state.previewEl,
                overshoot: state.overshoot,
                fitDistance: state.fitDistance,
                target: state.target.clone(),
                homeTarget: homeTarget.clone(),
                targetBounds: targetBounds ? targetBounds.clone() : null,
                pivot: { ...state.pivot },
            };
        },

        restorePose(snapshot) {
            if (!snapshot) return false;
            tween = null;
            poseTween = null;
            heldPose = null;
            state.locked = false;
            state.az = snapshot.az;
            state.el = snapshot.el;
            state.zoom = snapshot.zoom;
            state.previewAz = snapshot.previewAz || 0;
            state.previewEl = snapshot.previewEl || 0;
            state.overshoot = snapshot.overshoot || 0;
            if (Number.isFinite(snapshot.fitDistance)) state.fitDistance = snapshot.fitDistance;
            if (snapshot.target) state.target.copy(snapshot.target);
            if (snapshot.homeTarget) homeTarget.copy(snapshot.homeTarget);
            if (snapshot.targetBounds) targetBounds = snapshot.targetBounds.clone();
            if (snapshot.pivot) Object.assign(state.pivot, snapshot.pivot);
            cur.az = snapshot.curAz;
            cur.el = snapshot.curEl;
            cur.radius = snapshot.curRadius;
            camera.fov = BASE_FOV;
            camera.updateProjectionMatrix();
            applyOrbitPose();
            return true;
        },

        /* Camera forward and up in Apartment-local Z-up coordinates.
         *
         * Direction vectors go through the rotation only, matching picking.js:
         * worldToLocal is a point transform, correct here only for the eye.
         *
         * The hover micro-pivot is excluded by default. Two orthogonal rotations
         * of PIVOT_MAX compose to 0.81 degrees of forward deviation and induce
         * roll of about dx*sin(el), which is 0.50 degrees at the 60-degree
         * elevation stop: exactly the continuity budget the world pose is held
         * to. Excluding it makes roll identically zero and the round trip exact.
         * It is also continuous drift state that no snapshot can pin, because it
         * keeps integrating toward the live hover target for as long as the world
         * owns the viewport. */
        cameraBasisApartmentLocal(apartmentRoot, { includePivot = false } = {}) {
            if (!apartmentRoot) return null;
            apartmentRoot.updateMatrixWorld(true);
            const invRoot = apartmentRoot.getWorldQuaternion(new THREE.Quaternion()).invert();
            const az = cur.az + state.previewAz;
            const el = THREE.MathUtils.clamp(cur.el + state.previewEl, 0.05, Math.PI / 2 - 0.02);
            const radius = cur.radius * (1 + state.overshoot);
            const t = state.target;

            let forwardWorld;
            let upWorld;
            if (includePivot) {
                camera.updateMatrixWorld(true);
                const q = camera.getWorldQuaternion(new THREE.Quaternion());
                forwardWorld = new THREE.Vector3(0, 0, -1).applyQuaternion(q);
                upWorld = new THREE.Vector3(0, 1, 0).applyQuaternion(q);
            } else {
                // The pure lookAt basis, recomputed from detent geometry so no
                // pivot, tween, or held pose can contaminate it.
                const eye = new THREE.Vector3(
                    t.x + radius * Math.cos(el) * Math.sin(az),
                    t.y + radius * Math.sin(el),
                    t.z + radius * Math.cos(el) * Math.cos(az),
                );
                forwardWorld = new THREE.Vector3().subVectors(t, eye).normalize();
                const right = new THREE.Vector3().crossVectors(forwardWorld, WORLD_UP);
                if (right.lengthSq() < 1e-8) right.set(1, 0, 0);
                right.normalize();
                upWorld = new THREE.Vector3().crossVectors(right, forwardWorld).normalize();
            }

            const forward = forwardWorld.clone().applyQuaternion(invRoot);
            const up = upWorld.clone().applyQuaternion(invRoot);
            return {
                forwardLocal: [forward.x, forward.y, forward.z],
                upLocal: [up.x, up.y, up.z],
                verticalFovDeg: camera.fov,
                rangeM: radius,
                includedPivot: includePivot === true,
            };
        },

        /* Edit mode stays inside the detent system so pointer drag, keyboard
         * arrows, and explicit orbit controls all remain useful. Keep the
         * current azimuth and use the 35-degree isometric elevation so wall
         * faces are visible from the first editor frame. */
        goEditPose(dur = 0) {
            /* The centre is part of the pose: at a detent reached by zooming
             * toward the cursor it is off the fitted centre, and detent 0 is
             * about to re-centre it. Save the tween's goal, not wherever a
             * zoom in flight had got to. */
            if (!editReturn) {
                editReturn = {
                    az: state.az, el: state.el, zoom: state.zoom,
                    target: (tween ? tween.toTarget : state.target).clone(),
                };
            }
            clearAbsolutePose();
            goTo({ az: state.az, el: 0, zoom: 0, dur });
        },
        exitEditPose(dur = 600) {
            state.locked = false;
            const previous = editReturn;
            editReturn = null;
            goTo(previous ? { ...previous, dur } : { dur });
        },

        /* Absolute-pose flight (P4 camera snap): tween to a world pose with
         * the fov morphing in the FINAL 40% of the flight (lens-breathing
         * arrival). The pose is HELD (orbit suspended) until
         * returnToOverview() flies back and unlocks. */
        flyToPose({ position, quaternion, fov, dur = 900, projection = null }) {
            if (reducedMotion()) dur = 1; // jump cut — the swoop is a vestibular hazard
            state.locked = true;
            const fromPos = camera.position.clone();
            const toPos = position.clone();
            poseTween = {
                t: 0, dur, hold: true,
                fromPos, fromQuat: camera.quaternion.clone(),
                fromFov: camera.fov,
                midPos: cameraSwoopMidpoint(fromPos, toPos),
                toPos, toQuat: quaternion.clone(),
                toFov: fov || camera.fov,
                projection,
            };
        },
        returnToOverview(dur = 700) {
            const t = state.target;
            const az = azRad(state.az), el = elRad(state.el), r = zoomRadius(state.zoom);
            const toPos = new THREE.Vector3(
                t.x + r * Math.cos(el) * Math.sin(az),
                t.y + r * Math.sin(el),
                t.z + r * Math.cos(el) * Math.cos(az));
            const m = new THREE.Matrix4().lookAt(toPos, t, camera.up);
            poseTween = {
                t: 0, dur, hold: false,
                fromPos: camera.position.clone(), fromQuat: camera.quaternion.clone(),
                fromFov: camera.fov,
                toPos, toQuat: new THREE.Quaternion().setFromRotationMatrix(m),
                toFov: BASE_FOV,
            };
            heldPose = null;
            editReturn = null;
        },
        inCameraPose() { return !!(heldPose || (poseTween && poseTween.hold)); },
        cameraPoseFlying() { return !!(poseTween && poseTween.hold); },
        cameraPoseSettled() { return !!heldPose; },

        /* Instant hard reset (view unmount / recovery): drop any held or
         * in-flight pose and unlock input. The resident engine survives view
         * closes — without this, closing while snapped (or mid-flight, or in
         * the edit pose) leaves heldPose/locked frozen on the cached rig and
         * the reopened view is a dead camera with all input gated off. The
         * orbit branch recomputes position/lookAt from detent state on the
         * next frame, so no pose snapshot is needed. */
        resetPose() {
            poseTween = null;
            heldPose = null;
            pan = null;
            state.locked = false;
            state.previewAz = 0;
            state.previewEl = 0;
            // A close while pulled back must not strand the overshoot on the
            // resident rig; the reopened view would start silently zoomed out.
            state.overshoot = 0;
            state.pivot.tx = 0; state.pivot.ty = 0;
            state.pivot.cx = 0; state.pivot.cy = 0;
            state.pivot.dx = 0; state.pivot.dy = 0;
            cur.az = azRad(state.az);
            cur.el = elRad(state.el);
            cur.radius = zoomRadius(state.zoom);
            camera.fov = BASE_FOV;
            camera.updateProjectionMatrix();
            applyOrbitPose();
        },
    };
    return rig;
}

/* Attach constrained pointer/wheel input to a host element. Returns detach().
 *
 * Drag orbits; Shift-drag or right-drag pans; two pointers pinch through the
 * zoom detents (each PINCH_STEP of spread is one detent, toward the midpoint
 * like a wheel notch toward the cursor). A second pointer cancels an orbit or
 * pan in progress, and the pointers left after a pinch never start an orbit. */
export function attachInput(el, rig, { onZoomStep = null } = {}) {
    let drag = null;
    let panDrag = null;
    let pinch = null;
    let wheelAt = 0;
    const pointers = new Map();

    const ndcAt = (clientX, clientY) => {
        let rect = null;
        try { rect = el.getBoundingClientRect?.() ?? null; } catch (err) { /* */ }
        return rect && rect.width > 0 && rect.height > 0 ? {
            x: ((clientX - rect.left) / rect.width) * 2 - 1,
            y: -((clientY - rect.top) / rect.height) * 2 + 1,
        } : null;
    };
    const capture = (e) => { try { el.setPointerCapture(e.pointerId); } catch (err) { /* */ } };
    const release = (e) => { try { el.releasePointerCapture(e.pointerId); } catch (err) { /* */ } };
    const spread = () => {
        const [a, b] = [...pointers.values()];
        return { d: Math.hypot(b.x - a.x, b.y - a.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    };
    /* One zoom detent, through the host's zoom path when it has one (the
     * same path a wheel notch takes), with the pinch midpoint lent as the
     * focus for exactly this dispatch. */
    const zoomStep = (direction, at) => {
        rig.setZoomFocus?.(direction > 0 ? ndcAt(at.x, at.y) : null);
        try {
            if (typeof onZoomStep === 'function') onZoomStep(direction, {
                source: 'pinch',
                continuousBoundary: false,
                viewportHeightPx: el.clientHeight || 800,
            });
            else rig.stepZoom(direction);
        } finally {
            rig.setZoomFocus?.(null);
        }
    };

    const onDown = (e) => {
        if (e.pointerType === 'touch' || pointers.size > 0) pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size >= 2) {
            if (drag) { rig.dragCancel?.(); drag = null; }
            if (panDrag) { rig.panEnd?.(); panDrag = null; }
            if ((!pinch || pinch.ended) && pointers.size === 2) {
                const s = spread();
                pinch = { d0: Math.max(1, s.d), steps: 0 };
            }
            capture(e);
            return;
        }
        if (pinch) return;
        if (e.button === 2 || (e.button === 0 && e.shiftKey)) {
            if (rig.panStart?.()) {
                panDrag = { x0: e.clientX, y0: e.clientY, id: e.pointerId };
                capture(e);
            }
            return;
        }
        if (e.button !== 0) return;
        drag = { id: e.pointerId, x0: e.clientX, y0: e.clientY, t0: performance.now(), lastX: e.clientX, lastT: performance.now(), vx: 0 };
        capture(e);
    };
    const onMove = (e) => {
        if (pointers.has(e.pointerId)) pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pinch) {
            if (pinch.ended || pointers.size < 2) return;
            const s = spread();
            const target = Math.trunc(Math.log(Math.max(1e-3, s.d / pinch.d0)) / Math.log(PINCH_STEP));
            while (pinch && pinch.steps < target) { pinch.steps += 1; zoomStep(1, s); }
            while (pinch && pinch.steps > target) { pinch.steps -= 1; zoomStep(-1, s); }
            return;
        }
        if (panDrag) {
            if (e.pointerId === panDrag.id) rig.panMove?.(e.clientX - panDrag.x0, e.clientY - panDrag.y0, el.clientHeight || 800);
            return;
        }
        if (drag) {
            const now = performance.now();
            const dtm = Math.max(1, now - drag.lastT);
            drag.vx = (e.clientX - drag.lastX) / dtm;
            drag.lastX = e.clientX; drag.lastT = now;
            rig.dragPreview(e.clientX - drag.x0, e.clientY - drag.y0, el.clientWidth || 800);
        } else {
            const rect = el.getBoundingClientRect();
            const nx = ((e.clientX - rect.left) / rect.width) * 2 - 1;
            const ny = ((e.clientY - rect.top) / rect.height) * 2 - 1;
            rig.hoverPivot(nx, -ny);
        }
    };
    const onUp = (e) => {
        pointers.delete(e.pointerId);
        if (pinch) {
            // A finger left after a pinch stays inert until every finger lifts.
            if (!pointers.size) pinch = null;
            else if (pointers.size < 2) pinch.ended = true;
            release(e);
            return;
        }
        if (panDrag) {
            if (e.pointerId !== panDrag.id) return;
            rig.panEnd?.();
            panDrag = null;
            release(e);
            return;
        }
        if (!drag || drag.id !== e.pointerId) return;
        rig.dragRelease(e.clientX - drag.x0, e.clientY - drag.y0, drag.vx);
        drag = null;
        release(e);
    };
    // The canvas has no context menu; a right-drag pans instead.
    const onContextMenu = (e) => { e.preventDefault?.(); };
    const onWheel = (e) => {
        e.preventDefault();
        const now = performance.now();
        const direction = e.deltaY > 0 ? -1 : 1;
        /* Keep the established one-detent cadence INSIDE the Apartment. At the
         * outer boundary every native delta instead feeds the elastic momentum
         * band; suppressing 149ms of a trackpad gesture is what made the seam
         * feel like a button rather than a continuation of scroll. */
        const continuousBoundary = direction < 0 && rig.atWorldBoundary?.();
        if (!continuousBoundary && now - wheelAt < 150) return;
        if (!continuousBoundary) wheelAt = now;
        /* The host's zoom path reaches rig.stepZoom() synchronously for an
         * interior detent, so the cursor is lent for exactly this dispatch.
         * If that path ever becomes asynchronous the zoom degrades to the
         * centre, never to a stale cursor. */
        let rect = null;
        try { rect = el.getBoundingClientRect?.() ?? null; } catch (err) { /* */ }
        rig.setZoomFocus?.(rect && rect.width > 0 && rect.height > 0 ? {
            x: ((e.clientX - rect.left) / rect.width) * 2 - 1,
            y: -((e.clientY - rect.top) / rect.height) * 2 + 1,
        } : null);
        try {
            if (typeof onZoomStep === 'function') onZoomStep(direction, {
                source: 'wheel',
                continuousBoundary,
                deltaY: e.deltaY,
                deltaMode: e.deltaMode,
                viewportHeightPx: el.clientHeight || 800,
                // The boundary counts slow notches across an idle pull by this.
                timeStamp: e.timeStamp,
            });
            else rig.stepZoom(direction);
        } finally {
            rig.setZoomFocus?.(null);
        }
    };
    const onLeave = () => rig.hoverPivot(0, 0);

    el.addEventListener('pointerdown', onDown);
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('pointercancel', onUp);
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('pointerleave', onLeave);
    el.addEventListener('contextmenu', onContextMenu);
    return () => {
        el.removeEventListener('pointerdown', onDown);
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerup', onUp);
        el.removeEventListener('pointercancel', onUp);
        el.removeEventListener('wheel', onWheel);
        el.removeEventListener('pointerleave', onLeave);
        el.removeEventListener('contextmenu', onContextMenu);
    };
}
