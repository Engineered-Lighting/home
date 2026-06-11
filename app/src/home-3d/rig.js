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

const MAX_PREVIEW = THREE.MathUtils.degToRad(20);
const DRAG_RESIST = 0.35;
const COMMIT_PX = 60;
const ELEV_PX = 36;
const PIVOT_MAX = 0.010;              // rad — EL feel, slightly amplified
const TAU_PIVOT = 0.55;               // s (EL 0.003/frame ≈ 5.5 s is too dreamy for a dashboard at first paint; 10× faster, still soft)
const TAU_DRIFT = 1.1;

const easeInOutCubic = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

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

    function azRad(i) { return (i / AZ_STOPS) * Math.PI * 2; }
    function elRad(i) { return THREE.MathUtils.degToRad(ELEVATIONS[i]); }
    function zoomRadius(i) { return state.fitDistance * ZOOM_FACTORS[i]; }

    function shortestAngle(from, to) {
        let d = (to - from) % (Math.PI * 2);
        if (d > Math.PI) d -= Math.PI * 2;
        if (d < -Math.PI) d += Math.PI * 2;
        return d;
    }

    function startTween(toAz, toEl, toRadius, dur = 450) {
        tween = {
            t: 0, dur,
            fromAz: cur.az, dAz: shortestAngle(cur.az, toAz),
            fromEl: cur.el, dEl: toEl - cur.el,
            fromR: cur.radius, dR: toRadius - cur.radius,
        };
    }

    function goTo({ az = state.az, el = state.el, zoom = state.zoom, dur = 450 }) {
        state.az = ((az % AZ_STOPS) + AZ_STOPS) % AZ_STOPS;
        state.el = THREE.MathUtils.clamp(el, 0, ELEVATIONS.length - 1);
        state.zoom = THREE.MathUtils.clamp(zoom, 0, ZOOM_FACTORS.length - 1);
        startTween(azRad(state.az), elRad(state.el), zoomRadius(state.zoom), dur);
    }

    const rig = {
        state,
        goTo,
        stepAzimuth(dir, dur = 450) { goTo({ az: state.az + dir, dur }); },
        stepElevation(dir, dur = 450) { goTo({ el: state.el + dir, dur }); },
        stepZoom(dir, dur = 350) { goTo({ zoom: state.zoom + dir, dur }); },
        snapHome(dur = 500) { goTo({ ...HOME, dur }); },

        /* Fit the apartment bounds (world space). Called once assets load.
         * 0.85 (not a "safe" 1.1+): the apartment is a flat slab, so the
         * bounding-sphere fit massively over-distances — verified visually. */
        fitBounds(box3) {
            const sphere = box3.getBoundingSphere(new THREE.Sphere());
            const fovV = THREE.MathUtils.degToRad(camera.fov);
            const fovH = 2 * Math.atan(Math.tan(fovV / 2) * camera.aspect);
            const fit = (sphere.radius * 0.85) / Math.sin(Math.min(fovV, fovH) / 2);
            state.fitDistance = fit;
            state.target.copy(sphere.center);
            state.target.y = Math.min(Math.max(0.9, sphere.center.y * 0.6), sphere.center.y);
            goTo({ dur: 700 });
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
            state.pivot.tx = nx * PIVOT_MAX;
            state.pivot.ty = ny * PIVOT_MAX;
        },

        /* per-frame ----------------------------------------------------------- */
        update(dt) {
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
        },

        isTweening() { return !!tween; },
        azimuthIndex() { return state.az; },
        currentRadius() { return cur.radius; },
    };
    return rig;
}

/* Attach constrained pointer/wheel input to a host element. Returns detach(). */
export function attachInput(el, rig) {
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
        rig.stepZoom(e.deltaY > 0 ? -1 : 1);
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
