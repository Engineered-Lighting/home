/* Aim presentation for the room overlay: fixture label rings, the 44 px
 * reticle, the drag chip, the cyan proposal marker and the Set aim / Discard
 * bar. It reads the admitted UI overlay layer (project, pick, group) and the
 * consumer's DOM. It never moves an aim, proposes, sends, persists or keeps a
 * clock: proposals live in HomeApartmentAimProposals and the host acts on them. */
import * as THREE from 'three';

// Test-facing selector and ARIA vocabulary, copied from SPEC (Gestures, Aim
// lifecycle, Spot or zones) before any code. The lifecycle suite holds its
// own literal copy and fails if either side drifts.
export const AIM_OVERLAY_VOCABULARY = Object.freeze({
    root: 'data-apt-room-overlay',
    label: 'data-apt-fixture-label',
    ring: 'data-apt-fixture-ring',
    reticle: 'data-apt-aim-reticle',
    chip: 'data-apt-aim-chip',
    chipTitle: 'data-apt-aim-chip-title',
    chipMetrics: 'data-apt-aim-chip-metrics',
    chipNote: 'data-apt-aim-chip-note',
    disclosure: 'data-apt-aim-disclosure',
    proposal: 'data-apt-aim-proposal',
    blocker: 'data-apt-aim-blocker',
    bar: 'data-apt-aim-bar',
    barTarget: 'data-apt-aim-bar-target',
    action: 'data-apt-aim-action',
    pending: 'data-apt-aim-pending',
    state: 'data-state',
    aria: Object.freeze({
        reticle: 'Aim handle. Drag onto a surface, or use the arrow keys. Enter keeps the proposal, Escape cancels.',
        bar: 'Proposed aim',
        set: 'Set aim',
        discard: 'Discard',
        pending: 'aim pending',
    }),
});

export const AIM_OVERLAY_COPY = Object.freeze({
    noSurface: 'No surface here',
    notAimable: 'Can’t aim here',
    blocked: name => `Blocked by ${name}`,
    outOfReach: (tilt, limit) => `Out of reach · ${tilt}° from straight down, limit ${limit}°`,
    grazing: 'the pool will be long and dim',
    snaps: name => `Snaps to “${name}”`,
    metrics: (distance, tilt) => `${distance.toFixed(2)} m · ${tilt}° tilt`,
    opticalOrigin: 'Modeled from the socket display point · optical pivot not registered',
    travelLimits: 'travel limits not commissioned',
    barBlocked: 'Blocked',
    barOutOfReach: 'Out of reach',
    fallbackTarget: 'this spot',
    surfaceFallback: 'Scan surface',
    // The suggestion an invalid spot carries with its reason (SPEC › Gestures).
    suggest: Object.freeze({
        no_surface: 'Drag onto a wall, furniture or the floor.',
        not_aimable: 'Choose another surface.',
        blocked: 'Move the spot clear of it, or try another light.',
        out_of_reach: 'Choose a spot closer to straight below the light.',
    }),
});

const DEG = 180 / Math.PI, GRAZING_DEG = 80, SNAP_NORMAL_DOT = .9, MIN_SPAN_M = .015;
// A press that moves less than this is a click on the reticle, not an aim.
const DRAG_THRESHOLD_PX = 5;
const RETICLE_RADIUS_PX = 22, LABEL_LIFT_PX = 22, FEW_FIXTURES = 6;
// A label pill is 24 px tall; on a coarse pointer its hit area is 44 px.
const LABEL_HEIGHT_PX = 24, LABEL_HIT_PX = 44, LABEL_GAP_PX = 4, LABEL_MIN_WIDTH_PX = 44, LABEL_SHORT_MAX = 18;
// In-room chrome a label must not cover (DIRECTION › Canvas overlays).
export const LABEL_AVOID_ATTRIBUTE = 'data-apt-label-avoid';
const CHIP_OFFSET_PX = 18, CHIP_BOX = Object.freeze({ width: 240, height: 72 });
const BAR_HALF_WIDTH_PX = 110, BAR_DROP_PX = 38, BAR_TOP_PX = 40, BAR_BOTTOM_PX = 70;
const UNAIMABLE = Object.freeze(['blocked', 'out_of_reach', 'no_surface', 'not_aimable']);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const vector = value => Array.isArray(value) && value.length === 3 && value.every(finite);
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const length = a => Math.hypot(a[0], a[1], a[2]);
const scale = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const unitOf = a => { const n = length(a); return n > 1e-9 ? scale(a, 1 / n) : null; };
const name = (value, fallback) => typeof value === 'string' && value.trim() ? value.trim().slice(0, 80) : fallback;
const freezeModel = model => {
    for (const key of ['point_m', 'normal_m', 'blocker_point_m', 'disclosures']) if (model[key]) Object.freeze(model[key]);
    if (model.snap) Object.freeze(model.snap);
    return Object.freeze(model);
};

// Distance is measured from the optical origin when one is registered and
// otherwise from the socket display point, as the fixture module aims.
export function aimOrigin(descriptor) {
    if (vector(descriptor?.optical_origin_m)) return { point_m: descriptor.optical_origin_m.slice(), modeled: false };
    const point = vector(descriptor?.display_origin_m) ? descriptor.display_origin_m
        : vector(descriptor?.display_anchor_m) ? descriptor.display_anchor_m : null;
    return point ? { point_m: point.slice(), modeled: true } : null;
}

// A reach claim needs commissioned travel. Virtual-preview limits are a
// display range, so Victoria never says "out of reach" or implies reach.
// Limits use the fixture's elevation tilt (90 is straight down), so the
// lowest commissioned elevation bounds the angle from straight down.
export function commissionedTiltLimit(limits) {
    if (limits?.physical_travel !== 'commissioned' || limits.limit_role === 'virtual_preview' || !finite(limits.tilt_min_deg)) return null;
    const fromDown = 90 - limits.tilt_min_deg;
    return fromDown > 0 && fromDown < 180 ? fromDown : null;
}

// An engineered fixture profile's configured limits (Echo's simulated
// engineered light) bound its simulated aim the same way. They describe the
// profile, not commissioned travel, so they never stand in for Victoria's.
function profileTiltLimit(limits) {
    if (limits?.limit_role !== 'engineered_profile' || limits.physical_travel === 'commissioned' || !finite(limits.tilt_min_deg)) return null;
    const fromDown = 90 - limits.tilt_min_deg;
    return fromDown > 0 && fromDown < 180 ? fromDown : null;
}
const reachTiltLimit = limits => commissionedTiltLimit(limits) ?? profileTiltLimit(limits);

export function aimDisclosures(descriptor, limits) {
    const origin = aimOrigin(descriptor), out = [];
    if (!origin || origin.modeled) out.push({ id: 'optical-origin', text: AIM_OVERLAY_COPY.opticalOrigin });
    if (commissionedTiltLimit(limits) == null && limits?.limit_role !== 'engineered_profile')
        out.push({ id: 'travel-limits', text: AIM_OVERLAY_COPY.travelLimits });
    return out.map(item => Object.freeze(item));
}

function nearestNamedAim(namedAims, point, normal) {
    let best = null;
    for (const aim of Array.isArray(namedAims) ? namedAims : []) {
        const aimNormal = unitOf(vector(aim?.normal_m) ? aim.normal_m : [0, 0, 0]);
        if (!vector(aim?.point_m) || !aimNormal || dot(aimNormal, normal) < SNAP_NORMAL_DOT) continue;
        const size = Array.isArray(aim.size_m) ? Math.max(0, ...aim.size_m.filter(finite)) : 0;
        const distance = length(sub(aim.point_m, point));
        if (distance < Math.max(.22, size * .42) && (!best || distance < best.distance))
            best = { aim, normal: aimNormal, distance };
    }
    return best;
}

/* What the chip says about one surface hit. `hit` is the layer's pick
 * ({point, normal, surfaceName}) or null; `traceBeam(origin, point)` is the
 * optional canonical blocker trace ({point_m, name} when something is in the
 * way). Without a trace nothing is ever called blocked. */
export function describeAimHit({ descriptor, limits, hit, namedAims, traceBeam } = {}) {
    const origin = aimOrigin(descriptor), disclosures = aimDisclosures(descriptor, limits);
    const invalid = (reason, title, extra = {}) => freezeModel({ valid: false, reason, title, metrics: null,
        note: AIM_OVERLAY_COPY.suggest[reason] || null,
        disclosures, surface: null, snap: null, point_m: null, normal_m: null, distance_m: null, tilt_deg: null,
        blocker_point_m: null, ...extra });
    if (!origin) return invalid('not_aimable', AIM_OVERLAY_COPY.notAimable);
    if (!vector(hit?.point) || !vector(hit?.normal)) return invalid('no_surface', AIM_OVERLAY_COPY.noSurface);
    let point = hit.point.slice(), normal = unitOf(hit.normal), surface = name(hit.surfaceName, AIM_OVERLAY_COPY.surfaceFallback), snap = null;
    if (!normal) return invalid('no_surface', AIM_OVERLAY_COPY.noSurface);
    let ray = unitOf(sub(point, origin.point_m));
    if (!ray) return invalid('not_aimable', AIM_OVERLAY_COPY.notAimable);
    // Picking turns every normal toward the viewer. When that visible side
    // faces away from the light, the light can't reach it: the viewer sees
    // the back of the surface, such as the top of a ceiling above a
    // ceiling-mounted light. A drag keeps its last good spot instead.
    if (dot(normal, ray) > 0) return invalid('no_surface', AIM_OVERLAY_COPY.noSurface, { unlit_side: true });
    const near = nearestNamedAim(namedAims, point, normal);
    if (near) {
        point = near.aim.point_m.slice(); normal = near.normal;
        surface = name(near.aim.name, surface);
        snap = { id: name(near.aim.id, null), name: surface };
        ray = unitOf(sub(point, origin.point_m));
        if (!ray) return invalid('not_aimable', AIM_OVERLAY_COPY.notAimable);
    }
    const distance = length(sub(point, origin.point_m));
    const tilt = Math.acos(clamp(-ray[2], -1, 1)) * DEG, shownTilt = Math.round(tilt);
    const measured = { surface, snap, point_m: point, normal_m: normal, distance_m: distance, tilt_deg: tilt,
        metrics: AIM_OVERLAY_COPY.metrics(distance, shownTilt) };
    if (distance < MIN_SPAN_M) return invalid('not_aimable', AIM_OVERLAY_COPY.notAimable, measured);
    const limit = reachTiltLimit(limits);
    if (limit != null && tilt > limit) return invalid('out_of_reach', AIM_OVERLAY_COPY.outOfReach(shownTilt, Math.round(limit)), measured);
    let blocker = null;
    if (typeof traceBeam === 'function') {
        try { blocker = traceBeam(origin.point_m.slice(), point.slice()); } catch (_) { blocker = null; }
    }
    if (blocker && vector(blocker.point_m))
        return invalid('blocked', AIM_OVERLAY_COPY.blocked(name(blocker.name, 'an object')), { ...measured, blocker_point_m: blocker.point_m.slice() });
    const incidence = Math.acos(clamp(-dot(normal, ray), -1, 1)) * DEG;
    return freezeModel({ valid: true, reason: null, title: snap ? AIM_OVERLAY_COPY.snaps(surface) : surface,
        note: incidence > GRAZING_DEG ? AIM_OVERLAY_COPY.grazing : null, disclosures, blocker_point_m: null, ...measured });
}

// The store's target shape. A hit with no surface has no spot to propose.
export function proposalTargetFromModel(model) {
    if (!model || !vector(model.point_m)) return null;
    const target = { point_m: model.point_m.slice(), valid: model.valid === true };
    if (!target.valid) target.reason = UNAIMABLE.includes(model.reason) ? model.reason : 'not_aimable';
    if (typeof model.surface === 'string' && model.surface) target.surface = model.surface.slice(0, 160);
    if (vector(model.normal_m)) target.normal_m = model.normal_m.slice();
    return target;
}

// What the room bar shows for a pending proposal. Invalid offers only Discard.
export function describeProposalBar(proposal) {
    const target = proposal?.target;
    if (!target || !vector(target.point_m)) return null;
    if (target.valid === true) return Object.freeze({ state: 'valid', title: name(target.surface, AIM_OVERLAY_COPY.fallbackTarget), actions: Object.freeze(['set', 'discard']) });
    const title = target.reason === 'out_of_reach' ? AIM_OVERLAY_COPY.barOutOfReach
        : target.reason === 'blocked' ? AIM_OVERLAY_COPY.barBlocked
            : target.reason === 'no_surface' ? AIM_OVERLAY_COPY.noSurface : AIM_OVERLAY_COPY.notAimable;
    return Object.freeze({ state: 'blocked', title, actions: Object.freeze(['discard']) });
}

/* Keys the overlay answers itself. Escape walks the SPEC ladder from the top:
 * an active drag cancels first and leaves the proposal unchanged; otherwise a
 * proposal is discarded. Enter sets only a valid proposal. */
export function aimKeyIntent({ key, dragging = false, proposal = null } = {}) {
    if (key === 'Escape') return dragging ? 'cancel_drag' : proposal ? 'discard' : null;
    if (key === 'Enter') return dragging ? 'release' : proposal?.target?.valid === true ? 'set' : null;
    return null;
}

/* The short name an idle label shows when every label is on screen
 * (prototype `short`). It only trims the registered name, never invents one:
 * a trailing "fixture" goes, and a long name ends in an ellipsis. */
export function shortFixtureLabel(label) {
    const text = typeof label === 'string' ? label.trim() : '';
    const trimmed = text.replace(/\s+fixture$/i, '') || text;
    return trimmed.length > LABEL_SHORT_MAX ? trimmed.slice(0, LABEL_SHORT_MAX - 1).trimEnd() + '…' : trimmed;
}

const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;

/* Prototype label rules (scene.js _placeLabels) on the bridge projection.
 * Every label shows when there are few fixtures, otherwise only hover and
 * selection; idle labels then use short names. A label sits above its fixture
 * and climbs past anything it would touch: another label, or a box in
 * `avoid` (the mode pill, the room toolbar, the hint bar, a body label). With
 * no room above it drops below the fixture instead, and it never leaves the
 * visible canvas. On a coarse pointer each label keeps its whole 44 px hit
 * area clear. Selection and hover are placed first; an idle label with no
 * room left is hidden rather than drawn over another. */
export function placeFixtureLabels(entries, { selectedId = null, hoverId = null, pillFixtureId = null, bounds,
    avoid = [], coarse = false } = {}) {
    const list = Array.isArray(entries) ? entries : [], few = list.length <= FEW_FIXTURES;
    const height = coarse ? LABEL_HIT_PX : LABEL_HEIGHT_PX, half = height / 2;
    const box = bounds || { left: -Infinity, top: -Infinity, right: Infinity, bottom: Infinity };
    const taken = (Array.isArray(avoid) ? avoid : []).filter(item => item && [item.left, item.top, item.right, item.bottom].every(finite));
    const rank = entry => entry.id === selectedId ? 0 : entry.id === hoverId ? 1 : 2;
    const order = list.map((entry, index) => ({ entry, index })).sort((a, b) => rank(a.entry) - rank(b.entry) || a.index - b.index);
    const out = new Array(list.length);
    for (const { entry, index } of order) {
        const selected = entry.id === selectedId, hover = entry.id === hoverId;
        const screen = entry.screen, inside = !!screen && screen.x >= box.left && screen.x <= box.right
            && screen.y >= box.top && screen.y <= box.bottom;
        const state = selected ? 'selected' : hover ? 'hover' : 'idle';
        const ring = inside && (selected || hover) ? screen : null;
        if (!inside || !(few || selected || hover) || entry.id === pillFixtureId) { out[index] = { id: entry.id, visible: false, state, ring }; continue; }
        const width = Math.max(LABEL_MIN_WIDTH_PX, finite(entry.width) ? entry.width : 0);
        const room = box.right - box.left;
        const x = room <= width ? (box.left + box.right) / 2 : clamp(screen.x, box.left + width / 2, box.right - width / 2);
        const at = y => ({ left: x - width / 2 - LABEL_GAP_PX / 2, right: x + width / 2 + LABEL_GAP_PX / 2, top: y - half, bottom: y + half });
        const settle = (y, step) => {
            for (let tries = 0; tries <= taken.length; tries++) {
                if (y - half < box.top || y + half > box.bottom) return null;
                const blocker = taken.find(item => overlaps(at(y), item));
                if (!blocker) return y;
                y = step < 0 ? blocker.top - LABEL_GAP_PX - half : blocker.bottom + LABEL_GAP_PX + half;
            }
            return null;
        };
        let y = settle(clamp(screen.y - LABEL_LIFT_PX, box.top + half, box.bottom - half), -1);
        if (y == null) y = settle(clamp(screen.y + LABEL_LIFT_PX, box.top + half, box.bottom - half), 1);
        if (y == null && (selected || hover)) y = clamp(screen.y - LABEL_LIFT_PX, box.top + half, box.bottom - half);
        if (y == null) { out[index] = { id: entry.id, visible: false, state, ring }; continue; }
        taken.push(at(y));
        out[index] = { id: entry.id, visible: true, state, x, y, full: selected || hover || !few, ring };
    }
    return out;
}

// The bar sits below the proposed spot, kept inside the visible canvas (left
// of the column, above the sheet) so it stays usable with Chat open.
export function placeAimBar(screen, bounds) {
    if (!screen || !bounds) return null;
    const width = bounds.right - bounds.left, height = bounds.bottom - bounds.top;
    if (!(width > 0 && height > 0)) return null;
    const x = width < BAR_HALF_WIDTH_PX * 2 ? (bounds.left + bounds.right) / 2
        : clamp(screen.x, bounds.left + BAR_HALF_WIDTH_PX, bounds.right - BAR_HALF_WIDTH_PX);
    const y = height < BAR_TOP_PX + BAR_BOTTOM_PX ? bounds.top
        : clamp(screen.y + BAR_DROP_PX, bounds.top + BAR_TOP_PX, bounds.bottom - BAR_BOTTOM_PX);
    return { x, y };
}

// The chip trails the cursor and flips to stay inside the visible canvas.
export function placeDragChip(cursor, bounds, box = CHIP_BOX) {
    if (!cursor || !bounds) return null;
    let x = cursor.x + CHIP_OFFSET_PX, y = cursor.y + CHIP_OFFSET_PX;
    if (x + box.width > bounds.right) x = cursor.x - CHIP_OFFSET_PX - box.width;
    if (y + box.height > bounds.bottom) y = cursor.y - CHIP_OFFSET_PX - box.height;
    return { x: Math.max(bounds.left, x), y: Math.max(bounds.top, y) };
}

/* The Zones|Spot pill never covers the reticle. `pill` is the box the pill
 * would take ({x, y} bottom-centre, width, height); it moves up just enough
 * to clear the reticle's 44 px hit area. */
export function pillClearOfReticle(pill, reticle, gap = 6) {
    if (!pill || !reticle) return pill ? { x: pill.x, y: pill.y } : null;
    const half = pill.width / 2, top = pill.y - pill.height;
    const overlaps = pill.x + half > reticle.x - RETICLE_RADIUS_PX && pill.x - half < reticle.x + RETICLE_RADIUS_PX
        && pill.y > reticle.y - RETICLE_RADIUS_PX - gap && top < reticle.y + RETICLE_RADIUS_PX;
    return { x: pill.x, y: overlaps ? reticle.y - RETICLE_RADIUS_PX - gap : pill.y };
}

/* The presentation controller, on the overlay bridge's handle pattern: every
 * call first checks the owner predicate and retires when it fails; dispose is
 * idempotent and releases only what this module created. */
export function createAimPresentation({ layer, host, isCurrent, getInsets = () => null, traceBeam, onRetire = () => {} } = {}) {
    if (typeof layer?.project !== 'function' || typeof layer.pick !== 'function' || !layer.group?.isGroup
        || !host?.ownerDocument || typeof host.getBoundingClientRect !== 'function' || typeof isCurrent !== 'function'
        || typeof getInsets !== 'function' || traceBeam !== undefined && typeof traceBeam !== 'function'
        || typeof onRetire !== 'function') throw new TypeError('apartment_aim_presentation_dependencies_required');
    let disposed = false, elements = null, drag = null, revision = 0;
    let state = { fixtures: [], selectedId: null, hoverId: null, pillFixtureId: null, aim: null, proposal: null };
    let snapshot = Object.freeze({ revision, dragging: false, model: null });
    const listeners = new Set(), written = new WeakMap();
    const resources = [];
    const own = resource => { resources.push(resource); return resource; };
    const scene = new THREE.Group();
    scene.name = 'apartment-aim-presentation';
    scene.userData.presentation_only = true;
    const lineGeometry = own(new THREE.BufferGeometry());
    lineGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
    const proposalLine = new THREE.Line(lineGeometry, own(new THREE.LineDashedMaterial({ color: 0x45dfff, transparent: true,
        opacity: .85, dashSize: .05, gapSize: .035, depthTest: false, depthWrite: false })));
    proposalLine.name = 'aim-proposal-ray'; proposalLine.renderOrder = 41; proposalLine.frustumCulled = false; proposalLine.visible = false;
    const proposalSpot = new THREE.Mesh(own(new THREE.RingGeometry(.045, .065, 32)), own(new THREE.MeshBasicMaterial({ color: 0x45dfff,
        transparent: true, opacity: .9, depthTest: false, depthWrite: false, side: THREE.DoubleSide })));
    proposalSpot.name = 'aim-proposal-spot'; proposalSpot.renderOrder = 41; proposalSpot.visible = false;
    scene.add(proposalLine, proposalSpot);
    try { layer.group.add(scene); } catch (error) { for (const resource of resources) resource.dispose?.(); throw error; }

    function notify(next) {
        snapshot = Object.freeze({ revision: ++revision, ...next });
        for (const listener of [...listeners]) { try { listener(); } catch (_) { /* isolated */ } }
    }
    function current() {
        if (disposed) return false;
        let ok = false;
        try { ok = isCurrent() === true; } catch (_) { ok = false; }
        if (!ok) dispose();
        return ok;
    }
    function bounds() {
        const width = host.clientWidth, height = host.clientHeight;
        if (!(width > 0 && height > 0)) return null;
        let insets = null;
        try { insets = getInsets(); } catch (_) { insets = null; }
        const inset = key => finite(insets?.[key]) && insets[key] > 0 ? insets[key] : 0;
        return { left: inset('left'), top: inset('top'), right: width - inset('right'), bottom: height - inset('bottom') };
    }
    const project = point => { try { return vector(point) ? layer.project(point) : null; } catch (_) { return null; } };
    let coarsePointer = null;
    try { coarsePointer = host.ownerDocument.defaultView?.matchMedia?.('(pointer: coarse)') || null; } catch (_) { coarsePointer = null; }
    // A hidden label has no layout, so it keeps the width it last showed
    // with the same text; a label never shown is estimated from its text.
    const widths = new WeakMap();
    function labelWidth(element) {
        if (!element) return null;
        const text = element.textContent || '', measured = element.hidden ? 0 : element.offsetWidth;
        if (measured > 0) { widths.set(element, { text, width: measured }); return measured; }
        const last = widths.get(element);
        return last && last.text === text ? last.width : 30 + text.length * 6.6;
    }
    // The room chrome a label yields to, in host coordinates.
    function avoidBoxes() {
        const scope = host.closest?.('[data-home-apartment-view]') || host.parentElement || host;
        const nodes = scope.querySelectorAll?.(`[${LABEL_AVOID_ATTRIBUTE}]`) || [];
        if (!nodes.length) return [];
        const origin = host.getBoundingClientRect(), out = [];
        for (const node of nodes) {
            if (node.hidden || node.closest?.('[hidden]')) continue;
            const rect = node.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) out.push({ left: rect.left - origin.left, top: rect.top - origin.top,
                right: rect.right - origin.left, bottom: rect.bottom - origin.top });
        }
        return out;
    }
    const within = (screen, box) => screen && box && screen.x >= box.left && screen.x <= box.right && screen.y >= box.top && screen.y <= box.bottom;
    function write(element, hidden, x, y) {
        if (!element) return;
        if (element.hidden !== hidden) element.hidden = hidden;
        if (hidden) return;
        const transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`, last = written.get(element);
        if (last !== transform) { element.style.setProperty('--apt-x', Math.round(x) + 'px'); element.style.setProperty('--apt-y', Math.round(y) + 'px'); written.set(element, transform); }
    }
    function aimFixture() {
        const aim = state.aim;
        return aim && aim.descriptor?.fixture_id === state.selectedId ? aim : null;
    }
    function shownProposal() {
        const aim = aimFixture(), proposal = state.proposal;
        return aim && proposal?.fixture_id === aim.descriptor.fixture_id ? proposal : null;
    }
    function reticlePoint() {
        const aim = aimFixture();
        if (!aim) return null;
        const proposal = shownProposal(), descriptor = aim.descriptor;
        if (vector(proposal?.target?.point_m)) return proposal.target.point_m;
        if (vector(descriptor.target?.point)) return descriptor.target.point;
        const anchor = vector(descriptor.display_anchor_m) ? descriptor.display_anchor_m : null;
        // z_up_metric_floor0: straight below the socket on the floor plane.
        return anchor ? [anchor[0], anchor[1], 0] : null;
    }
    function reticleState() {
        const model = drag?.model, proposal = shownProposal();
        if (model) return model.valid ? 'proposed' : 'invalid';
        if (proposal) return proposal.target.valid ? 'proposed' : 'invalid';
        return 'current';
    }
    function describeAt(clientX, clientY) {
        const aim = state.aim;
        if (!aim) return null;
        let hit = null;
        try { hit = layer.pick(clientX, clientY); } catch (_) { hit = null; }
        return describeAimHit({ descriptor: aim.descriptor, limits: aim.limits, namedAims: aim.namedAims, hit, traceBeam });
    }
    function cursorOf(clientX, clientY) {
        const rect = host.getBoundingClientRect();
        return { x: clientX - rect.left, y: clientY - rect.top };
    }
    function drawProposal(proposal) {
        const origin = proposal && aimOrigin(state.aim?.descriptor);
        if (!origin || !vector(proposal.target.point_m)) { proposalLine.visible = proposalSpot.visible = false; return; }
        const positions = lineGeometry.attributes.position;
        positions.array.set([...origin.point_m, ...proposal.target.point_m]); positions.needsUpdate = true;
        proposalLine.computeLineDistances();
        proposalLine.material.color.setHex(proposal.target.valid ? 0x45dfff : 0xff625f);
        proposalSpot.material.color.setHex(proposal.target.valid ? 0x45dfff : 0xff625f);
        proposalSpot.position.fromArray(proposal.target.point_m);
        const normal = vector(proposal.target.normal_m) ? proposal.target.normal_m : [0, 0, 1];
        proposalSpot.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), new THREE.Vector3(...normal).normalize());
        proposalSpot.position.addScaledVector(new THREE.Vector3(...normal).normalize(), .006);
        proposalLine.visible = proposalSpot.visible = true;
    }
    function frame() {
        if (!current()) return false;
        const box = bounds(), proposal = shownProposal();
        drawProposal(drag ? null : proposal);
        if (!elements?.root?.isConnected || !box) return false;
        // Every projection reads layout, so all of them run before any style
        // write; one frame then costs at most one layout, not one per element.
        const placed = placeFixtureLabels(state.fixtures.map(fixture => ({ id: fixture.fixture_id,
            width: labelWidth(elements.labels.get(fixture.fixture_id)),
            screen: project(vector(fixture.display_anchor_m) ? [fixture.display_anchor_m[0], fixture.display_anchor_m[1], fixture.display_anchor_m[2] + .02] : null) })),
        { selectedId: state.selectedId, hoverId: state.hoverId, pillFixtureId: state.pillFixtureId, bounds: box,
            avoid: avoidBoxes(), coarse: !!coarsePointer?.matches });
        const aim = aimFixture(), reticleAt = drag ? drag.cursor : aim ? project(reticlePoint()) : null;
        const chipAt = drag?.model && placeDragChip(drag.cursor, box);
        const blockerAt = drag?.model?.blocker_point_m && project(drag.model.blocker_point_m);
        const proposalAt = !drag && proposal ? project(proposal.target.point_m) : null;
        // A spot under the column or sheet keeps its bar, clamped into view.
        const barAt = proposalAt ? placeAimBar(proposalAt, box) : null;
        for (const item of placed) {
            const label = elements.labels.get(item.id), ring = elements.rings.get(item.id);
            write(label, !item.visible, item.x, item.y);
            if (label && label.getAttribute('data-state') !== item.state) label.setAttribute('data-state', item.state);
            write(ring, !item.ring, item.ring?.x, item.ring?.y);
            if (ring && item.ring) ring.setAttribute('data-state', item.state);
        }
        write(elements.reticle, !aim || !within(reticleAt, box), reticleAt?.x, reticleAt?.y);
        if (elements.reticle && elements.reticle.getAttribute('data-state') !== reticleState()) elements.reticle.setAttribute('data-state', reticleState());
        write(elements.chip, !chipAt, chipAt?.x, chipAt?.y);
        write(elements.blocker, !within(blockerAt, box), blockerAt?.x, blockerAt?.y);
        write(elements.proposal, !within(proposalAt, box), proposalAt?.x, proposalAt?.y);
        write(elements.bar, !barAt, barAt?.x, barAt?.y);
        return current();
    }
    function collect(root) {
        const query = attribute => [...root.querySelectorAll(`[${attribute}]`)];
        const byId = attribute => new Map(query(attribute).map(node => [node.getAttribute(attribute), node]));
        const one = attribute => root.querySelector(`[${attribute}]`);
        const V = AIM_OVERLAY_VOCABULARY;
        return { root, labels: byId(V.label), rings: byId(V.ring), reticle: one(V.reticle), chip: one(V.chip),
            blocker: one(V.blocker), proposal: one(V.proposal), bar: one(V.bar) };
    }
    function hideAll() {
        proposalLine.visible = proposalSpot.visible = false;
        if (!elements) return;
        for (const node of [...elements.labels.values(), ...elements.rings.values(), elements.reticle, elements.chip,
            elements.blocker, elements.proposal, elements.bar]) if (node) node.hidden = true;
    }
    function dispose() {
        if (disposed) return false;
        disposed = true; drag = null;
        hideAll();
        try { scene.removeFromParent(); } catch (_) { /* owner cleanup continues */ }
        for (const resource of resources) { try { resource.dispose(); } catch (_) { /* owner cleanup continues */ } }
        resources.length = 0; elements = null;
        notify({ dragging: false, model: null });
        listeners.clear();
        try { onRetire(); } catch (_) { /* observer failures stay inside */ }
        return true;
    }
    const handle = Object.freeze({
        // Host state: fixtures with display anchors, selection, hover, the
        // fixture whose pill carries its name, the aim fixture and the
        // store's current proposal for it. Sync never notifies, so a React
        // layout effect can call it every render.
        sync(next = {}) {
            if (!current()) return false;
            const fixtures = Array.isArray(next.fixtures) ? next.fixtures.filter(item => typeof item?.fixture_id === 'string') : state.fixtures;
            state = { fixtures, selectedId: next.selectedId ?? null, hoverId: next.hoverId ?? null,
                pillFixtureId: next.pillFixtureId ?? null, aim: next.aim?.descriptor ? next.aim : null, proposal: next.proposal ?? null };
            if (drag && !aimFixture()) { drag = null; notify({ dragging: false, model: null }); }
            return true;
        },
        attach(root) {
            if (disposed || !root?.querySelectorAll) return () => {};
            elements = collect(root); hideAll(); frame();
            return () => { if (elements?.root === root) { hideAll(); elements = null; } };
        },
        frame,
        beginDrag({ pointerId, clientX, clientY } = {}) {
            if (!current() || drag || !aimFixture() || ![clientX, clientY].every(finite)) return false;
            const cursor = cursorOf(clientX, clientY);
            drag = { pointerId, start: cursor, cursor, moved: false, model: null };
            notify({ dragging: true, model: null });
            frame();
            return true;
        },
        moveDrag({ pointerId, clientX, clientY } = {}) {
            if (!current() || !drag || pointerId !== drag.pointerId || ![clientX, clientY].every(finite)) return null;
            drag.cursor = cursorOf(clientX, clientY);
            if (!drag.moved && Math.hypot(drag.cursor.x - drag.start.x, drag.cursor.y - drag.start.y) < DRAG_THRESHOLD_PX) { frame(); return null; }
            drag.moved = true;
            const model = describeAt(clientX, clientY);
            // A sample on a surface's unlit side keeps the last good spot, so
            // the proposal follows the floor rather than jumping to a ceiling.
            drag.model = model?.unlit_side && drag.model?.valid ? drag.model : model;
            notify({ dragging: true, model: drag.model });
            frame();
            return drag.model;
        },
        // Ends the drag and returns what it would propose; the host decides.
        // A press that never moved past the threshold proposes nothing.
        endDrag({ pointerId } = {}) {
            if (!drag || pointerId !== drag.pointerId) return null;
            const model = drag.model;
            drag = null;
            if (!current()) return null;
            notify({ dragging: false, model: null });
            frame();
            return model;
        },
        cancelDrag() {
            if (!drag) return false;
            drag = null;
            if (current()) { notify({ dragging: false, model: null }); frame(); }
            return true;
        },
        // Keyboard nudge from the reticle's projected point, in screen space.
        nudge(dx, dy) {
            if (!current() || drag || !aimFixture() || ![dx, dy].every(finite)) return null;
            const at = project(reticlePoint());
            if (!at) return null;
            const rect = host.getBoundingClientRect();
            return describeAt(rect.left + at.x + dx, rect.top + at.y + dy);
        },
        describeAt(clientX, clientY) { return current() && [clientX, clientY].every(finite) ? describeAt(clientX, clientY) : null; },
        // Pure helpers for the classic-script overlay, which cannot import.
        vocabulary: AIM_OVERLAY_VOCABULARY,
        describeProposalBar,
        shortLabel: shortFixtureLabel,
        proposalTarget: proposalTargetFromModel,
        keyIntent: aimKeyIntent,
        subscribe(listener) { if (typeof listener !== 'function') return () => {}; listeners.add(listener); return () => listeners.delete(listener); },
        getSnapshot: () => snapshot,
        readback: () => Object.freeze({ disposed, dragging: !!drag, proposal_drawn: proposalLine.visible,
            reticle_state: disposed ? null : reticleState(), physical_commands: false }),
        dispose,
    });
    return handle;
}
