/* Den labels, pure: the record (den-room-labels-1), names and kinds, typical sizes, where a new box goes, and the
 * checks a save must pass. No DOM, no three.js, no network, nothing runs on import. den-live's room_labels.py holds
 * the same record and checks, with the same refusal words.
 *
 * The frame is the den scan's own (Home's apartment frame): Z up, metres, floor at z = 0. A label is one upright box:
 * centre_m, size_m = [width along its own x, depth along its own y, height], and yaw_deg turning its own x from the
 * scan's +x, counter-clockwise from above. Its own +y face is its front, so front = (-sin yaw, cos yaw). A picture,
 * a screen, a door or a window clicked on a wall sits flush with that wall (on_wall), its front into the room. */

export const SCHEMA = 'den-room-labels-1';
export const KINDS = Object.freeze(['seat', 'table', 'picture', 'machine', 'storage', 'screen', 'opening', 'lamp', 'area', 'other']);
export const KIND_LABEL = Object.freeze({ seat: 'Seat', table: 'Table or desk', picture: 'Picture', machine: 'Machine', storage: 'Shelf or storage',
    screen: 'Screen', opening: 'Door or window', lamp: 'Lamp', area: 'Area', other: 'Other' });
export const MAX_LABELS = 100, MAX_BYTES = 65536, NAME_MAX = 40, MIN_SIZE = 0.02, MAX_SIZE = 8;
const NAME = /^[A-Za-z0-9][A-Za-z0-9 \-'&/().]*$/;
const ID = /^l-[a-z0-9-]{1,60}$/;
const WALL_KINDS = new Set(['picture', 'opening', 'screen']);

/* Usual den names, their kind and a typical size [w, d, h] in metres. Typing offers these first. */
export const NAMES = Object.freeze([
    ['sofa', 'seat', [2.0, 0.9, 0.85]], ['armchair', 'seat', [0.85, 0.85, 0.95]], ['desk chair', 'seat', [0.6, 0.6, 1.0]],
    ['chair', 'seat', [0.55, 0.55, 0.9]], ['stool', 'seat', [0.4, 0.4, 0.6]], ['ottoman', 'seat', [0.6, 0.6, 0.42]],
    ['desk', 'table', [1.4, 0.7, 0.75]], ['coffee table', 'table', [1.0, 0.55, 0.42]], ['side table', 'table', [0.5, 0.5, 0.55]],
    ['table', 'table', [1.2, 0.8, 0.75]], ['workbench', 'table', [1.5, 0.7, 0.9]],
    ['painting', 'picture', [0.8, 0.03, 0.6]], ['poster', 'picture', [0.6, 0.02, 0.9]], ['print', 'picture', [0.5, 0.03, 0.4]],
    ['mirror', 'picture', [0.6, 0.03, 1.0]],
    ['3D printer', 'machine', [0.5, 0.5, 0.6]], ['computer', 'machine', [0.25, 0.5, 0.45]], ['sewing machine', 'machine', [0.45, 0.2, 0.3]],
    ['speaker', 'machine', [0.25, 0.3, 0.4]],
    ['bookshelf', 'storage', [0.9, 0.35, 1.8]], ['cabinet', 'storage', [0.9, 0.45, 0.9]], ['shelf', 'storage', [0.9, 0.3, 0.3]],
    ['drawers', 'storage', [0.5, 0.5, 0.6]],
    ['TV', 'screen', [1.25, 0.08, 0.75]], ['monitor', 'screen', [0.6, 0.2, 0.4]],
    ['door', 'opening', [0.9, 0.05, 2.05]], ['window', 'opening', [1.2, 0.05, 1.3]],
    ['floor lamp', 'lamp', [0.4, 0.4, 1.6]], ['desk lamp', 'lamp', [0.2, 0.2, 0.45]],
    ['plant', 'other', [0.45, 0.45, 0.9]], ['reading corner', 'area', [1.5, 1.5, 0.02]], ['rug', 'area', [2.0, 1.4, 0.02]],
].map(row => Object.freeze(row)));
export const KIND_DEFAULT = Object.freeze({ seat: [0.6, 0.6, 0.9], table: [1.2, 0.7, 0.75], picture: [0.8, 0.03, 0.6],
    machine: [0.5, 0.5, 0.5], storage: [0.9, 0.4, 1.2], screen: [1.0, 0.08, 0.6], opening: [0.9, 0.05, 2.0], lamp: [0.35, 0.35, 1.5],
    area: [1.5, 1.5, 0.02], other: [0.5, 0.5, 0.5] });
const KEYWORDS = [
    [/\b(sofa|couch|chair|stool|ottoman|bench|seat|recliner|bed|beanbag)\b/i, 'seat'],
    [/\b(desk|table|workbench|counter|nightstand)\b/i, 'table'],
    [/\b(painting|picture|poster|print|art|artwork|photo|frame|mirror|canvas)\b/i, 'picture'],
    [/(printer|machine|computer|\bpc\b|console|speaker|router|tower)/i, 'machine'],
    [/\b(shelf|shelves|bookshelf|bookcase|cabinet|dresser|drawers?|wardrobe|closet|chest|rack)\b/i, 'storage'],
    [/\b(tv|television|monitor|screen|projector)\b/i, 'screen'],
    [/\b(door|doorway|window)\b/i, 'opening'],
    [/\b(lamp|light)\b/i, 'lamp'],
    [/\b(corner|area|nook|zone|rug)\b/i, 'area'],
];

/* The usual den things the first-time checklist walks through. */
export const CHECKLIST = Object.freeze(['sofa', 'desk', 'desk chair', '3D printer', 'painting', 'bookshelf', 'armchair', 'coffee table',
    'floor lamp', 'window', 'door', 'TV']);

/** Kind and typical size for a name: an exact usual name, else a keyword, else "other". */
export function guess(name) {
    const n = String(name || '').trim().toLowerCase();
    const exact = NAMES.find(([nm]) => nm.toLowerCase() === n);
    if (exact) return { kind: exact[1], size: exact[2].slice() };
    for (const [re, kind] of KEYWORDS) if (re.test(n)) {
        const near = NAMES.find(([nm, k]) => k === kind && n.includes(nm.toLowerCase()));
        return { kind, size: (near ? near[2] : KIND_DEFAULT[kind]).slice() };
    }
    return { kind: 'other', size: KIND_DEFAULT.other.slice() };
}

/** Up to 6 usual names for what's typed (exact, then starting with it, then a word starting with it, then containing
 *  it; shorter first), then what was typed itself when it isn't a usual name. */
export function suggestions(typed) {
    const t = String(typed || '').trim(), low = t.toLowerCase();
    const rank = nm => { const n = nm.toLowerCase(); return n === low ? 0 : n.startsWith(low) ? 1 : n.includes(' ' + low) ? 2 : 3; };
    const known = NAMES.filter(([nm]) => !low || nm.toLowerCase().includes(low))
        .slice().sort((a, b) => (low ? rank(a[0]) - rank(b[0]) || a[0].length - b[0].length : 0))
        .slice(0, 6).map(([name, kind]) => ({ name, kind, typed: false }));
    if (t && !NAMES.some(([nm]) => nm.toLowerCase() === low)) known.push({ name: t, kind: guess(t).kind, typed: true });
    return known;
}

export function cleanName(value) { return String(value || '').replace(/\s+/g, ' ').trim().slice(0, NAME_MAX).trim(); }
/** null when the name can be saved, else a sentence saying why not. */
export function nameProblem(name, labels = [], exceptId = null) {
    const n = cleanName(name);
    if (!n) return 'Give it a name.';
    if (!NAME.test(n)) return "Use letters, digits, spaces and - ' & / ( ) . only.";
    if (labels.some(l => l.id !== exceptId && String(l.name).toLowerCase() === n.toLowerCase())) return 'Another label already has this name.';
    return null;
}
/** "painting", then "painting 2", "painting 3"… when the name is taken. */
export function uniqueName(name, labels = [], exceptId = null) {
    const base = cleanName(name), used = new Set(labels.filter(l => l.id !== exceptId).map(l => String(l.name).toLowerCase()));
    if (!used.has(base.toLowerCase())) return base;
    for (let i = 2; i < 1000; i++) { const next = `${base.slice(0, NAME_MAX - 4).trim()} ${i}`; if (!used.has(next.toLowerCase())) return next; }
    return base;
}
export function newId(name, now = Date.now(), salt = Math.random()) {
    const slug = String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'label';
    return `l-${slug}-${Math.floor(now).toString(36).slice(-5)}${Math.floor(salt * 1296).toString(36).padStart(2, '0')}`;
}

/* ---------------- geometry ---------------- */
const rad = d => d * Math.PI / 180, deg = r => r * 180 / Math.PI;
const r3 = v => Math.round(v * 1000) / 1000;
export const normYaw = y => { const v = ((Math.round(Number(y) * 10) / 10) % 360 + 360) % 360; return v >= 360 ? 0 : v; };
export const front = yaw => [-Math.sin(rad(yaw)), Math.cos(rad(yaw))];
export const yawFromFront = (fx, fy) => normYaw(deg(Math.atan2(-fx, fy)));
export function toLocal(box, x, y) {
    const a = rad(box.yaw_deg), dx = x - box.centre_m[0], dy = y - box.centre_m[1];
    return [dx * Math.cos(a) + dy * Math.sin(a), -dx * Math.sin(a) + dy * Math.cos(a)];
}
export function toWorld(box, lx, ly) {
    const a = rad(box.yaw_deg);
    return [box.centre_m[0] + lx * Math.cos(a) - ly * Math.sin(a), box.centre_m[1] + lx * Math.sin(a) + ly * Math.cos(a)];
}
/** The four floor corners: back left, back right, front right, front left. */
export function footprint(box) {
    const [w, d] = box.size_m;
    return [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) => toWorld(box, sx * w / 2, sy * d / 2));
}
/** Of the four room-aligned turns, the one whose front faces the viewer. */
export function facingYaw(at, viewer) {
    const tx = viewer[0] - at[0], ty = viewer[1] - at[1];
    let best = 0, score = -Infinity;
    for (const yaw of [0, 90, 180, 270]) { const [fx, fy] = front(yaw); const s = fx * tx + fy * ty; if (s > score + 1e-9) { score = s; best = yaw; } }
    return best;
}

/**
 * Where a new label's box goes, from one click (plan.md P4).
 *   hit: { point:[x,y,z], normal:[x,y,z] } on the scan, in the apartment frame.
 *   viewer: the camera's [x, y] in the same frame, so the box's front faces you.
 *   fit: an optional flat-top rectangle from Home's named-aims fit ({center, size:[w,h], up}), used for a table or
 *        desk clicked on its top when each side is 20 cm to 3 m.
 *   roomTop: the ceiling height, which a wall item never crosses.
 * Returns { box, on_wall }.
 */
export function placeBox({ name, kind = null, hit, viewer = [0, 0], fit = null, roomTop = 2.6 }) {
    const g = guess(name), k = kind || g.kind;
    let [w, d, h] = kind && kind !== g.kind ? KIND_DEFAULT[k].slice() : g.size;
    const [px, py, pz] = hit.point, n = hit.normal, hl = Math.hypot(n[0], n[1]);
    const hx = hl > 1e-6 ? n[0] / hl : 0, hy = hl > 1e-6 ? n[1] / hl : 0;
    const vertical = Math.abs(n[2]) < 0.35, up = n[2] > 0.65;
    let c, yaw, onWall = false;
    if (WALL_KINDS.has(k) && vertical) {
        yaw = yawFromFront(hx, hy); onWall = true;
        c = [px + hx * d / 2, py + hy * d / 2, Math.min(Math.max(pz, h / 2), Math.max(h / 2, roomTop - h / 2))];
    } else if (k === 'area') {
        yaw = facingYaw([px, py], viewer); c = [px, py, h / 2];
    } else if (k === 'table' && up && fit && Math.min(...fit.size) >= 0.2 && Math.max(...fit.size) <= 3) {
        // The fit's own `up` lies along its second side; that side is the box's depth, front towards you.
        const [ux, uy] = fit.up, l = Math.hypot(ux, uy) || 1;
        yaw = yawFromFront(ux / l, uy / l); [w, d] = fit.size;
        const [fx, fy] = front(yaw), tx = viewer[0] - fit.center[0], ty = viewer[1] - fit.center[1];
        if (fx * tx + fy * ty < 0) yaw = normYaw(yaw + 180);
        // The top's height is the fitted top's own (the display mesh's), not the coarser collision click's.
        h = Math.max(MIN_SIZE, Number.isFinite(fit.center[2]) ? fit.center[2] : pz); c = [fit.center[0], fit.center[1], h / 2];
    } else {
        let cx = px, cy = py;
        if (vertical) { yaw = yawFromFront(hx, hy); cx = px - hx * d / 2; cy = py - hy * d / 2; } else yaw = facingYaw([px, py], viewer);
        // A side click centres the box on the click's height; within 25 cm of the floor it stands on the floor.
        let bottom = vertical && k !== 'seat' && k !== 'table' ? pz - h / 2 : 0;
        if (bottom < 0.25) bottom = 0;
        let top = bottom + h;
        if (up && pz > 0.15 && k !== 'seat') { top = pz; bottom = k === 'table' ? 0 : Math.max(0, pz - h); }
        h = Math.max(MIN_SIZE, top - bottom); c = [cx, cy, (bottom + top) / 2];
    }
    const clampSize = v => Math.min(MAX_SIZE, Math.max(MIN_SIZE, v));
    return { box: { centre_m: c.map(r3), size_m: [w, d, h].map(v => r3(clampSize(v))), yaw_deg: normYaw(yaw) }, on_wall: onWall };
}

/* ---------------- the record ---------------- */
export function emptyDoc(contentRevision = null) {
    return { schema: SCHEMA, home: 'den', room_id: 'den', content_revision: contentRevision, revision: 0, saved_at: null, labels: [] };
}
const finite = v => typeof v === 'number' && Number.isFinite(v);
const triple = (v, ok) => Array.isArray(v) && v.length === 3 && v.every(x => finite(x) && ok(x));
const exactKeys = (o, keys) => !!o && typeof o === 'object' && !Array.isArray(o)
    && Object.keys(o).length === keys.length && keys.every(k => Object.prototype.hasOwnProperty.call(o, k));
const DOC_KEYS = ['schema', 'home', 'room_id', 'content_revision', 'revision', 'saved_at', 'labels'];
const LABEL_KEYS = ['id', 'name', 'kind', 'box', 'on_wall', 'made_by', 'status', 'updated_at'];
const BOX_KEYS = ['centre_m', 'size_m', 'yaw_deg'];
const optStr = (v, max) => v === null || (typeof v === 'string' && v.length <= max);

/** null when den-live would accept the document, else a short reason in den-live's own words. */
export function docProblem(doc) {
    if (!exactKeys(doc, DOC_KEYS)) return 'key_unknown';
    if (doc.schema !== SCHEMA) return 'schema_wrong';
    if (doc.home !== 'den' || doc.room_id !== 'den') return 'scope_wrong';
    if (!optStr(doc.content_revision, 128) || !optStr(doc.saved_at, 40)) return 'field_invalid';
    if (!Number.isInteger(doc.revision) || doc.revision < 0) return 'revision_invalid';
    if (!Array.isArray(doc.labels)) return 'labels_invalid';
    if (doc.labels.length > MAX_LABELS) return 'too_many_labels';
    const ids = new Set(), names = new Set();
    for (const l of doc.labels) {
        if (!exactKeys(l, LABEL_KEYS) || !exactKeys(l.box, BOX_KEYS)) return 'key_unknown';
        if (typeof l.id !== 'string' || !ID.test(l.id)) return 'id_invalid';
        if (ids.has(l.id)) return 'id_duplicate';
        ids.add(l.id);
        if (typeof l.name === 'string' && l.name.length > NAME_MAX) return 'name_too_long';
        if (typeof l.name !== 'string' || !NAME.test(l.name) || l.name !== l.name.trimEnd()) return 'name_invalid';
        if (names.has(l.name.toLowerCase())) return 'name_duplicate';
        names.add(l.name.toLowerCase());
        if (!KINDS.includes(l.kind)) return 'kind_unknown';
        if (!triple(l.box.centre_m, x => Math.abs(x) <= 100) || !triple(l.box.size_m, x => x >= MIN_SIZE && x <= MAX_SIZE)
            || !finite(l.box.yaw_deg)) return 'box_invalid';
        if (typeof l.on_wall !== 'boolean') return 'field_invalid';
        if (!['owner', 'system'].includes(l.made_by) || !['confirmed', 'suggested', 'check'].includes(l.status)) return 'field_invalid';
        if (!optStr(l.updated_at, 40)) return 'field_invalid';
    }
    return new TextEncoder().encode(JSON.stringify(doc)).length > MAX_BYTES ? 'too_large' : null;
}

/** A label as saved: a clean name, a rounded box, yaw in [0, 360). */
export function makeLabel({ id, name, kind, box, on_wall = false, made_by = 'owner', status = 'confirmed', updated_at = null }) {
    return { id, name: cleanName(name), kind, box: { centre_m: box.centre_m.map(r3), size_m: box.size_m.map(r3), yaw_deg: normYaw(box.yaw_deg) },
        on_wall: !!on_wall, made_by, status, updated_at };
}

/** False when the saved labels were placed on another scan than the one Home shows ("check these"). */
export function sameScan(doc, contentRevision) {
    return !doc || !doc.labels.length || !doc.content_revision || doc.content_revision === contentRevision;
}

/** The checklist items that already have a label: "blue painting" counts as a painting. */
export function checklistDone(labels) {
    const names = labels.map(l => String(l.name).toLowerCase());
    return new Set(CHECKLIST.filter(item => {
        const n = item.toLowerCase();
        return names.some(m => m === n || m.startsWith(n + ' ') || m.endsWith(' ' + n));
    }));
}
