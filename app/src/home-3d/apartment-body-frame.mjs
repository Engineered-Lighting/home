/* The live body's frame contract (den-live-body-1) and the pure steps that turn frames into what the body layer draws.
 * A frame is one person's body points, already in the apartment frame (z up, metres, floor at 0), with each point's
 * provenance, plus the pose joints when the producer has them. Whoever produces frames does the depth work (the
 * empty-room cut, the near-limb cut, back fill, held-object segmentation); nothing here reads a camera.
 *
 * Point fields are little-endian base64, so a frame travels as JSON:
 *   key          uint32  the point's depth-pixel key, strictly ascending (front i, back N+i, side 2N+2i+k). The camera
 *                        is fixed, so equal keys in two frames are the same surface spot: that is what interpolates.
 *   position_mm  int16x3 apartment frame, millimetres
 *   normal       int8x3  unit normal x 127 (optional; 0,0,0 = unknown)
 *   kind         uint8   KIND below
 *   alpha        uint8   feather at cut edges, 0-255 (optional, default 255)
 *   motion       uint8   how fast this spot moved, 0-255 (optional, default 0)
 *   head         uint8   1-255 inside the head region, drawn thinner (optional, default 0) */

export const BODY_SCHEMA = 'den-live-body-1';
export const FACE_FREE_BODY_SCHEMA = 'den-live-body-2';
export const MAX_POINTS = 16384;
export const KIND = Object.freeze({ measured: 0, backFill: 1, guessed: 3, remembered: 4, heldObject: 5 });
const KINDS = new Set(Object.values(KIND));
export const COCO = Object.freeze(['nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear', 'left_shoulder', 'right_shoulder',
    'left_elbow', 'right_elbow', 'left_wrist', 'right_wrist', 'left_hip', 'right_hip', 'left_knee', 'right_knee', 'left_ankle', 'right_ankle']);
const JOINT_NAMES = new Set(COCO);
const JOINT_STATUS = new Set(['measured', 'placed', 'uncertain']);
const FIELDS = { key: 4, position_mm: 6, normal: 3, kind: 1, alpha: 1, motion: 1, head: 1 };
const REQUIRED = ['key', 'position_mm', 'kind'];
const BODY_V2_FIELDS = new Set(['schema', 'home', 'track_id', 'seq', 'capture_ts', 'content_revision', 'transform_id', 'source', 'points', 'joints', 'object']);
const BODY_V2_POINT_FIELDS = new Set(['count', 'key', 'position_mm', 'normal', 'kind', 'alpha', 'motion']);
const BODY_V2_JOINTS = new Set(COCO.slice(5));

const finite = value => typeof value === 'number' && Number.isFinite(value);
const finite3 = value => Array.isArray(value) && value.length === 3 && value.every(v => finite(v) && Math.abs(v) <= 100);
const validId = value => typeof value === 'string' && value.length > 0 && value.length <= 64;
const B64 = /^[A-Za-z0-9+/]*={0,2}$/;

function bytes(text) {
    if (typeof text !== 'string' || text.length % 4 !== 0 || !B64.test(text)) return null;
    let raw;
    try { raw = atob(text); } catch (_) { return null; }
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
}

/* The frame's own checks, shape only: a frame that fails is ignored whole, never drawn in part. */
export function validBodyFrame(frame, { schema = BODY_SCHEMA } = {}) {
    if (!frame || frame.schema !== schema || !validId(frame.track_id) || !finite(frame.capture_ts)) return false;
    const p = frame.points;
    if (!p || !Number.isSafeInteger(p.count) || p.count < 0 || p.count > MAX_POINTS) return false;
    if (schema === FACE_FREE_BODY_SCHEMA && (frame.source !== 'joints' || !validId(frame.home)
        || !Object.keys(frame).every(key => BODY_V2_FIELDS.has(key))
        || !Object.keys(p).every(key => BODY_V2_POINT_FIELDS.has(key))
        || frame.joints != null && (!Array.isArray(frame.joints) || !frame.joints.every(j => j && BODY_V2_JOINTS.has(j.name)
            && Object.keys(j).every(key => key === 'name' || key === 'xyz_m' || key === 'status')))
        || frame.object != null && !Object.keys(frame.object).every(key => key === 'label' || key === 'confidence'))) return false;
    for (const name of Object.keys(FIELDS)) {
        if (p[name] == null) { if (REQUIRED.includes(name)) return false; continue; }
        if (typeof p[name] !== 'string' || p[name].length !== 4 * Math.ceil(p.count * FIELDS[name] / 3)) return false;
    }
    if (frame.joints != null && (!Array.isArray(frame.joints) || frame.joints.length > COCO.length
        || !frame.joints.every(j => j && JOINT_NAMES.has(j.name) && finite3(j.xyz_m) && JOINT_STATUS.has(j.status ?? 'measured')))) return false;
    if (frame.joints && new Set(frame.joints.map(j => j.name)).size !== frame.joints.length) return false;
    if (frame.object != null && (typeof frame.object !== 'object' || frame.object.label != null && (typeof frame.object.label !== 'string' || frame.object.label.length > 32)
        || frame.object.confidence != null && !(finite(frame.object.confidence) && frame.object.confidence >= 0 && frame.object.confidence <= 1))) return false;
    return true;
}

/* A valid frame's arrays, or null when its bytes don't hold what its shape says (bad base64, keys out of order,
 * an unknown kind). Positions come back in metres. */
export function decodeBodyFrame(frame, options) {
    if (!validBodyFrame(frame, options)) return null;
    const n = frame.points.count, p = frame.points, raw = {};
    for (const name of Object.keys(FIELDS)) {
        if (p[name] == null) continue;
        const b = bytes(p[name]);
        if (!b || b.length !== n * FIELDS[name]) return null;
        raw[name] = new DataView(b.buffer, b.byteOffset, b.byteLength);
    }
    const keys = new Uint32Array(n), pos = new Float32Array(n * 3), nrm = new Float32Array(n * 3), kind = new Uint8Array(n);
    const alpha = new Float32Array(n), motion = new Float32Array(n), head = new Float32Array(n);
    let sx = 0, sy = 0, sz = 0, sn = 0, objects = 0;
    for (let i = 0; i < n; i++) {
        keys[i] = raw.key.getUint32(i * 4, true);
        if (i > 0 && keys[i] <= keys[i - 1]) return null;
        for (let k = 0; k < 3; k++) pos[i * 3 + k] = raw.position_mm.getInt16(i * 6 + k * 2, true) / 1000;
        if (raw.normal) {
            const x = raw.normal.getInt8(i * 3), y = raw.normal.getInt8(i * 3 + 1), z = raw.normal.getInt8(i * 3 + 2), l = Math.hypot(x, y, z);
            if (l > 0) { nrm[i * 3] = x / l; nrm[i * 3 + 1] = y / l; nrm[i * 3 + 2] = z / l; }
        }
        kind[i] = raw.kind.getUint8(i);
        if (!KINDS.has(kind[i]) || frame.schema === FACE_FREE_BODY_SCHEMA && kind[i] !== KIND.guessed) return null;
        alpha[i] = raw.alpha ? raw.alpha.getUint8(i) / 255 : 1;
        motion[i] = raw.motion ? raw.motion.getUint8(i) / 255 : 0;
        head[i] = raw.head ? raw.head.getUint8(i) / 255 : 0;
        if (kind[i] === KIND.heldObject) objects++;
        else if (kind[i] === KIND.measured || kind[i] === KIND.backFill || frame.schema === FACE_FREE_BODY_SCHEMA) {
            sx += pos[i * 3]; sy += pos[i * 3 + 1]; sz += pos[i * 3 + 2]; sn++;
        }
    }
    const joints = {}, jointStatus = {};
    for (const j of frame.joints || []) { joints[j.name] = [...j.xyz_m]; jointStatus[j.name] = j.status ?? 'measured'; }
    return { trackId: frame.track_id, seq: Number.isSafeInteger(frame.seq) ? frame.seq : null, captureTs: frame.capture_ts, n, keys, pos, nrm, kind, alpha, motion, head,
        objects, centroid: sn ? [sx / sn, sy / sn, sz / sn] : null, joints, jointStatus,
        object: frame.object ? { label: frame.object.label ?? null, confidence: frame.object.confidence ?? null } : null };
}

/* Scratch arrays for drawing up to `capacity` points. */
export function drawBuffers(capacity = MAX_POINTS * 2) {
    return { capacity, n: 0, pos: new Float32Array(capacity * 3), nrm: new Float32Array(capacity * 3), kind: new Float32Array(capacity),
        alpha: new Float32Array(capacity), motion: new Float32Array(capacity), head: new Float32Array(capacity), rand: new Float32Array(capacity) };
}

/* A stable 0-1 number per key, so each point keeps its own flicker phase from frame to frame. */
export function keyRandom(key) { const x = Math.sin(key * 12.9898 + 78.233) * 43758.5453; return x - Math.floor(x); }

/* Interpolate from frame a to frame b by s (0..1, eased): equal keys move from a's spot to b's, a's unmatched points
 * fade out, b's fade in. Writes into out (drawBuffers) and returns the number of points written. */
export function mergeFrames(a, b, s, out) {
    const e = Math.max(0, Math.min(1, s)), w = e * e * (3 - 2 * e), na = a ? a.n : 0, nb = b ? b.n : 0;
    let i = 0, j = 0, m = 0;
    const put = (F, k, fade) => {
        for (let q = 0; q < 3; q++) { out.pos[m * 3 + q] = F.pos[k * 3 + q]; out.nrm[m * 3 + q] = F.nrm[k * 3 + q]; }
        out.kind[m] = F.kind[k]; out.alpha[m] = F.alpha[k] * fade; out.motion[m] = F.motion[k]; out.head[m] = F.head[k]; out.rand[m] = keyRandom(F.keys[k]);
    };
    while ((i < na || j < nb) && m < out.capacity) {
        const ka = i < na ? a.keys[i] : Infinity, kb = j < nb ? b.keys[j] : Infinity;
        if (ka === kb) {
            put(b, j, 1);
            for (let q = 0; q < 3; q++) {
                out.pos[m * 3 + q] = a.pos[i * 3 + q] + (b.pos[j * 3 + q] - a.pos[i * 3 + q]) * w;
                out.nrm[m * 3 + q] = a.nrm[i * 3 + q] + (b.nrm[j * 3 + q] - a.nrm[i * 3 + q]) * w;
            }
            out.alpha[m] = a.alpha[i] + (b.alpha[j] - a.alpha[i]) * w;
            out.head[m] = a.head[i] + (b.head[j] - a.head[i]) * w;
            i++; j++; m++;
        } else if (ka < kb) { if (w < 0.999) { put(a, i, 1 - w); m++; } i++; }
        else { put(b, j, w); m++; j++; }
    }
    out.n = m;
    return m;
}

/* The joints between two frames (b's joints where a has none). */
export function mergeJoints(a, b, s) {
    const e = Math.max(0, Math.min(1, s)), out = {};
    for (const name of Object.keys(b?.joints || {})) {
        const p = a?.joints?.[name], q = b.joints[name];
        out[name] = p ? p.map((v, k) => v + (q[k] - v) * e) : [...q];
    }
    return out;
}

/* How far out the view is for this person: 0 at Home's closest step, 1 at the overview (log distance, the same ratio
 * Home's point sizing uses). */
export function zoomRatio(distance, closest = 4.8, overview = 23) {
    if (!finite(distance) || distance <= 0) return 1;
    return Math.max(0, Math.min(1, Math.log(distance / closest) / Math.log(overview / closest)));
}

/* A frame from plain arrays, for producers written in JavaScript and for tests. points: [{key, xyz_m, kind, normal?,
 * alpha?, motion?, head?}] in any order. */
export function encodeBodyFrame({ trackId, captureTs, seq = null, points = [], joints = null, object = null, home = 'den', schema = BODY_SCHEMA }) {
    const sorted = [...points].sort((a, b) => a.key - b.key), n = sorted.length;
    const field = (size, write) => {
        const b = new Uint8Array(n * size), v = new DataView(b.buffer);
        sorted.forEach((pt, i) => write(v, i, pt));
        let text = '';
        for (let i = 0; i < b.length; i++) text += String.fromCharCode(b[i]);
        return btoa(text);
    };
    const clamp16 = x => Math.max(-32768, Math.min(32767, Math.round(x * 1000)));
    const u8 = (x, d) => Math.max(0, Math.min(255, Math.round((x ?? d) * 255)));
    const faceFree = schema === FACE_FREE_BODY_SCHEMA;
    const frame = { schema, home, track_id: trackId, seq, capture_ts: captureTs, points: {
        count: n,
        key: field(4, (v, i, pt) => v.setUint32(i * 4, pt.key, true)),
        position_mm: field(6, (v, i, pt) => pt.xyz_m.forEach((x, k) => v.setInt16(i * 6 + k * 2, clamp16(x), true))),
        normal: field(3, (v, i, pt) => (pt.normal || [0, 0, 0]).forEach((x, k) => v.setInt8(i * 3 + k, Math.max(-127, Math.min(127, Math.round(x * 127)))))),
        kind: field(1, (v, i, pt) => v.setUint8(i, pt.kind ?? (faceFree ? KIND.guessed : KIND.measured))),
        alpha: field(1, (v, i, pt) => v.setUint8(i, u8(pt.alpha, 1))),
        motion: field(1, (v, i, pt) => v.setUint8(i, u8(pt.motion, 0))),
    } };
    if (faceFree) frame.source = 'joints';
    else frame.points.head = field(1, (v, i, pt) => v.setUint8(i, u8(pt.head, 0)));
    if (joints) frame.joints = Object.entries(joints).map(([name, value]) => Array.isArray(value)
        ? { name, xyz_m: value, status: 'measured' } : { name, xyz_m: value.xyz_m, status: value.status ?? 'measured' });
    if (object) frame.object = object;
    return frame;
}
