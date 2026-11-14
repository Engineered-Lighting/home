/* The den's live caption in Home's apartment view (direction A of docs/design/den-inference/SPEC.md).
 *
 * One caption per tracked person, beside them in the room: who (a name tag, never a face), Qwen's current word with a
 * ring that drains over the answer's life, what they hold, what Qwen saw (up close), and what the light is doing for
 * them. home-den-inference-model.mjs decides every word; this file places and draws them.
 * - Anchor: the measured body's shoulders when the body layer holds this person, else the floor marker.
 * - Text changes only when the data does (an event lands, an answer ages a whole second, the zoom crosses a step);
 *   positions follow each frame on a 0.24 s smoother, and the caption keeps its side until the anchor passes 42% or
 *   66% of the width.
 * - A new answer flashes the word and runs a pulse down the leader from the person to the caption. Reduced motion
 *   turns both off.
 * - Presentation only: no network; whoever holds the streams calls update() and setSpot(). Text goes in with
 *   textContent. */
import * as THREE from 'three';
import { captionFor, detailFor } from './home-den-inference-model.mjs';
import { zoomRatio } from './home-3d/apartment-body-frame.mjs';

const SVG = 'http://www.w3.org/2000/svg';
const STALE_S = 3, SIDE = [0.42, 0.66], TAU_S = 0.24, PULSE_S = 0.7, MEASURE_MS = 250;

export function attachDenCaption({ engine, host, markers, body = () => null, names = () => ({}),
    reducedMotion = () => false, clock = () => performance.now() } = {}) {
    const camera = engine?.camera, parent = engine?.apartmentRoot, canvas = engine?.renderer?.domElement, doc = host?.ownerDocument;
    if (!camera || !parent || !canvas || !doc || typeof markers !== 'function') throw Error('den_caption_dependencies_required');
    const layer = doc.createElement('div');
    layer.className = 'home-den-caption-layer';
    layer.dataset.denCaption = '1';
    const leaders = doc.createElement('canvas');
    leaders.className = 'home-den-caption-leaders';
    leaders.setAttribute('aria-hidden', 'true');
    layer.appendChild(leaders);
    host.appendChild(layer);
    const lctx = leaders.getContext('2d');
    const captions = new Map(), v = new THREE.Vector3(), eye = new THREE.Vector3();
    let latest = null, latestAt = 0, spot = null, disposed = false, raf = null, last = null;

    /* The canvas's and the chip's boxes in the host. They're read on a resize and at most every MEASURE_MS, never in
     * between, so a frame that only moves captions forces no layout. */
    let box = null, measuredAt = -Infinity;
    const resized = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { measuredAt = -Infinity; }) : null;
    resized?.observe(host);
    function measure() {
        if (box && clock() - measuredAt < MEASURE_MS) return box;
        const c = canvas.getBoundingClientRect(), h = host.getBoundingClientRect();
        const chip = host.querySelector('[data-den-live-chip]')?.getBoundingClientRect();
        box = { x: c.left - h.left, y: c.top - h.top, width: c.width, height: c.height, w: h.width, h: h.height,
            chip: chip && chip.width > 0 ? { left: chip.left - h.left, right: chip.right - h.left, top: chip.top - h.top, bottom: chip.bottom - h.top } : null };
        measuredAt = clock();
        return box;
    }
    function project(p) {
        v.set(p[0], p[1], p[2]); parent.localToWorld(v);
        const distance = v.distanceTo(camera.getWorldPosition(eye));
        v.project(camera);
        return { x: box.x + (v.x + 1) / 2 * box.width, y: box.y + (1 - v.y) / 2 * box.height, visible: v.z < 1 && v.z > -1, distance };
    }

    function make(id) {
        const el = doc.createElement('div');
        el.className = 'home-den-caption';
        el.dataset.side = 'right';
        el.dataset.trackId = id;
        const part = (tag, cls, parentNode = el) => { const node = doc.createElement(tag); node.className = cls; parentNode.appendChild(node); return node; };
        const nameRow = part('p', 'home-den-caption-name');
        const name = part('span', 'home-den-caption-who', nameRow), prov = part('span', 'home-den-caption-prov', nameRow);
        const lineRow = part('p', 'home-den-caption-line');
        const ring = doc.createElementNS(SVG, 'svg');
        ring.setAttribute('class', 'home-den-caption-life'); ring.setAttribute('viewBox', '-9 -9 18 18'); ring.setAttribute('aria-hidden', 'true');
        const bg = doc.createElementNS(SVG, 'circle'); bg.setAttribute('r', '7'); bg.setAttribute('class', 'bg');
        const arc = doc.createElementNS(SVG, 'circle'); arc.setAttribute('r', '7'); arc.setAttribute('class', 'arc');
        arc.setAttribute('pathLength', '100'); arc.setAttribute('stroke-dasharray', '0 100');
        ring.append(bg, arc); lineRow.appendChild(ring);
        const word = part('span', 'home-den-caption-word', lineRow), age = part('span', 'home-den-caption-age', lineRow);
        const object = part('p', 'home-den-caption-object'), basis = part('p', 'home-den-caption-basis'), house = part('p', 'home-den-caption-house');
        const houseWords = part('span', 'home-den-caption-house-words', house), houseLevel = part('span', 'home-den-caption-house-level', house);
        const near = part('p', 'home-den-caption-near');
        layer.appendChild(el);
        const c = { el, name, prov, ring, arc, word, age, object, basis, house, houseWords, houseLevel, near,
            x: null, y: null, side: 'right', answerAt: null, pulseAt: -Infinity, opacity: 0, dash: '' };
        captions.set(id, c);
        return c;
    }
    const setText = (node, text) => { if (node.textContent === text) return false; node.textContent = text; return true; };
    const setData = (node, key, value) => { if (node.dataset[key] === value) return false; node.dataset[key] = value; return true; };   // no style churn when unchanged
    const show = (node, on) => { if (node.hidden !== on) return false; node.hidden = !on; return true; };

    function anchorFor(track, marker, bodySnap) {
        const [mx, my] = marker.xy_m;
        const c = bodySnap?.centroid;
        if (c && bodySnap.presence > 0.05 && (bodySnap.trackId === track.track_id || Math.hypot(c[0] - mx, c[1] - my) < 0.6)) {
            const s = bodySnap.torso?.shoulder;
            return { point: s ? [s[0], s[1], s[2] + 0.22] : [c[0], c[1], c[2] + 0.75], body: true };
        }
        return { point: [mx, my, 0.02], body: false };
    }

    function frame(dt, elapsed = dt) {
        const { w, h, chip: chipBox } = measure(), dpr = Math.min(2, doc.defaultView?.devicePixelRatio || 1);
        if (leaders.width !== Math.round(w * dpr) || leaders.height !== Math.round(h * dpr)) { leaders.width = Math.round(w * dpr); leaders.height = Math.round(h * dpr); }
        lctx.setTransform(dpr, 0, 0, dpr, 0, 0); lctx.clearRect(0, 0, w, h);
        const compactFlag = w < 520 ? '1' : '0';
        if (layer.dataset.compact !== compactFlag) layer.dataset.compact = compactFlag;
        const since = latest ? (clock() - latestAt) / 1000 : Infinity;
        const seen = new Set();
        if (latest && since <= STALE_S + 2 && w > 0 && h > 0) {
            parent.updateWorldMatrix(true, false); camera.updateMatrixWorld();
            const byId = new Map(markers().map(m => [m.track_id, m]));
            const bodySnap = body(), current = spot, fixtureNames = names() || {}, reduce = reducedMotion();
            for (const track of latest.tracks) {
                const marker = byId.get(track.track_id);
                if (!marker) continue;
                const { point, body: onBody } = anchorFor(track, marker, bodySnap);
                const at = project(point);
                if (!at.visible) continue;
                seen.add(track.track_id);
                const c = captions.get(track.track_id) || make(track.track_id);
                const model = captionFor({ event: latest, track, sinceEventS: since, spot: current, names: fixtureNames });
                const detail = detailFor(zoomRatio(at.distance));
                const compact = w < 520;
                if (render(c, model, detail, compact, since > STALE_S) || !c.size) c.size = [c.el.offsetWidth, c.el.offsetHeight];
                // place: keep the side until the anchor crosses 42% / 66% of the width; stay inside the view
                if (at.x < w * SIDE[0]) c.side = 'right'; else if (at.x > w * SIDE[1]) c.side = 'left';
                const gap = onBody ? (compact ? 22 : 34) : (compact ? 18 : 26), [ew, eh] = c.size;
                if (c.side === 'right' && at.x + gap + ew > w - 8 && at.x - gap - ew > 8) c.side = 'left';
                if (c.side === 'left' && at.x - gap - ew < 8 && at.x + gap + ew < w - 8) c.side = 'right';
                setData(c.el, 'side', c.side);
                const tx = c.side === 'right' ? at.x + gap : at.x - gap - ew, ty = at.y - (onBody ? eh * 0.35 : eh + 10);
                const k = reduce || c.x == null ? 1 : 1 - Math.exp(-Math.max(0, dt) / TAU_S);
                c.x = c.x == null ? tx : c.x + (tx - c.x) * k; c.y = c.y == null ? ty : c.y + (ty - c.y) * k;
                const left = Math.max(8, Math.min(w - ew - 8, c.x));
                let top = Math.max(8, Math.min(h - eh - 8, c.y));
                // never over the status chip: step below it when they'd overlap
                if (chipBox && left < chipBox.right + 6 && left + ew > chipBox.left - 6 && top < chipBox.bottom + 6 && top + eh > chipBox.top - 6)
                    top = Math.min(h - eh - 8, chipBox.bottom + 8);
                c.el.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
                c.opacity = Math.min(1, c.opacity + Math.max(0, elapsed) * 3);   // real time: a slow frame still finishes the fade
                c.el.style.opacity = String(c.opacity * (marker.coasting ? 0.6 : 1));
                // the leader, and the pulse when a new answer lands
                const lx = c.side === 'right' ? left - 4 : left + ew + 4, ly = top + Math.min(eh / 2, 30);
                lctx.globalAlpha = c.opacity * (since > STALE_S ? 0.4 : 1);
                lctx.strokeStyle = 'rgba(237, 237, 237, 0.4)'; lctx.lineWidth = 1;
                lctx.beginPath(); lctx.moveTo(at.x, at.y); lctx.lineTo(lx, ly); lctx.stroke();
                lctx.fillStyle = 'rgba(237, 237, 237, 0.85)'; lctx.beginPath(); lctx.arc(at.x, at.y, 2.2, 0, Math.PI * 2); lctx.fill();
                const p = (clock() - c.pulseAt) / 1000;
                if (!reduce && p >= 0 && p < PULSE_S) {
                    const u = p / PULSE_S, px = at.x + (lx - at.x) * u, py = at.y + (ly - at.y) * u;
                    const g = lctx.createRadialGradient(px, py, 0, px, py, 14);
                    g.addColorStop(0, `rgba(221, 234, 255, ${0.9 * (1 - u)})`); g.addColorStop(1, 'rgba(184, 216, 255, 0)');
                    lctx.fillStyle = g; lctx.beginPath(); lctx.arc(px, py, 14, 0, Math.PI * 2); lctx.fill();
                }
                lctx.globalAlpha = 1;
            }
        }
        for (const [id, c] of captions) if (!seen.has(id)) {
            c.opacity -= Math.max(0.05, elapsed) * 3;
            c.el.style.opacity = String(Math.max(0, c.opacity));
            if (c.opacity <= 0) { c.el.remove(); captions.delete(id); }
        }
    }

    function render(c, model, detail, compact, stale) {
        const el = c.el;
        let d = 0;   // whether anything that sizes the caption changed
        d |= setData(el, 'kind', model.identity.kind); d |= setData(el, 'strength', model.identity.strength); d |= setData(el, 'detail', String(detail));
        d |= setData(el, 'line', model.line.kind); d |= setData(el, 'stale', stale ? '1' : '0'); d |= setData(el, 'asking', model.line.asking ? '1' : '0');
        d |= setText(c.name, model.identity.name);
        const id = model.identity;
        d |= setText(c.prov, detail >= 2 ? (id.kind === 'household' ? `face ${id.score.toFixed(2)}${id.age_s != null ? ` · ${Math.round(id.age_s)} s ago` : ''}`
            : id.kind === 'guest' ? 'face seen · not household' : 'no face seen yet') : '');
        d |= setText(c.word, stale ? 'Stream stopped' : model.line.words);
        d |= setText(c.age, stale ? '' : model.line.detail || '');
        const life = model.line.life;
        d |= show(c.ring, life != null);
        const dash = `${((stale ? 0 : life || 0) * 100).toFixed(1)} 100`;   // the ring never changes the caption's size
        if (dash !== c.dash) { c.arc.setAttribute('stroke-dasharray', dash); c.dash = dash; }
        const answer = model.answer;
        if (answer && answer.at !== c.answerAt) {
            if (c.answerAt != null && !reducedMotion()) { c.pulseAt = clock(); c.word.classList.remove('is-new'); void c.word.offsetWidth; c.word.classList.add('is-new'); }
            c.answerAt = answer.at;
        } else if (!answer) c.answerAt = null;
        d |= show(c.object, !!answer?.object && (detail >= 1 || compact));
        d |= setText(c.object, answer?.object ? answer.object : '');
        d |= show(c.basis, !!answer?.basis.length && detail >= 2);
        d |= setText(c.basis, answer?.basis.length ? `Qwen saw ${answer.basis.join(' · ')}` : '');
        const house = model.house;
        d |= show(c.house, !!house && (detail >= 1 || compact));
        d |= setText(c.houseWords, house ? house.words : '');
        d |= setText(c.houseLevel, house && !house.dark && house.level != null ? `${Math.round(house.level * 100)}%${house.cct_k ? ` · ${house.cct_k} K` : ''}` : '');
        if (house) d |= setData(c.house, 'dark', house.dark ? '1' : '0');
        d |= show(c.near, detail === 1 && !compact && !!model.near);
        d |= setText(c.near, model.near || '');
        return d !== 0;
    }

    function loop(time) {
        if (disposed) return;
        const elapsed = last == null ? 0 : Math.max(0, (time - last) / 1000);
        frame(Math.min(0.1, elapsed), elapsed);
        last = time;
        raf = requestAnimationFrame(loop);
    }
    raf = requestAnimationFrame(loop);

    return {
        /* One people event this page accepted (same scan), and when it arrived on clock(). */
        update(event, receivedAt = clock()) { if (!disposed && event && Array.isArray(event.tracks)) { latest = event; latestAt = receivedAt; } },
        /* The applied spot snapshot (home-den-spot-playback's current()), or null. */
        setSpot(value) { spot = value || null; },
        clear() { latest = null; spot = null; },
        /* Read-only, for Home's browser tests. */
        status: () => [...captions.entries()].map(([track_id, c]) => ({ track_id, name: c.name.textContent, line: c.word.textContent,
            age: c.age.textContent, life: c.dash, object: c.object.hidden ? null : c.object.textContent,
            house: c.house.hidden ? null : c.houseWords.textContent, side: c.side, opacity: Number(c.el.style.opacity || 0) })),
        dispose() {
            if (disposed) return;
            disposed = true;
            if (raf != null) cancelAnimationFrame(raf);
            resized?.disconnect();
            layer.remove(); captions.clear();
        },
    };
}
