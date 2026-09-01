/* home-3d/rig.js — the constrained camera rig.
 *
 * Quantized navigation per the plan: 8 azimuth stops (45°), 2 elevation stops
 * (35°/60°), 3 zoom detents (overview/room/close), rubber-band drag previews
 * with snap-back, and the engineered.lighting micro-pivot (double-lerp drift,
 * dt-corrected). No free-fly, ever.
 *
 * Works in three.js world space (Y-up; the apartmentRoot converts the Z-up data).
 */
import * as THREE from 'three';

const AZ_STOPS = 8;
const ELEVATIONS = [35, 60];          // degrees
const ZOOM_FACTORS = [1.0, 0.55, 0.3]; // × fitDistance
const HOME = { az: 1, el: 0, zoom: 0 };
const WORLD_UP = new THREE.Vector3(0, 1, 0);

const MAX_PREVIEW = THREE.MathUtils.degToRad(20);
const DRAG_RESIST = 0.35;
const COMMIT_PX = 24;
const ELEV_PX = 36;
const PIVOT_MAX = 0.010;              // rad — EL feel, slightly amplified
const TAU_PIVOT = 0.55;               // s (EL 0.003/frame ≈ 5.5 s is too dreamy for a dashboard at first paint; 10× faster, still soft)
const TAU_DRIFT = 1.1;

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
    };

    const cur = { az: azRad(HOME.az), el: elRad(HOME.el), radius: 18 };
    let tween = null;
    let poseTween = null;   // absolute-pose flight (camera snap)
    let heldPose = null;    // held camera pose after arrival
    let editReturn = null;  // detented pose restored when Apartment editing ends
    const BASE_FOV = camera.fov;

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

    function startTween(toAz, toEl, toRadius, dur = 450) {
        if (reducedMotion()) dur = 1;
        tween = {
            t: 0, dur,
            fromAz: cur.az, dAz: shortestAngle(cur.az, toAz),
            fromEl: cur.el, dEl: toEl - cur.el,
            fromR: cur.radius, dR: toRadius - cur.radius,
        };
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
        const r = cur.radius;
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

    function goTo({ az = state.az, el = state.el, zoom = state.zoom, dur = 450 }) {
        state.az = ((az % AZ_STOPS) + AZ_STOPS) % AZ_STOPS;
        state.el = THREE.MathUtils.clamp(el, 0, ELEVATIONS.length - 1);
        state.zoom = THREE.MathUtils.clamp(zoom, 0, ZOOM_FACTORS.length - 1);
        const toAz = azRad(state.az);
        const toEl = elRad(state.el);
        const toRadius = zoomRadius(state.zoom);
        if (dur <= 0) {
            tween = null;
            cur.az = toAz;
            cur.el = toEl;
            cur.radius = toRadius;
            applyOrbitPose();
        } else {
            startTween(toAz, toEl, toRadius, dur);
        }
    }

    const rig = {
        state,
        goTo,
        stepAzimuth(dir, dur = 450) { if (!state.locked) goTo({ az: state.az + dir, dur }); },
        stepElevation(dir, dur = 450) { if (!state.locked) goTo({ el: state.el + dir, dur }); },
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
            goTo({ zoom: next, dur });
            return { accepted: true, zoom: state.zoom };
        },
        snapHome(dur = 500) { if (!state.locked) goTo({ ...HOME, dur }); },

        /* Fit the apartment bounds (world space). Desktop keeps the established
         * tight dollhouse crop; portrait mobile uses projected box corners so
         * the whole apartment lands inside the usable HUD-safe viewport. */
        fitBounds(box3, { dur = 700 } = {}) {
            const sphere = box3.getBoundingSphere(new THREE.Sphere());
            const fovV = THREE.MathUtils.degToRad(camera.fov);
            const fovH = 2 * Math.atan(Math.tan(fovV / 2) * camera.aspect);
            state.target.copy(sphere.center);
            state.target.y = Math.min(Math.max(0.9, sphere.center.y * 0.6), sphere.center.y);
            const viewport = fitViewport(camera.aspect || 1);
            const sphereFit = (sphere.radius * 0.85) / Math.sin(Math.min(fovV, fovH) / 2);
            const viewFit = projectedFitDistance(
                box3,
                state.target,
                azRad(state.az),
                elRad(state.el),
                fovV,
                camera.aspect || 1,
                viewport.safeX,
                viewport.safeY,
            );
            state.fitDistance = Math.max(sphereFit, viewFit) * viewport.extra;
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
            state.previewAz = 0;
            state.previewEl = 0;
            if (state.locked) return;
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
                if (tween.t >= tween.dur) {
                    cur.az = ((tween.fromAz + tween.dAz) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
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
        currentRadius() { return cur.radius; },

        /* Edit mode stays inside the detent system so pointer drag, keyboard
         * arrows, and explicit orbit controls all remain useful. Keep the
         * current azimuth and use the 35-degree isometric elevation so wall
         * faces are visible from the first editor frame. */
        goEditPose(dur = 0) {
            if (!editReturn) editReturn = { az: state.az, el: state.el, zoom: state.zoom };
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
            state.locked = false;
            state.previewAz = 0;
            state.previewEl = 0;
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

/* Attach constrained pointer/wheel input to a host element. Returns detach(). */
export function attachInput(el, rig, { onZoomStep = null } = {}) {
    let drag = null;
    let wheelAt = 0;

    const onDown = (e) => {
        if (e.button !== 0) return;
        drag = { x0: e.clientX, y0: e.clientY, t0: performance.now(), lastX: e.clientX, lastT: performance.now(), vx: 0 };
        try { el.setPointerCapture(e.pointerId); } catch (err) { /* */ }
    };
    const onMove = (e) => {
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
        if (!drag) return;
        rig.dragRelease(e.clientX - drag.x0, e.clientY - drag.y0, drag.vx);
        drag = null;
        try { el.releasePointerCapture(e.pointerId); } catch (err) { /* */ }
    };
    const onWheel = (e) => {
        e.preventDefault();
        const now = performance.now();
        if (now - wheelAt < 150) return; // one detent per gesture
        wheelAt = now;
        const direction = e.deltaY > 0 ? -1 : 1;
        if (typeof onZoomStep === 'function') onZoomStep(direction, { source: 'wheel' });
        else rig.stepZoom(direction);
    };
    const onLeave = () => rig.hoverPivot(0, 0);

    el.addEventListener('pointerdown', onDown);
    el.addEventListener('pointermove', onMove);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('pointercancel', onUp);
    el.addEventListener('wheel', onWheel, { passive: false });
    el.addEventListener('pointerleave', onLeave);
    return () => {
        el.removeEventListener('pointerdown', onDown);
        el.removeEventListener('pointermove', onMove);
        el.removeEventListener('pointerup', onUp);
        el.removeEventListener('pointercancel', onUp);
        el.removeEventListener('wheel', onWheel);
        el.removeEventListener('pointerleave', onLeave);
    };
}
