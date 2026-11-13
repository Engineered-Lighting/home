/* The live body's labels in the apartment view, in the owner-approved "System" style (2026-11-11): a hairline caption
 * beside the person at every zoom (name; name and what they're doing; everything up close), a faint floor ring with a
 * lit arc and pointer for where their head faces, a faint sight line up close, a solid accent tag for what they hold,
 * and a glass card with everything on tap. Home's type and colour: Geist for words, Geist Mono for small uppercase
 * detail, #EDEDED / #A1A1A1, ice #B8D8FF, accent #4D9CFF. The rules live in apartment-body-annotation-model.mjs.
 * Drawn on a 2D canvas over the viewport (crisp type); the card is a DOM element (frosted with backdrop-filter). It
 * never covers the body and takes no pointer events: a tap is read from the viewport without stopping the orbit.
 * Presentation only: the identity, activity and house lines are supplied by the caller. */
import * as THREE from 'three';
import { createBodyAnnotationState, captionLines, cardRows } from './apartment-body-annotation-model.mjs';
import { zoomRatio } from './apartment-body-frame.mjs';

export const LABEL_STYLE = Object.freeze({ sans: '"Geist", ui-sans-serif, -apple-system, system-ui, sans-serif',
    mono: '"Geist Mono", ui-monospace, "SF Mono", Menlo, monospace', fg: '#EDEDED', dim: '#A1A1A1', ice: '#B8D8FF', accent: '#4D9CFF' });
const S = LABEL_STYLE, TAP_PX = 6, TAP_MS = 450, HIT_PX = 56;

function smoother() {
    const s = new Map();
    return (key, x, y, dt, tau = 0.24) => {
        let p = s.get(key);
        if (!p || !Number.isFinite(p.x)) { p = { x, y }; s.set(key, p); return p; }
        const k = 1 - Math.exp(-Math.max(0, dt) / tau); p.x += (x - p.x) * k; p.y += (y - p.y) * k; return p;
    };
}
function spaced(ctx, text, x, y, track) { let cx = x; for (const ch of text) { ctx.fillText(ch, cx, y); cx += ctx.measureText(ch).width + track; } return cx - x - track; }
function spacedWidth(ctx, text, track) { let w = 0; for (const ch of text) w += ctx.measureText(ch).width + track; return Math.max(0, w - track); }
function roundRect(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function corners(ctx, x0, y0, x1, y1, len, color, width) {
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.beginPath();
    for (const [x, y, dx, dy] of [[x0, y0, 1, 1], [x1, y0, -1, 1], [x0, y1, 1, -1], [x1, y1, -1, -1]]) { ctx.moveTo(x + dx * len, y); ctx.lineTo(x, y); ctx.lineTo(x, y + dy * len); }
    ctx.stroke();
}

export function attachApartmentBodyLabels({ engine, host, layer, person = () => null, activity = () => null, house = () => null,
    now = () => performance.now() / 1000, animate = true } = {}) {
    const parent = engine?.apartmentRoot, camera = engine?.camera, doc = host?.ownerDocument;
    if (!parent || !camera || !doc || typeof host.appendChild !== 'function' || typeof layer?.snapshot !== 'function') throw Error('apartment_body_labels_dependencies_required');
    const canvas = doc.createElement('canvas');
    canvas.className = 'apt-body-labels';
    canvas.setAttribute('aria-hidden', 'true');
    Object.assign(canvas.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', zIndex: '3' });
    const card = doc.createElement('div');
    card.className = 'apt-body-card';
    Object.assign(card.style, { position: 'absolute', left: '0', top: '0', display: 'none', pointerEvents: 'none', zIndex: '4', minWidth: '208px', padding: '12px 16px 13px',
        borderRadius: '10px', border: '1px solid rgba(255,255,255,0.10)', background: 'rgba(8,9,11,0.66)', backdropFilter: 'blur(14px) saturate(1.3)',
        WebkitBackdropFilter: 'blur(14px) saturate(1.3)', color: S.fg, font: `400 12.5px/1.5 ${S.sans}`, boxShadow: '0 8px 30px rgba(0,0,0,0.35)' });
    host.appendChild(canvas); host.appendChild(card);
    const ctx = canvas.getContext('2d');
    const state = createBodyAnnotationState(), v = new THREE.Vector3();
    let smooth = smoother();
    let disposed = false, rafId = null, last = null, cardOpen = false, tapStart = null, hit = null, shown = null;

    function project(p) {
        v.set(p[0], p[1], p[2]); parent.localToWorld(v); v.project(camera);
        const w = host.clientWidth, h = host.clientHeight;
        return [(v.x * 0.5 + 0.5) * w, (-v.y * 0.5 + 0.5) * h, v.z <= 1 && v.z >= -1];
    }
    const down = event => { tapStart = { x: event.clientX, y: event.clientY, t: event.timeStamp }; };
    const up = event => {
        const s = tapStart; tapStart = null;
        if (!s || Math.hypot(event.clientX - s.x, event.clientY - s.y) > TAP_PX || event.timeStamp - s.t > TAP_MS) return;
        const r = host.getBoundingClientRect(), x = event.clientX - r.left, y = event.clientY - r.top;
        const onPerson = hit && hit.some(([hx, hy]) => Math.hypot(x - hx, y - hy) < HIT_PX);
        cardOpen = onPerson ? !cardOpen : false;
    };
    host.addEventListener('pointerdown', down, { passive: true });
    host.addEventListener('pointerup', up, { passive: true });

    function caption(st, lines, head, w, h, dt, zoomT) {
        const a = smooth('anchor', head[0], head[1] - 6, dt), L = 60 + 30 * (1 - zoomT), dir = st.right ? 1 : -1;
        const by = Math.max(46, Math.min(h - 90, a.y - L)), bx = a.x + dir * Math.max(L * 0.55, L - (a.y - by));
        const b = smooth('block', Math.max(40, Math.min(w - 40, bx)), by, dt, 0.22), tx = st.right ? b.x + 24 : b.x - 24;
        const bw = st.detail === 0 ? 120 : 250, bh = st.detail === 0 ? 34 : st.detail === 1 ? 56 : 92;
        const cx = st.right ? tx + bw * 0.42 : tx - bw * 0.42, cy = b.y + bh * 0.4 - 8;
        ctx.save(); ctx.translate(cx, cy); ctx.scale(1, bh / bw * 1.5); ctx.translate(-cx, -cy);
        const scrim = ctx.createRadialGradient(cx, cy, 4, cx, cy, bw * 0.75); scrim.addColorStop(0, 'rgba(0,0,0,0.46)'); scrim.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = scrim; ctx.beginPath(); ctx.arc(cx, cy, bw * 0.75, 0, 7); ctx.fill(); ctx.restore();
        ctx.save(); ctx.shadowColor = 'rgba(0,0,0,0.75)'; ctx.shadowBlur = 10;
        ctx.strokeStyle = 'rgba(237,237,237,0.7)'; ctx.lineWidth = 1.25; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.lineTo(b.x + dir * 18, b.y); ctx.stroke();
        ctx.fillStyle = S.fg; ctx.beginPath(); ctx.arc(a.x, a.y, 3, 0, 7); ctx.fill();
        ctx.textAlign = st.right ? 'left' : 'right'; ctx.textBaseline = 'alphabetic';
        ctx.font = `600 ${st.detail === 0 ? 17 : 20}px ${S.sans}`; ctx.fillStyle = '#FFFFFF'; ctx.fillText(lines.name, tx, b.y + 7);
        ctx.textAlign = 'left';
        if (lines.doing) { ctx.font = `500 12px ${S.mono}`; ctx.fillStyle = '#D4D4D4'; const t = lines.doing.toUpperCase(), tw = spacedWidth(ctx, t, 1.8); spaced(ctx, t, st.right ? tx : tx - tw, b.y + 28, 1.8); }
        if (lines.provenance) { ctx.font = `500 10.5px ${S.mono}`; ctx.fillStyle = S.dim; const t = lines.provenance.toUpperCase(), tw = spacedWidth(ctx, t, 1.8); spaced(ctx, t, st.right ? tx : tx - tw, b.y + 46, 1.8); }
        if (lines.house) { ctx.font = `500 14px ${S.sans}`; ctx.fillStyle = S.ice; const hw = ctx.measureText(lines.house).width; ctx.fillText(lines.house, st.right ? tx : tx - hw, b.y + 70); }
        ctx.restore();
        return [[a.x, a.y], [tx + dir * 60, b.y]];
    }
    function ring(st, centroid, dt) {
        const R = st.ringRadius, z = st.ringZ, cx = centroid[0], cy = centroid[1];
        camera.getWorldPosition(v); parent.worldToLocal(v);
        const cam = [v.x - cx, v.y - cy], cl = Math.hypot(cam[0], cam[1]) || 1;
        ctx.save();
        let prev = null;
        for (let i = 0; i <= 96; i++) {
            const a = i / 96 * Math.PI * 2, p = project([cx + R * Math.cos(a), cy + R * Math.sin(a), z]), front = Math.max(0, (Math.cos(a) * cam[0] + Math.sin(a) * cam[1]) / cl);
            if (prev && p[2] && prev[2]) { ctx.strokeStyle = `rgba(243,237,228,${(0.12 + 0.4 * front) * 0.7})`; ctx.lineWidth = 0.8 + 0.6 * front; ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(p[0], p[1]); ctx.stroke(); }
            prev = p;
        }
        if (st.facingAz != null) {
            const az = st.facingAz, arc = [];
            for (let k = -6; k <= 6; k++) { const a = az + k * 0.045; arc.push([...project([cx + R * Math.cos(a), cy + R * Math.sin(a), z]), 1 - Math.abs(k) / 7]); }
            for (let k = 0; k < arc.length - 1; k++) { const p = arc[k], q = arc[k + 1], s = Math.min(p[3], q[3]);
                ctx.strokeStyle = `rgba(184,216,255,${0.2 + 0.7 * s})`; ctx.lineWidth = 1.2 + 1.6 * s; ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke(); }
            const tip = project([cx + (R + 0.075) * Math.cos(az), cy + (R + 0.075) * Math.sin(az), z]);
            const l = project([cx + R * Math.cos(az + 0.07), cy + R * Math.sin(az + 0.07), z]), r = project([cx + R * Math.cos(az - 0.07), cy + R * Math.sin(az - 0.07), z]);
            ctx.fillStyle = 'rgba(184,216,255,0.95)'; ctx.beginPath(); ctx.moveTo(tip[0], tip[1]); ctx.lineTo(l[0], l[1]); ctx.lineTo(r[0], r[1]); ctx.closePath(); ctx.fill();
            if (st.detail >= 2 && st.eye && st.facing) {
                const f = st.facing, G = 0.4, rot = (u, a) => [u[0] * Math.cos(a) - u[1] * Math.sin(a), u[0] * Math.sin(a) + u[1] * Math.cos(a), u[2]];
                const e = project(st.eye), gl = rot(f, 0.16), gr = rot(f, -0.16);
                const pl = project(st.eye.map((x, k) => x + gl[k] * G)), pr = project(st.eye.map((x, k) => x + gr[k] * G));
                const g = ctx.createLinearGradient(e[0], e[1], (pl[0] + pr[0]) / 2, (pl[1] + pr[1]) / 2);
                g.addColorStop(0, 'rgba(184,216,255,0.2)'); g.addColorStop(1, 'rgba(184,216,255,0)');
                ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(e[0], e[1]); ctx.lineTo(pl[0], pl[1]); ctx.lineTo(pr[0], pr[1]); ctx.closePath(); ctx.fill();
            }
        }
        ctx.restore();
    }
    function objectTag(st, points, dt) {
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (const p of points) { const q = project(p); if (!q[2]) continue; x0 = Math.min(x0, q[0]); y0 = Math.min(y0, q[1]); x1 = Math.max(x1, q[0]); y1 = Math.max(y1, q[1]); }
        if (!Number.isFinite(x0)) return;
        const a0 = smooth('obj-0', x0 - 5, y0 - 5, dt), a1 = smooth('obj-1', x1 + 5, y1 + 5, dt), dir = st.right ? -1 : 1;
        ctx.save();
        corners(ctx, a0.x, a0.y, a1.x, a1.y, 8, S.accent, 1.8);
        const lx = dir < 0 ? a0.x : a1.x, ly = a1.y, ex = lx + dir * 34, ey = ly + 22;
        ctx.strokeStyle = S.accent; ctx.lineWidth = 1.25; ctx.beginPath(); ctx.moveTo(lx, ly); ctx.lineTo(ex, ey); ctx.stroke();
        const t = (st.object.label || 'Held object').toUpperCase();
        ctx.font = `600 11.5px ${S.mono}`; const tw = spacedWidth(ctx, t, 1.6) + 16, bx = dir < 0 ? ex - tw : ex;
        roundRect(ctx, bx, ey - 10, tw, 20, 4); ctx.fillStyle = S.accent; ctx.fill(); ctx.fillStyle = '#000000'; ctx.textAlign = 'left'; spaced(ctx, t, bx + 8, ey + 4, 1.6);
        if (Number.isFinite(st.object.confidence)) {
            ctx.shadowColor = 'rgba(0,0,0,0.75)'; ctx.shadowBlur = 10; ctx.font = `500 10.5px ${S.mono}`; ctx.fillStyle = '#D4D4D4';
            const c = `${Math.round(st.object.confidence * 100)}%`, cw = ctx.measureText(c).width; ctx.fillText(c, dir < 0 ? bx - cw - 6 : bx + tw + 6, ey + 4);
        }
        ctx.restore();
    }
    function showCard(st, who, houseLine, anchor, w) {
        if (!cardOpen || !anchor) { card.style.display = 'none'; return; }
        const rows = cardRows(who, st, houseLine), esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
        const html = `<div style="font:600 15px/1.3 ${S.sans};color:#fff">${esc(rows.name)}${rows.confidence ? ` <span style="font:500 9.5px ${S.mono};color:#8F8F8F;border:1px solid rgba(255,255,255,.18);border-radius:5px;padding:1px 6px;margin-left:6px;vertical-align:2px">${esc(rows.confidence)}</span>` : ''}</div>`
            + (rows.posture ? `<div>${esc(rows.posture)}</div>` : '') + (rows.activity ? `<div style="color:${S.dim}">${esc(rows.activity)}</div>` : '')
            + (rows.house ? `<div style="height:1px;background:rgba(255,255,255,.08);margin:8px 0 7px"></div><div><span style="font:500 9.5px ${S.mono};letter-spacing:.2em;color:#8F8F8F;margin-right:10px">HOUSE</span><span style="color:${S.ice}">${esc(rows.house)}</span></div>` : '');
        if (card.innerHTML !== html) card.innerHTML = html;
        card.style.display = 'block';
        const cw = card.offsetWidth || 224, ch = card.offsetHeight || 110, dir = st.right ? 1 : -1;
        const x = Math.max(8, Math.min(w - cw - 8, anchor[0] + (dir > 0 ? 40 : -40 - cw))), y = Math.max(8, anchor[1] - ch - 24);
        card.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
    }

    function clear() {
        hit = null; shown = null; tapStart = null; cardOpen = false;
        card.style.display = 'none'; card.textContent = '';
        state.reset(); smooth = smoother();
        ctx.resetTransform(); ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    function tick(dt) {
        if (disposed) return;
        const w = host.clientWidth, h = host.clientHeight, dpr = Math.min(2, doc.defaultView?.devicePixelRatio || 1);
        if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
        const snap = layer.snapshot(), J = snap.joints;
        if (!snap.centroid || snap.presence < 0.05 || !(w > 0 && h > 0)) { hit = null; card.style.display = 'none'; shown = null; return; }
        parent.updateWorldMatrix(true, false); camera.updateMatrixWorld();
        const anchor = snap.torso ? snap.torso.shoulder || snap.centroid
            : J.nose && J.left_ear && J.right_ear ? [(J.left_ear[0] + J.right_ear[0]) / 2, (J.left_ear[1] + J.right_ear[1]) / 2, Math.max(J.left_ear[2], J.right_ear[2]) + 0.17]
            : [snap.centroid[0], snap.centroid[1], snap.centroid[2] + 0.75];
        const screenAnchor = project(anchor);
        v.set(...snap.centroid); parent.localToWorld(v); const cw = new THREE.Vector3(); camera.getWorldPosition(cw);
        const zoomT = zoomRatio(cw.distanceTo(v));
        const st = state.step({ now: now(), dt, joints: J, centroid: snap.centroid, torso: snap.torso,
            activity: activity(), object: snap.object, headScreenX: screenAnchor[0], width: w, zoomT });
        shown = st;
        const who = person(), houseLine = house(), lines = captionLines(who, st, houseLine);
        ctx.globalAlpha = Math.min(1, snap.presence * 1.2);
        ring(st, snap.centroid, dt);
        hit = screenAnchor[2] ? caption(st, lines, screenAnchor, w, h, dt, zoomT) : null;
        if (st.object && st.detail >= 1 && snap.objectPoints.length) objectTag(st, snap.objectPoints, dt);
        ctx.globalAlpha = 1;
        showCard(st, who, houseLine, hit?.[0], w);
    }

    if (animate && typeof requestAnimationFrame === 'function') {
        const frame = time => { if (disposed) return; tick(last == null ? 0 : (time - last) / 1000); last = time; rafId = requestAnimationFrame(frame); };
        rafId = requestAnimationFrame(frame);
    }
    return {
        tick,
        clear,
        state: () => shown && { ...shown },
        cardOpen: () => cardOpen,
        setCardOpen(open) { cardOpen = !!open; },
        dispose() {
            if (disposed) return;
            disposed = true;
            if (rafId != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(rafId);
            host.removeEventListener('pointerdown', down); host.removeEventListener('pointerup', up);
            canvas.remove(); card.remove();
        },
    };
}
