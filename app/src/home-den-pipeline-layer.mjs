/* The den's live pipeline drawn through the room (docs/design/den-inference/SPEC.md, "Pipeline through the room").
 *
 * The systems with no place in the room ride a rail along the canvas's top edge: the camera, Frigate's occupancy
 * gate, den-live's perception, Qwen and the lighting coordinator. Wires run along the rail and drop into the room
 * where the data has a place: from perception to each person's floor marker, and from the coordinator to where the
 * spot's beam lands (its target_m). The loop reads camera → machines → back into the room as light.
 * - Every value is den-live's own: health, the people event's tracks and activity, the applied spot snapshot. A field
 *   den-live doesn't send yet (health.occupancy) is read when present; until then the occupancy card says "Not reported".
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
 * - On a phone (a room under COMPACT_PX wide) the rail is four joined stations, camera → den-live → Qwen → light, and
 *   Frigate rides under Qwen as a small gate with its dashed line up into it. A card's values dock along the bottom of
 *   the room instead of over it. When the person turns the pipeline on, it asks Home's sheet down to peek
 *   (home-sheet-detent), so the room has the screen; when they turn it off, it puts back the height it found, unless
 *   they moved the sheet since. Only their toggle moves the sheet: a re-mount (a page load, an app switch, a hidden
 *   page) with the pipeline left on never does.
 * - Qwen's card drains a hairline meter over its answer's life, which ends when the answer expires.
 * - Text goes in with textContent. Role names, not host names: the page shows what each system does. */
import * as THREE from 'three';
import { FADE, answerOf, identityOf } from './home-den-inference-model.mjs';
import { normalizeDenModelStages, denModelStageLabel, denModelAdmissionLabel, denSourceClockInterval } from './home-den-model-runtime.mjs';

const SVG = 'http://www.w3.org/2000/svg';
const WIRE_HOT_MS = 1100, STALE_S = 3, MEASURE_MS = 250, COMPACT_PX = 640, FIT_MS = 450, DOT_R = 4.5, COMET_PX = 26;
const STORE_KEY = 'home.den.pipeline';
// The sheet's height from before the person turned the pipeline on. It outlives a re-mount (the den view disposes and
// re-attaches this layer whenever the room leaves the screen), so a later "off" can still put it back.
let sheetBefore = null;
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
    waiting_for_tracking: 'Nobody tracked', task_released: 'Fading out', task_ending: 'Letting go: the task ended',
    standby: 'Standby: aimed at the page, dark', stopped: 'Stop holds', off: 'Automation off' });
const finite = value => typeof value === 'number' && Number.isFinite(value);
const ms = value => finite(value) ? `${Math.round(value)} ms` : '—';
const secs = value => finite(value) ? (value < 10 ? `${value.toFixed(1)} s` : `${Math.round(value)} s`) : '—';
const reported = value => value === true ? 'Yes' : value === false ? 'No' : 'Unknown';
const freshnessWord = value => value === 'fresh' ? 'Fresh' : value === 'stale' ? 'Stale' : 'Unknown';
const INTENT_REASONS = Object.freeze({ intent_hint_absent: 'No hint offered', malformed_intent_hint: 'Malformed hint',
    invalid_clock: 'Clock unavailable', future_intent_hint: 'Future hint declined', capture_lease_expired: 'Capture lease expired',
    invalid_control_cutoff: 'Control timing unavailable', newer_control: 'Newer control state',
    current_binding_changed: 'Person or source binding changed', newer_measured_stop: 'Newer measured stop',
    newer_measured_turn: 'Newer measured turn', awaiting_held_out_admission: 'Awaiting held-out admission',
    request_id_conflict: 'Request identity conflict', request_identity_unavailable: 'Request identity unavailable',
    invalid_request_identity: 'Invalid request identity', older_intent_hint: 'Older hint declined', idempotent_retry: 'Original receipt retained' });
const own = (value, key) => value && typeof value === 'object' && Object.hasOwn(value, key) ? value[key] : undefined;
function runtimeOf(event, context = {}) {
    try {
        return normalizeDenModelStages(event?.health?.model_stages,
            { clock: context.sourceClock ?? null, receivedAt: context.receivedAt, now: context.now ?? context.receivedAt });
    } catch (_) { return null; } // Invalid optional metadata cannot interrupt legacy cards.
}
// The actual producer reports lighting_policy separately; its other model stages belong to perception.
const stageNode = stage => stage.role === 'lighting_policy' ? 'qwen' : 'perception';

function runtimeSummary(stages) {
    if (!stages.length) return '';
    const counts = new Map();
    for (const stage of stages) {
        const word = stage.admission_scope === 'bounded_trial' ? 'trial/evaluation'
                : stage.admission_scope === 'production' ? 'production admitted'
                    : stage.reason === 'planned' ? 'planned'
                        : stage.admission_scope === 'not_admitted' ? 'not admitted' : 'admission unknown';
        counts.set(word, (counts.get(word) || 0) + 1);
    }
    const stale = stages.filter(stage => stage.freshness === 'stale').length;
    const unknown = stages.filter(stage => stage.freshness === 'unknown').length;
    return [...counts].map(([word, count]) => `${count} ${word}`).join(' · ')
        + (stale ? ` · ${stale} stale` : unknown ? ` · freshness unknown` : '');
}

/* Public, bounded facts only. Ready/running/resident never implies that a result was consumed. */
export function pipelineModelRows(event, node = 'perception', context = {}) {
    const runtime = runtimeOf(event, context);
    if (!runtime) return event?.health?.model_stages != null ? [['Model metadata', 'Unavailable']] : [];
    const stages = runtime.stages.filter(stage => stageNode(stage) === node);
    const rows = [];
    for (const stage of stages) {
        rows.push(['Stage', `${stage.id} · ${stage.role.replace(/_/g, ' ')}`],
            ['Model ID', stage.model_id ?? 'Unknown'], ['Revision', stage.model_revision ?? 'Unknown'],
            ['Device', stage.device ?? 'Unknown'], ['Profile digest', stage.profile_digest ?? 'Unknown'], ['Configured', reported(stage.configured)],
            ['Provisioned', reported(stage.provisioned)], ['Loaded', reported(stage.loaded)],
            ['Lifecycle', denModelStageLabel(stage)], ['Freshness', freshnessWord(stage.freshness)],
            ['Observation reason', stage.reason?.replace(/_/g, ' ') ?? 'Unknown'],
            ['Admission', stage.reason === 'planned' ? `Planned · ${denModelAdmissionLabel(stage)}` : denModelAdmissionLabel(stage)],
            ['Work', `Queued ${stage.queued ?? 'unknown'} · running ${stage.running ?? 'unknown'}`],
            ['Result use', 'Not reported by model stage']);
    }
    if (!stages.length) rows.push(['Model stages', runtime.rejected_stages ? 'Unavailable' : 'Not reported']);
    return rows;
}

/* Only the coordinator's contracted intent receipt supplies disposition/use.
 * Worker event.intent_hints and motion consumption cannot promote semantic hints. */
export function pipelineIntentRows(spot, context = {}) {
    const path = own(spot, 'path_lighting');
    if (own(path, 'schema') !== 'den-path-lighting-1') return [['Intent receipt', 'Not reported'], ['Intent consumed', 'Unknown']];
    const rows = [];
    for (const [key, label] of [['intent', 'Intent'], ['intent_rejected', 'Declined hint']]) {
        const receipt = own(own(path, 'evidence'), key);
        if (receipt == null && key === 'intent_rejected') continue;
        const disposition = own(receipt, 'disposition');
        const valid = own(receipt, 'schema') === 'den.walking-intent/v1'
            && ['absent', 'ignored', 'expired'].includes(disposition)
            && own(receipt, 'selected') === false && own(receipt, 'consumed') === false
            && own(receipt, 'authority') === 'semantic_prior_only'
            && own(receipt, 'uncertainty') === 'uncalibrated_semantic_hint'
            && own(receipt, 'clock') === 'producer_monotonic';
        if (!valid) { rows.push([`${label} receipt`, 'Unavailable'], [`${label} consumed`, 'Unknown']); continue; }
        const reason = own(receipt, 'reason');
        rows.push([`${label} disposition`, { absent: 'Absent', ignored: 'Ignored', expired: 'Expired' }[disposition]],
            [`${label} reason`, typeof reason === 'string' && Object.hasOwn(INTENT_REASONS, reason) ? INTENT_REASONS[reason] : 'Not reported'],
            [`${label} consumed`, 'No · coordinator receipt']);
        let freshness = 'unknown';
        if (disposition !== 'absent') {
            const observed = own(receipt, 'observed_at'), produced = own(receipt, 'produced_at'), until = own(receipt, 'expires_at');
            const atReceive = denSourceClockInterval(context.sourceClock, context.receivedAt);
            const current = finite(context.now) && finite(context.receivedAt) && context.now >= context.receivedAt
                ? denSourceClockInterval(context.sourceClock, context.now) : null;
            if ([observed, produced, until].every(finite) && 0 <= observed && observed <= produced
                && produced < until && until <= observed + 10 && atReceive && current && produced <= atReceive.upper_mono) {
                if (current.lower_mono >= until) freshness = 'stale';
                else if (produced <= current.lower_mono && current.upper_mono < until) freshness = 'fresh';
            }
        }
        rows.push([`${label} freshness`, freshnessWord(freshness)]);
    }
    return rows;
}

// Older subjects can omit binding fields; every field a subject supplies must be verified on the track.
const subjectMatches = (subject, track) => typeof subject?.track_id === 'string' && subject.track_id.length > 0
    && subject.track_id === track.track_id
    && ['service_run_id', 'camera_epoch', 'association_revision'].every(key => subject[key] == null
        || (key === 'service_run_id' ? typeof subject[key] === 'string' && subject[key].length > 0
            : Number.isSafeInteger(subject[key]) && subject[key] >= 0) && subject[key] === track[key]);

/* The Perception popover uses the producer's model and request round-trip timings separately. Pure. */
export function pipelinePerceptionRows(event, context = {}) {
    const h = event?.health || {}, t = h.timings_ms || {};
    const timing = key => t[key] ? `${ms(t[key].p50)} p50 · ${ms(t[key].p95)} p95` : '—';
    return [['People', String((event?.tracks || []).length)], ['Detector model', timing('detector_model')],
        ['Detector round trip', timing('detector_turnaround')], ['Lift', timing('lift')], ['Track', timing('track')],
        ['Behind', finite(h.lag_s_median) ? secs(h.lag_s_median + (h.transit_s_median || 0)) : '—'],
        ['Status', event?.perception_status ?? '—'], ...pipelineModelRows(event, 'perception', context)];
}

/* What each card says, from one people event (sinceS seconds old) and the applied spot snapshot. Pure. */
export function pipelineCards(event, sinceS, spot, names = {}, context = {}) {
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
    else cards.occupancy = { state: 'off', value: 'Not reported', sub: '' };   // until den-live sends health.occupancy
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
        const subject = spot.subject ? tracks.find(track => subjectMatches(spot.subject, track)) : null;
        const who = subject ? identityOf(subject, event, sinceS).name : spot.subject ? 'a track no longer seen' : 'no one';
        const name = aimed ? (names[aimed.fixture_id] || 'Spot') : '';
        cards.coordinator = spot.stopped ? { state: 'attention', value: 'Stop', sub: 'every spot dark' }
            : lit ? { state: 'live', sub: `for ${who}`, fixture: name, why: REASON[lit.reason] || lit.reason,
                // while a level train runs, where it's heading: "40% → 79%"
                value: finite(lit.fade?.to) && Math.round(lit.fade.to * 100) !== Math.round(lit.level * 100)
                    ? `${Math.round(lit.level * 100)}% → ${Math.round(lit.fade.to * 100)}%` : `${Math.round(lit.level * 100)}%`,
                change: Object.hasOwn(FADE, lit.fade?.purpose) ? `${FADE[lit.fade.purpose]}${finite(lit.fade.duration_s) ? ` over ${lit.fade.duration_s} s` : ''}` : null }
            : aimed ? { state: 'idle', value: 'Aimed', sub: `dark, for ${who}`, fixture: name, why: REASON[aimed.reason] || aimed.reason }
            : { state: 'idle', value: 'Dark', sub: (REASON[rows[0]?.reason] || 'waiting').toLowerCase() };
        cards.coordinator.target = (lit || aimed)?.target_m ?? null;
        const ambient = spot.ambient;   // the room glow (additive): {level, scale, cct_k}
        cards.coordinator.ambient = ambient && finite(ambient.scale) ? `${Math.round(ambient.scale * 100)}% of the house${finite(ambient.cct_k) ? ` · ${ambient.cct_k} K` : ''}` : null;
    }
    const runtime = runtimeOf(event, context);
    for (const node of ['perception', 'qwen']) {
        if (runtime) {
            const stages = runtime.stages.filter(stage => stageNode(stage) === node);
            cards[node].modelStages = stages;
            cards[node].modelSummary = runtimeSummary(stages);
        } else if (event?.health?.model_stages != null) {
            cards[node].modelStages = [];
            cards[node].modelSummary = 'Model metadata unavailable';
        }
    }
    return cards;
}

export function attachDenPipeline({ engine, host, markers, names = () => ({}), reducedMotion = () => false, clock = () => performance.now(),
    getSourceClock = () => null, toggle: ownToggle = true } = {}) {
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
        // Qwen's answer lives ANSWER_S at most: a hairline along the card's foot drains with it
        const life = node.id === 'qwen' ? doc.createElement('i') : null;
        if (life) { life.className = 'home-den-pipeline-life'; life.setAttribute('aria-hidden', 'true'); el.append(life); }
        el.addEventListener('click', event => { event.stopPropagation(); open = open === node.id ? null : node.id; renderPop(); });
        rail.appendChild(el);
        cards.set(node.id, { el, value, sub: subText, state: stateWord, life, node, changedAt: -Infinity });
    }
    const onKey = event => { if (event.key === 'Escape' && open) { open = null; renderPop(); } };
    const onDown = event => { if (open && !layer.contains(event.target)) { open = null; renderPop(); } };
    doc.addEventListener('keydown', onKey);
    doc.addEventListener('pointerdown', onDown, true);

    let latest = null, latestAt = 0, spot = null, spotAt = 0, model = {}, open = null, disposed = false, raf = null;
    let lastAnswerAt = null, lastSpotKey = null, box = null, measuredAt = -Infinity, on = false, fit = null;
    /* The room's top inset eases to the rail's bottom (on) or to 0 (off); the rest of the insets stay the shell's. */
    function insetTarget() {
        if (!on) return 0;
        const c = canvas.getBoundingClientRect(), r = rail.getBoundingClientRect();
        // on a phone Frigate's gate hangs under the rail, so the room starts below it
        const gate = layer.dataset.compact === '1' ? cards.get('occupancy').el.getBoundingClientRect() : null;
        const bottom = Math.max(r.bottom, gate && gate.height > 0 ? gate.bottom : 0);
        return r.height > 0 ? Math.max(0, Math.round(bottom - c.top + 12)) : 0;
    }
    /* Home's sheet (a phone or tablet) covers the room's lower half. Turning the pipeline on asks the sheet down to peek
     * and keeps the height it found; turning it off puts that back only if the sheet still sits at peek. */
    const shell = () => host.closest('.apt-home-shell');
    const askSheet = detent => doc.defaultView?.dispatchEvent(new CustomEvent('home-sheet-detent', { detail: { detent } }));
    function lowerSheet() {
        const s = shell(), panel = s?.querySelector('.apt-home-panel');
        if (!s || !panel) return;
        const p = panel.getBoundingClientRect(), r = s.getBoundingClientRect();
        if (!(p.width >= r.width - 1 && p.top > r.top + 1)) return;   // a side column, not a sheet
        if (s.dataset.sheet && s.dataset.sheet !== 'peek') { sheetBefore = s.dataset.sheet; askSheet('peek'); }
    }
    function restoreSheet() {
        if (sheetBefore && shell()?.dataset.sheet === 'peek') askSheet(sheetBefore);
        sheetBefore = null;
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
        // only the person's toggle moves the sheet: attaching with the stored state (remember false) never does
        if (remember) { if (on) lowerSheet(); else restoreSheet(); }
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
        model = pipelineCards(latest, since, spot, names() || {}, runtimeContext());
        for (const [id, c] of cards) {
            const m = model[id] || { state: 'off', value: '—', sub: '' };
            if (c.el.dataset.state !== m.state) c.el.dataset.state = m.state;
            if (c.value.textContent !== m.value) { if (c.value.textContent) c.changedAt = clock(); c.value.textContent = m.value; }
            const sub = [m.modelSummary, m.sub].filter(Boolean).join(' · ');
            if (c.sub.textContent !== sub) c.sub.textContent = sub;
            const changed = m.state === 'live' && clock() - c.changedAt < 1500;
            const word = m.state === 'live' ? (changed ? 'Changed' : 'Current') : m.state === 'attention' ? 'Stale' : m.state === 'idle' ? 'Idle' : 'Unavailable';
            if (c.state.textContent !== word) c.state.textContent = word;
            if (c.el.dataset.changed !== String(changed)) c.el.dataset.changed = String(changed);
            const label = `${c.node.role}, ${c.node.system}: ${m.value}${sub ? `, ${sub}` : ''}`;
            if (c.el.title !== label) c.el.title = label;
            if (c.el.getAttribute('aria-label') !== label) c.el.setAttribute('aria-label', label);
            if (c.life) {
                const life = m.state === 'live' && finite(m.life) ? m.life : 0;
                const width = `${(life * 100).toFixed(1)}%`;
                if (c.life.style.width !== width) c.life.style.width = width;
            }
        }
        if (open) renderPop();
    }
    function runtimeContext(kind = 'people') {
        let sourceClock = null;
        try { sourceClock = getSourceClock?.() ?? null; } catch (_) { /* unavailable clock stays unknown */ }
        return { sourceClock, receivedAt: kind === 'spot' ? spotAt : latestAt, now: clock() };
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
        }
        if (layer.dataset.compact === '1') { if (pop.style.transform) pop.style.transform = ''; }   // docked along the room's foot (CSS)
        else if (r && box) {
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
            : [['Frigate', 'not reported'], ['Why', 'den-live doesn’t send its occupancy yet']];
        if (id === 'perception') return pipelinePerceptionRows(e, runtimeContext());
        if (id === 'qwen') return [['State', q.state ?? '—'], ['Why', q.why ?? '—'], ['Reply', secs(q.reply_s_median)], ['Asks', finite(q.asks_per_min) ? `${q.asks_per_min} a minute` : '—'],
            ['Answer', model.qwen?.value ?? '—'], ...pipelineModelRows(e, 'qwen', runtimeContext())];
        const rows = Array.isArray(spot?.fixtures) ? spot.fixtures : [];
        return [['Light', `${model.coordinator?.value ?? '—'}${model.coordinator?.fixture ? ` · ${model.coordinator.fixture}` : ''}`], ['Why', model.coordinator?.why ?? '—'], ['For', (model.coordinator?.sub ?? '—').replace(/^for /, '')],
            ...(model.coordinator?.change ? [['Change', model.coordinator.change]] : []), ...(model.coordinator?.ambient ? [['Room glow', model.coordinator.ambient]] : []),
            ...rows.map(row => [names()?.[row.fixture_id] || 'Spot', `${row.on ? `${Math.round(row.level * 100)}%` : 'dark'} · ${String(row.reason).replace(/_/g, ' ')}`]),
            ...pipelineIntentRows(spot, runtimeContext('spot'))];
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
        // on a phone Frigate's gate hangs centred under Qwen's station (its top is CSS: just under the rail)
        const gate = cards.get('occupancy').el, qBox = b.cards.get('qwen'), gBox = b.cards.get('occupancy');
        if (compact && qBox && gBox) {
            const gl = `${Math.round(qBox.left + qBox.width / 2 - gBox.width / 2 - left)}px`;
            if (gate.style.left !== gl) { gate.style.left = gl; measuredAt = -Infinity; }
        } else if (!compact && gate.style.left) { gate.style.left = ''; measuredAt = -Infinity; }
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
          if (A && Z) push('occupancy>qwen', compact ? `M${A.x.toFixed(1)} ${A.top.toFixed(1)} L${Z.x.toFixed(1)} ${Z.bottom.toFixed(1)}`
            : `M${A.x.toFixed(1)} ${A.top.toFixed(1)} C ${A.x.toFixed(1)} ${(A.top - 16).toFixed(1)}, ${Z.x.toFixed(1)} ${(Z.top - 16).toFixed(1)}, ${Z.x.toFixed(1)} ${Z.top.toFixed(1)}`,
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
        setSpot(value, receivedAt = clock()) {
            spot = value || null;
            spotAt = receivedAt;
            const rows = Array.isArray(spot?.fixtures) ? spot.fixtures : [];
            const key = JSON.stringify([spot?.stopped, spot?.subject?.request_id, rows.map(r => [r.on, Math.round((r.level || 0) * 20), r.reason, r.motion_phase])]);
            if (key !== lastSpotKey) { if (lastSpotKey != null) heat('coordinator>light'); lastSpotKey = key; }
            render();
        },
        clear() { latest = null; spot = null; render(); },
        setOn: value => setOn(value),
        status: () => ({ on, open, cards: Object.fromEntries(NODES.map(n => [n.id, { state: model[n.id]?.state ?? null, value: model[n.id]?.value ?? null, sub: model[n.id]?.sub ?? null,
            modelSummary: model[n.id]?.modelSummary ?? null, modelStages: model[n.id]?.modelStages ?? [] }])),
            wires: [...wires.querySelectorAll('path')].map(p => p.getAttribute('class').replace('home-den-pipeline-wire is-', '')) }),
        dispose() {
            if (disposed) return;
            disposed = true;
            if (raf != null) cancelAnimationFrame(raf);
            clearInterval(tick); resized?.disconnect();
            if (on && typeof engine.setViewInsets === 'function') engine.setViewInsets({ ...(engine.viewInsets?.() || {}), top: 0 });
            // the sheet stays where it is: hiding the room is not the person turning the pipeline off
            doc.removeEventListener('keydown', onKey); doc.removeEventListener('pointerdown', onDown, true);
            layer.remove();
        },
    };
}
