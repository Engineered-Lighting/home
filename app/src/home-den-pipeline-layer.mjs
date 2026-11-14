/* The den's live pipeline drawn through the room (docs/design/den-inference/SPEC.md, "Pipeline through the room").
 *
 * The systems with no place in the room ride a rail along the canvas's top edge: the camera, Frigate's occupancy
 * gate, den-live's perception, Qwen and the lighting coordinator. Wires run along the rail and drop into the room
 * where the data has a place: from perception to each person's floor marker, and from the coordinator to where the
 * spot's beam lands (its target_m). The loop reads camera → machines → back into the room as light.
 * - Every value is den-live's own: health, the people event's tracks and activity, the applied spot snapshot. A field
 *   den-live doesn't send yet (health.occupancy) is read when present; until then the occupancy card shows Qwen's gate.
 * - A wire brightens only when a record has just gone through it (a people event, a new Qwen answer, a change in the
 *   spot), and fades over WIRE_HOT_MS. Nothing moves when nothing arrives. Reduced motion keeps the brightening, without
 *   the travelling dot.
 * - Tapping a card opens its values in a glass popover; Escape or a second tap closes it.
 * - The Pipeline toggle turns it all on and off. On, the rail asks the room to fit below it
 *   (data-apt-view-inset="top", which the apartment's fit reads), and the room eases down and smaller over FIT_MS while
 *   the rail and wires fade in; off, the room eases back up to its full canvas and they fade out. The choice is kept per
 *   browser.
 * - The look is the Perception Lab's graph (design v8): a dark card per system with a hairline border, its name and a
 *   role badge, one readable value, and a status line in the Lab's own state words (Changed, with a brief highlight;
 *   Current; Stale; Unavailable; Idle).
 * - Text goes in with textContent. Role names, not host names: the page shows what each system does. */
import * as THREE from 'three';
import { answerOf, identityOf } from './home-den-inference-model.mjs';

const SVG = 'http://www.w3.org/2000/svg';
const WIRE_HOT_MS = 1100, STALE_S = 3, MEASURE_MS = 250, COMPACT_PX = 640, FIT_MS = 450, DOT_R = 4.5, COMET_PX = 26;
const STORE_KEY = 'home.den.pipeline';
const stored = () => { try { return localStorage.getItem(STORE_KEY) === 'on'; } catch (_) { return false; } };
const store = on => { try { localStorage.setItem(STORE_KEY, on ? 'on' : 'off'); } catch (_) {} };
const NODES = Object.freeze([
    { id: 'occupancy', role: 'Occupancy', system: 'Frigate' },
    { id: 'camera', role: 'Camera', system: 'OAK-D Pro' },
    { id: 'perception', role: 'Perception', system: 'den-live' },
    { id: 'qwen', role: 'Activity', system: 'Qwen' },
    { id: 'coordinator', role: 'Light', system: 'Coordinator' },
]);
const RAIL_WIRES = Object.freeze([['camera', 'perception'], ['perception', 'qwen'], ['qwen', 'coordinator']]);   // the camera's line; Frigate gates Qwen
const REASON = Object.freeze({ task: 'Lit for close-up work', pre_aim: 'Aimed, dark', waiting_for_task: 'Waiting for close-up work',
    waiting_for_tracking: 'Nobody tracked', task_released: 'Fading out', stopped: 'Stop holds', off: 'Automation off' });
const finite = value => typeof value === 'number' && Number.isFinite(value);
const ms = value => finite(value) ? `${Math.round(value)} ms` : '—';
const secs = value => finite(value) ? (value < 10 ? `${value.toFixed(1)} s` : `${Math.round(value)} s`) : '—';

/* What each card says, from one people event (sinceS seconds old) and the applied spot snapshot. Pure. */
export function pipelineCards(event, sinceS, spot, names = {}) {
    const live = !!event && sinceS <= STALE_S;
    const h = event?.health || {}, q = h.qwen || null, t = h.timings_ms || {};
    const tracks = Array.isArray(event?.tracks) ? event.tracks : [];
    const dark = event?.camera?.why === 'too dark' || event?.mode === 'none';
    const cards = {};
    cards.camera = !event ? { state: 'off', value: 'No stream', sub: '' }
        : !live ? { state: 'attention', value: 'Stream stopped', sub: `no event for ${Math.floor(sinceS)} s` }
        : dark ? { state: 'attention', value: 'Too dark', sub: 'infrared is not on yet' }
        : { state: 'live', value: finite(h.fps_in) ? `${h.fps_in.toFixed(1)} fps` : 'Live',
            sub: finite(h.transit_s_median) ? `${ms(h.transit_s_median * 1000)} away` : '' };
    const occ = h.occupancy;   // proposed: {effective: on|off|unknown, why}
    if (occ && typeof occ === 'object' && typeof occ.effective === 'string')
        cards.occupancy = { state: !live ? 'attention' : occ.effective === 'on' ? 'live' : occ.effective === 'off' ? 'idle' : 'attention',
            value: occ.effective === 'on' ? 'Occupied' : occ.effective === 'off' ? 'Empty' : 'Unknown', sub: '', why: occ.why ? String(occ.why).slice(0, 80) : '' };
    else if (q) cards.occupancy = { state: !live ? 'attention' : q.state === 'off' ? 'idle' : 'live',
        value: q.state === 'off' ? 'Gate closed' : 'Gate open', sub: '', why: q.state === 'off' && q.why ? String(q.why).slice(0, 80) : '', inferred: true };
    else cards.occupancy = { state: 'off', value: 'Not sent', sub: 'den-live without the ask' };
    cards.perception = !live ? { state: event ? 'attention' : 'off', value: event ? 'Stale' : '—', sub: '' }
        : { state: dark ? 'attention' : 'live', value: tracks.length === 0 ? 'No one' : tracks.length === 1 ? '1 person' : `${tracks.length} people`,
            sub: finite(h.lag_s_median) ? `${secs(h.lag_s_median + (h.transit_s_median || 0))} behind` : '' };
    let answer = null;
    for (const track of tracks) { const a = answerOf(track, event, sinceS); if (a && (!answer || a.age_s < answer.age_s)) answer = a; }
    cards.qwen = !q ? { state: 'off', value: 'Not asked', sub: '' }
        : answer && live ? { state: 'live', value: answer.words, sub: `${Math.ceil(answer.left_s)} s left`, life: answer.life }
        : { state: q.state === 'unavailable' ? 'attention' : q.state === 'asking' ? 'live' : 'idle',
            value: q.state === 'asking' ? 'Asking' : q.state === 'unavailable' ? 'Not answering' : 'Off',
            sub: q.state === 'off' ? String(q.why || '').slice(0, 40) : `${finite(q.asks_per_min) ? q.asks_per_min : 0} a minute` };
    if (!spot) cards.coordinator = { state: 'off', value: 'No snapshot', sub: 'the spot is dark' };
    else {
        const rows = Array.isArray(spot.fixtures) ? spot.fixtures : [];
        const lit = rows.find(row => row.on === true && finite(row.level) && row.level > 0);
        const aimed = lit || rows.find(row => Array.isArray(row.target_m) && (row.reason === 'pre_aim' || row.reason === 'task'));
        const subject = spot.subject ? tracks.find(track => track.track_id === spot.subject.track_id) : null;
        const who = subject ? identityOf(subject, event, sinceS).name : spot.subject ? 'a track no longer seen' : 'no one';
        const name = aimed ? (names[aimed.fixture_id] || 'Spot') : '';
        cards.coordinator = spot.stopped ? { state: 'attention', value: 'Stop', sub: 'every spot dark' }
            : lit ? { state: 'live', value: `${Math.round(lit.level * 100)}%`, sub: `for ${who}`, fixture: name, why: REASON[lit.reason] || lit.reason }
            : aimed ? { state: 'idle', value: 'Aimed', sub: `dark, for ${who}`, fixture: name, why: REASON[aimed.reason] || aimed.reason }
            : { state: 'idle', value: 'Dark', sub: (REASON[rows[0]?.reason] || 'waiting').toLowerCase() };
        cards.coordinator.target = (lit || aimed)?.target_m ?? null;
    }
    return cards;
}

export function attachDenPipeline({ engine, host, markers, names = () => ({}), reducedMotion = () => false, clock = () => performance.now(), toggle: ownToggle = true } = {}) {
    const camera = engine?.camera, parent = engine?.apartmentRoot, canvas = engine?.renderer?.domElement, doc = host?.ownerDocument;
    if (!camera || !parent || !canvas || !doc || typeof markers !== 'function') throw Error('den_pipeline_dependencies_required');
    const layer = doc.createElement('div');
    layer.className = 'home-den-pipeline';
    layer.dataset.denPipeline = '1';
    const wires = doc.createElementNS(SVG, 'svg');
    wires.setAttribute('class', 'home-den-pipeline-wires');
    wires.setAttribute('aria-hidden', 'true');
    const defs = `<defs><radialGradient id="home-den-pipeline-bloom"><stop offset="0" stop-color="#ffddb0" stop-opacity=".55"/>`
        + `<stop offset="1" stop-color="#ffddb0" stop-opacity="0"/></radialGradient></defs>`;
    const rail = doc.createElement('div');
    rail.className = 'home-den-pipeline-rail';
    rail.setAttribute('role', 'group');
    rail.setAttribute('aria-label', 'The den’s live pipeline');
    const pop = doc.createElement('div');
    pop.className = 'home-den-pipeline-pop';
    pop.hidden = true;
    pop.setAttribute('role', 'dialog');
    const toggle = doc.createElement('button');
    toggle.type = 'button';
    toggle.className = 'home-den-pipeline-toggle';
    toggle.textContent = 'Pipeline';
    toggle.title = 'Show how the den\u2019s data reaches the light';
    toggle.hidden = !ownToggle;   // the den's capsule carries the switch when it's there
    const legend = doc.createElement('p');
    legend.className = 'home-den-pipeline-legend';
    legend.textContent = '';
    legend.hidden = true;
    layer.append(wires, rail, pop, toggle, legend);
    host.appendChild(layer);

    const cards = new Map();
    for (const node of NODES) {
        const el = doc.createElement('button');
        el.type = 'button';
        el.className = 'home-den-pipeline-card';
        el.dataset.node = node.id;
        const top = doc.createElement('span'); top.className = 'home-den-pipeline-role';
        const dot = doc.createElement('i'); dot.className = 'home-den-pipeline-dot'; dot.setAttribute('aria-hidden', 'true');
        const sys = doc.createElement('span'); sys.className = 'home-den-pipeline-system'; sys.textContent = node.system;
        const badge = doc.createElement('span'); badge.className = 'home-den-pipeline-badge'; badge.textContent = node.role;
        top.append(sys, badge);
        el.title = node.role;
        const value = doc.createElement('span'); value.className = 'home-den-pipeline-value';
        const sub = doc.createElement('span'); sub.className = 'home-den-pipeline-sub';
        const stateWord = doc.createElement('b'); stateWord.className = 'home-den-pipeline-state';
        const subText = doc.createElement('span');
        sub.append(dot, stateWord, subText);
        el.append(top, value, sub);
        el.addEventListener('click', event => { event.stopPropagation(); open = open === node.id ? null : node.id; renderPop(); });
        rail.appendChild(el);
        cards.set(node.id, { el, value, sub: subText, state: stateWord, node, changedAt: -Infinity });
    }
    const onKey = event => { if (event.key === 'Escape' && open) { open = null; renderPop(); } };
    const onDown = event => { if (open && !layer.contains(event.target)) { open = null; renderPop(); } };
    doc.addEventListener('keydown', onKey);
    doc.addEventListener('pointerdown', onDown, true);

    let latest = null, latestAt = 0, spot = null, model = {}, open = null, disposed = false, raf = null;
    let lastAnswerAt = null, lastSpotKey = null, box = null, measuredAt = -Infinity, on = false, fit = null;
    /* The room's top inset eases to the rail's bottom (on) or to 0 (off); the rest of the insets stay the shell's. */
    function insetTarget() {
        if (!on) return 0;
        const c = canvas.getBoundingClientRect(), r = rail.getBoundingClientRect();
        return r.height > 0 ? Math.max(0, Math.round(r.bottom - c.top + 12)) : 0;
    }
    function fitTo(target) {
        if (typeof engine.setViewInsets !== 'function') return;
        const from = engine.viewInsets?.()?.top ?? 0;
        fit = { from, target, start: clock(), duration: reducedMotion() ? 0 : FIT_MS };
    }
    function stepFit() {
        if (!fit) return;
        const u = fit.duration ? Math.min(1, (clock() - fit.start) / fit.duration) : 1, e = u * u * (3 - 2 * u);
        const insets = engine.viewInsets?.() || { top: 0, right: 0, bottom: 0, left: 0 };
        engine.setViewInsets({ ...insets, top: Math.round(fit.from + (fit.target - fit.from) * e) });
        if (u >= 1) fit = null;
    }
    function setOn(value, { remember = true } = {}) {
        on = value === true;
        layer.dataset.on = on ? '1' : '0';
        toggle.setAttribute('aria-pressed', String(on));
        if (on) rail.dataset.aptViewInset = 'top'; else delete rail.dataset.aptViewInset;
        if (!on) { open = null; renderPop(); }
        measuredAt = -Infinity;
        if (remember) store(on);
        // the rail's place is set in frame(); give it one frame before measuring where the room should start
        requestAnimationFrame(() => { if (!disposed) fitTo(insetTarget()); });
    }
    toggle.addEventListener('click', event => { event.stopPropagation(); setOn(!on); });
    const hot = new Map();   // wire id -> clock() when a record last went through it
    const heat = id => hot.set(id, clock());
    const resized = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { measuredAt = -Infinity; }) : null;
    resized?.observe(host);

    function measure() {
        if (box && clock() - measuredAt < MEASURE_MS) return box;
        const c = canvas.getBoundingClientRect(), h = host.getBoundingClientRect();
        const rel = r => ({ left: r.left - h.left, right: r.right - h.left, top: r.top - h.top, bottom: r.bottom - h.top, width: r.width, height: r.height });
        const chip = host.querySelector('[data-den-live-chip]')?.getBoundingClientRect();
        const labels = host.querySelector('.home-den-labels-toggle')?.getBoundingClientRect();
        box = { canvas: rel(c), w: h.width, h: h.height, chip: chip && chip.width > 0 ? rel(chip) : null,
            labels: labels && labels.width > 0 ? rel(labels) : null,
            cards: new Map([...cards].map(([id, c2]) => [id, rel(c2.el.getBoundingClientRect())])) };
        measuredAt = clock();
        return box;
    }
    const v = new THREE.Vector3();
    function project(p) {
        v.set(p[0], p[1], p[2]); parent.localToWorld(v); v.project(camera);
        const c = box.canvas;
        return { x: c.left + (v.x + 1) / 2 * c.width, y: c.top + (1 - v.y) / 2 * c.height, visible: v.z > -1 && v.z < 1 };
    }

    function render() {
        const since = latest ? (clock() - latestAt) / 1000 : Infinity;
        model = pipelineCards(latest, since, spot, names() || {});
        for (const [id, c] of cards) {
            const m = model[id] || { state: 'off', value: '—', sub: '' };
            if (c.el.dataset.state !== m.state) c.el.dataset.state = m.state;
            if (c.value.textContent !== m.value) { if (c.value.textContent) c.changedAt = clock(); c.value.textContent = m.value; }
            if (c.sub.textContent !== m.sub) c.sub.textContent = m.sub;
            const changed = m.state === 'live' && clock() - c.changedAt < 1500;
            const word = m.state === 'live' ? (changed ? 'Changed' : 'Current') : m.state === 'attention' ? 'Stale' : m.state === 'idle' ? 'Idle' : 'Unavailable';
            if (c.state.textContent !== word) c.state.textContent = word;
            if (c.el.dataset.changed !== String(changed)) c.el.dataset.changed = String(changed);
            const label = `${c.node.role}, ${c.node.system}: ${m.value}${m.sub ? `, ${m.sub}` : ''}`;
            if (c.el.getAttribute('aria-label') !== label) c.el.setAttribute('aria-label', label);
        }
        if (open) renderPop();
    }
    function renderPop() {
        if (!open) { pop.hidden = true; for (const c of cards.values()) c.el.removeAttribute('aria-expanded'); return; }
        const c = cards.get(open), m = model[open] || {}, r = box?.cards.get(open);
        pop.hidden = false;
        c.el.setAttribute('aria-expanded', 'true');
        const rows = popRows(open);
        if (pop._key !== JSON.stringify(rows)) {
            pop._key = JSON.stringify(rows);
            pop.textContent = '';
            const h = doc.createElement('p'); h.className = 'home-den-pipeline-pop-title'; h.textContent = `${c.node.role} · ${c.node.system}`;
            const dl = doc.createElement('dl');
            for (const [k, val] of rows) { const dt = doc.createElement('dt'); dt.textContent = k; const dd = doc.createElement('dd'); dd.textContent = val; dl.append(dt, dd); }
            pop.append(h, dl);
            if (m.inferred) { const n = doc.createElement('p'); n.className = 'home-den-pipeline-pop-note'; n.textContent = 'den-live doesn’t send Frigate’s occupancy yet; this is Qwen’s gate.'; pop.append(n); }
        }
        if (r && box) {
            const left = Math.max(8, Math.min(box.w - 268, r.left));
            pop.style.transform = `translate(${Math.round(left)}px, ${Math.round(r.bottom + 8)}px)`;
        }
    }
    function popRows(id) {
        const e = latest, h = e?.health || {}, t = h.timings_ms || {}, q = h.qwen || {};
        const since = e ? (clock() - latestAt) / 1000 : null;
        const p = (k, x) => (t[k] ? `${ms(t[k].p50)} p50 · ${ms(t[k].p95)} p95` : '—');
        if (id === 'camera') return [['Frames in', finite(h.fps_in) ? `${h.fps_in.toFixed(2)} a second` : '—'], ['Fetch', p('fetch')], ['Transit', finite(h.transit_s_median) ? ms(h.transit_s_median * 1000) : '—'],
            ['Dropped', finite(h.dropped_per_min) ? `${h.dropped_per_min} a minute` : '—'], ['Mode', e?.mode ?? '—'], ['Last event', since == null ? '—' : `${secs(since)} ago`]];
        if (id === 'occupancy') return h.occupancy ? [['Frigate', String(h.occupancy.effective)], ['Why', String(h.occupancy.why || '—')], ['Gates', 'whether den-live asks Qwen']]
            : [['Qwen gate', q.state ?? '—'], ['Why', q.why ?? '—']];
        if (id === 'perception') return [['People', String((e?.tracks || []).length)], ['Detector', p('detector')], ['Lift', p('lift')], ['Track', p('track')],
            ['Behind', finite(h.lag_s_median) ? secs(h.lag_s_median + (h.transit_s_median || 0)) : '—'], ['Status', e?.perception_status ?? '—']];
        if (id === 'qwen') return [['State', q.state ?? '—'], ['Why', q.why ?? '—'], ['Reply', secs(q.reply_s_median)], ['Asks', finite(q.asks_per_min) ? `${q.asks_per_min} a minute` : '—'],
            ['Answer', model.qwen?.value ?? '—']];
        const rows = Array.isArray(spot?.fixtures) ? spot.fixtures : [];
        return [['Light', `${model.coordinator?.value ?? '—'}${model.coordinator?.fixture ? ` · ${model.coordinator.fixture}` : ''}`], ['Why', model.coordinator?.why ?? '—'], ['For', (model.coordinator?.sub ?? '—').replace(/^for /, '')],
            ...rows.map(row => [names()?.[row.fixture_id] || 'Spot', `${row.on ? `${Math.round(row.level * 100)}%` : 'dark'} · ${String(row.reason).replace(/_/g, ' ')}`])];
    }

    /* From a station's dot straight down its stem, past its words, then one easy curve to where the data lands. */
    function stem(from, at) {
        const x = from.x, y0 = from.bottom, y1 = from.bottom + 8, m = Math.max(24, (at.y - y1) * 0.55);
        return `M${x.toFixed(1)} ${y0.toFixed(1)} L${x.toFixed(1)} ${y1.toFixed(1)} C ${x.toFixed(1)} ${(y1 + m).toFixed(1)}, ${at.x.toFixed(1)} ${(at.y - m).toFixed(1)}, ${at.x.toFixed(1)} ${at.y.toFixed(1)}`;
    }
    function frame() {
        if (disposed) return;
        const b = measure();
        const compact = b.w < COMPACT_PX;
        if (layer.dataset.compact !== (compact ? '1' : '0')) { layer.dataset.compact = compact ? '1' : '0'; measuredAt = -Infinity; }
        // the rail sits beside the status chip on a wide view, under it on a narrow one
        // The toggle sits beside the status chip (under it on a narrow view). The rail takes its own row under the top
        // controls, the canvas's full width, so each card has room for its value; beside the chip only while the chip
        // is open tall (its coordinator section), so the rail never covers it.
        const chip = b.chip, labels = b.labels;
        const tTop = compact ? (chip ? chip.bottom + 8 : 12) : 12, tLeft = !compact && chip ? chip.right + 12 : 12;
        const tw = ownToggle ? toggle.offsetWidth || 80 : 0, th = ownToggle ? toggle.offsetHeight || 30 : 0;
        const chipTall = !!chip && chip.height > 120;
        const rowTop = Math.max(tTop + th, chip && !chipTall ? chip.bottom : 0, labels ? labels.bottom : 0) + 10;
        const top = compact ? (ownToggle ? tTop + th + 6 : tTop) : chipTall ? tTop + th + 8 : rowTop;
        const left = compact || !chipTall ? 12 : chip.right + 12;
        const place = `${Math.round(left)}px ${Math.round(top)}px ${Math.round(tLeft)}px ${Math.round(tTop)}px`;
        if (rail.dataset.at !== place) {
            rail.dataset.at = place; rail.style.left = `${Math.round(left)}px`; rail.style.top = `${Math.round(top)}px`; rail.style.right = '12px';
            toggle.style.left = `${Math.round(tLeft)}px`; toggle.style.top = `${Math.round(tTop)}px`; measuredAt = -Infinity;
            legend.style.right = '14px'; legend.style.top = `${Math.round(top - 16)}px`;
            if (on && !fit) requestAnimationFrame(() => { if (!disposed && on) fitTo(insetTarget()); });   // the rail moved: refit below it
        }
        stepFit();
        if (!on) { if (wires._key !== 'off') { wires._key = 'off'; wires.textContent = ''; } raf = requestAnimationFrame(frame); return; }
        wires.setAttribute('viewBox', `0 0 ${Math.round(b.w)} ${Math.round(b.h)}`);
        const now = clock(), reduce = reducedMotion(), paths = [];
        const R = id => b.cards.get(id);
        const D = id => { const r = R(id); return r ? { x: r.left + r.width / 2, y: r.top + r.height / 2, left: r.left, right: r.right, top: r.top, bottom: r.bottom } : null; };
        const heatOf = id => { const at = hot.get(id); return at == null ? 0 : Math.max(0, 1 - (now - at) / WIRE_HOT_MS); };
        const push = (id, d, kind, heatValue = heatOf(id)) => paths.push({ id, d, kind, heat: heatValue });
        for (const [a, z] of RAIL_WIRES) {
            const A = D(a), Z = D(z);
            if (A && Z) {
                const x1 = A.right, x2 = Z.left, k = Math.max(8, (x2 - x1) * 0.5);
                push(`${a}>${z}`, `M${x1.toFixed(1)} ${A.y.toFixed(1)} C ${(x1 + k).toFixed(1)} ${A.y.toFixed(1)}, ${(x2 - k).toFixed(1)} ${Z.y.toFixed(1)}, ${x2.toFixed(1)} ${Z.y.toFixed(1)}`, 'flow');
                paths.push({ id: `port:${a}`, ring: { x: x1, y: A.y }, kind: 'port', heat: 0 }, { id: `port:${z}`, ring: { x: x2, y: Z.y }, kind: 'port', heat: 0 });
            }
        }
        { const A = D('occupancy'), Z = D('qwen');
          if (A && Z) push('occupancy>qwen', `M${A.x.toFixed(1)} ${A.top.toFixed(1)} C ${A.x.toFixed(1)} ${(A.top - 16).toFixed(1)}, ${Z.x.toFixed(1)} ${(Z.top - 16).toFixed(1)}, ${Z.x.toFixed(1)} ${Z.top.toFixed(1)}`,
            'gate', model.occupancy?.state === 'live' ? 0.25 : 0); }
        const live = latest && (now - latestAt) / 1000 <= STALE_S;
        if (live && b.canvas.width > 0) {
            parent.updateWorldMatrix(true, false); camera.updateMatrixWorld();
            const P = D('perception');
            for (const m of markers()) {
                const at = project([m.xy_m[0], m.xy_m[1], 0.02]);
                if (!at.visible || !P) continue;
                push(`perception>${m.track_id}`, stem(P, at), 'room', heatOf('perception>room'));
                paths.push({ id: `ring:${m.track_id}`, ring: at, kind: 'anchor', heat: heatOf('perception>room') });
            }
            const C = D('coordinator'), target = model.coordinator?.target;
            if (C && Array.isArray(target)) {
                const at = project(target);
                if (at.visible) {
                    push('coordinator>light', stem(C, at), model.coordinator.state === 'live' ? 'light' : 'room');
                    paths.push({ id: 'target', ring: at, kind: model.coordinator.state === 'live' ? 'light' : 'anchor', heat: heatOf('coordinator>light') });
                }
            }
        }
        draw(paths, reduce, now);
        raf = requestAnimationFrame(frame);
    }
    function draw(paths, reduce, now) {
        const key = paths.map(p => `${p.id}:${p.d || `${Math.round(p.ring.x)},${Math.round(p.ring.y)}`}:${p.heat.toFixed(2)}`).join('|');
        if (wires._key === key) return;   // nothing moved and nothing cooled: leave the DOM alone
        wires._key = key;
        wires.innerHTML = defs;
        for (const p of paths) {
            if (p.ring) {
                if (p.kind === 'light') {
                    const halo = doc.createElementNS(SVG, 'circle');
                    halo.setAttribute('cx', p.ring.x.toFixed(1)); halo.setAttribute('cy', p.ring.y.toFixed(1)); halo.setAttribute('r', '14');
                    halo.setAttribute('class', 'home-den-pipeline-halo'); halo.style.setProperty('--heat', p.heat.toFixed(2));
                    wires.appendChild(halo);
                }
                const c = doc.createElementNS(SVG, 'circle');
                c.setAttribute('cx', p.ring.x.toFixed(1)); c.setAttribute('cy', p.ring.y.toFixed(1)); c.setAttribute('r', p.kind === 'light' ? '3.5' : p.kind === 'port' ? '2.6' : '4');
                c.setAttribute('class', `home-den-pipeline-anchor is-${p.kind}`); c.style.setProperty('--heat', p.heat.toFixed(2));
                wires.appendChild(c);
                continue;
            }
            const path = doc.createElementNS(SVG, 'path');
            path.setAttribute('d', p.d); path.setAttribute('class', `home-den-pipeline-wire is-${p.kind}`);
            path.style.setProperty('--heat', p.heat.toFixed(2));
            wires.appendChild(path);
            if (!reduce && p.heat > 0 && p.kind !== 'gate') {
                const len = path.getTotalLength?.() || 0, head = len * (1 - p.heat) * 1.15;
                if (len > 0 && head < len + COMET_PX) {
                    const comet = doc.createElementNS(SVG, 'path');
                    comet.setAttribute('d', p.d);
                    comet.setAttribute('class', `home-den-pipeline-comet is-${p.kind}`);
                    comet.setAttribute('stroke-dasharray', `${COMET_PX} ${Math.ceil(len + COMET_PX * 2)}`);
                    comet.setAttribute('stroke-dashoffset', (COMET_PX - head).toFixed(1));
                    comet.style.setProperty('--heat', p.heat.toFixed(2));
                    wires.appendChild(comet);
                }
            }
        }
    }
    setOn(stored(), { remember: false });
    raf = requestAnimationFrame(frame);
    const tick = setInterval(() => { if (!disposed) render(); }, 500);   // ages tick over between events

    return {
        update(event, receivedAt = clock()) {
            if (disposed || !event || !Array.isArray(event.tracks)) return;
            latest = event; latestAt = receivedAt;
            heat('camera>perception'); heat('perception>room');
            const q = event.health?.qwen, newest = event.tracks.map(t => t.activity?.at).filter(finite).sort((a, b) => b - a)[0];
            const answered = finite(newest) ? newest : finite(q?.last_reply_at) ? q.last_reply_at : null;
            if (answered != null && answered !== lastAnswerAt) { if (lastAnswerAt != null) { heat('perception>qwen'); heat('qwen>coordinator'); } lastAnswerAt = answered; }
            render();
        },
        setSpot(value) {
            spot = value || null;
            const rows = Array.isArray(spot?.fixtures) ? spot.fixtures : [];
            const key = JSON.stringify([spot?.stopped, spot?.subject?.request_id, rows.map(r => [r.on, Math.round((r.level || 0) * 20), r.reason, r.motion_phase])]);
            if (key !== lastSpotKey) { if (lastSpotKey != null) heat('coordinator>light'); lastSpotKey = key; }
            render();
        },
        clear() { latest = null; spot = null; render(); },
        setOn: value => setOn(value),
        status: () => ({ on, open, cards: Object.fromEntries(NODES.map(n => [n.id, { state: model[n.id]?.state ?? null, value: model[n.id]?.value ?? null, sub: model[n.id]?.sub ?? null }])),
            wires: [...wires.querySelectorAll('path')].map(p => p.getAttribute('class').replace('home-den-pipeline-wire is-', '')) }),
        dispose() {
            if (disposed) return;
            disposed = true;
            if (raf != null) cancelAnimationFrame(raf);
            clearInterval(tick); resized?.disconnect();
            if (on && typeof engine.setViewInsets === 'function') engine.setViewInsets({ ...(engine.viewInsets?.() || {}), top: 0 });
            doc.removeEventListener('keydown', onKey); doc.removeEventListener('pointerdown', onDown, true);
            layer.remove();
        },
    };
}
