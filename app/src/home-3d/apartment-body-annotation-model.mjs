/* What the live body's labels say and where they sit, as plain state: no DOM, no three.js. The labels layer projects
 * and draws what this decides. The rules (owner-approved, 2026-11-11):
 * - Name, never face. An unknown person is "Someone". The house's response is said; facts about the person aren't.
 * - Say less zoomed out: name at the overview, name and what they're doing at room zoom, everything up close.
 * - Nothing flickers: activity changes and clears only after 2 s (a new one shows at once when none is shown), the
 *   held-object tag stays 1.5 s past its last sighting, and the caption changes side only past 42% / 66% of the width.
 * - Facing is the skeleton's: the shoulder and hip lines (facingOf below), steadied by a quick 0.12 s average, then
 *   followed with a 0.9 s time constant while the gap is noise-sized and 0.1 s once it passes about 20 degrees, so a
 *   real turn still lands within a second. The sight line's origin (the head) is smoothed over 0.25 s and the ring
 *   height over 0.5 s. */

export const DEBOUNCE_ACTIVITY_S = 2, OBJECT_HOLD_S = 1.5, SIDE_FLIP = Object.freeze([0.42, 0.66]);
export const SEATED_PELVIS_M = 0.72, WALKING_MPS = 0.25;
const DEG = Math.PI / 180;

const mid = (a, b) => [0, 1, 2].map(k => (a[k] + b[k]) / 2);
const ease = (dt, tau) => 1 - Math.exp(-Math.max(0, dt) / tau);
const has = (J, ...names) => names.every(name => Array.isArray(J?.[name]));

/* Which way the person faces, on the floor plane (z up), as a unit 3-vector with z 0, or null. From the skeleton: the
 * line across the shoulders is about 40 cm wide, so its normal is steady, where nose
 * against the ear midpoint is about 10 cm of noisy depth (owner, 2026-11-12: "knowing the direction my skeleton is
 * facing"). The shoulders' line when both are there, else the hips'. Forward is that line turned a quarter to the right (the person's left shoulder is on their left). The nose
 * only settles a left/right swap: when it sits clearly behind the torso, forward is flipped. Without shoulders, nose
 * against the ears is the fallback. */
export function facingOf(joints) {
    let ax = 0, ay = 0;   // the shoulders when both are there (nearly always seen); else the hips
    const pair = has(joints, 'left_shoulder', 'right_shoulder') ? ['left_shoulder', 'right_shoulder'] : has(joints, 'left_hip', 'right_hip') ? ['left_hip', 'right_hip'] : null;
    if (pair) { ax = joints[pair[0]][0] - joints[pair[1]][0]; ay = joints[pair[0]][1] - joints[pair[1]][1]; }
    const across = Math.hypot(ax, ay);
    if (across > 0.1) {
        let fx = ay / across, fy = -ax / across;
        if (has(joints, 'nose', 'left_shoulder', 'right_shoulder')) {
            const chest = mid(joints.left_shoulder, joints.right_shoulder);
            if ((joints.nose[0] - chest[0]) * fx + (joints.nose[1] - chest[1]) * fy < -0.04) { fx = -fx; fy = -fy; }
        }
        return [fx, fy, 0];
    }
    if (has(joints, 'nose', 'left_ear', 'right_ear')) {
        const ears = mid(joints.left_ear, joints.right_ear), v = [joints.nose[0] - ears[0], joints.nose[1] - ears[1], 0];
        return Math.hypot(v[0], v[1]) > 1e-6 ? v : null;
    }
    return null;
}

/* 0 overview, 1 room, 2 close: from zoomRatio (0 closest .. 1 overview). */
export function detailFor(zoomT) { return zoomT < 0.35 ? 2 : zoomT < 0.8 ? 1 : 0; }

export function formatDuration(seconds) {
    if (!(seconds > 0)) return '';
    if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
    return `${Math.floor(seconds / 3600)} h ${Math.round(seconds % 3600 / 60)} min`;
}

/* The head's facing direction, steadied as described above. step(raw, dt) takes a 3-vector (any length) or null and
 * returns a unit vector, or null until the first sample. */
export function createFacingFilter({ quick = 0.12, slow = 0.9, fast = 0.1, gapFrom = 7 * DEG, gapTo = 20 * DEG } = {}) {
    let quickDir = null, shown = null;
    const unit = v => { const l = Math.hypot(v[0], v[1], v[2]) || 1; return v.map(x => x / l); };
    return {
        step(raw, dt) {
            if (!raw || !raw.every(Number.isFinite) || Math.hypot(...raw) < 1e-9) return shown ? unit(shown) : null;
            const r = unit(raw);
            if (!shown) { quickDir = r.slice(); shown = r.slice(); return unit(shown); }
            const kq = ease(dt, quick);
            for (let k = 0; k < 3; k++) quickDir[k] += (r[k] - quickDir[k]) * kq;
            const a = unit(quickDir), b = unit(shown), gap = Math.acos(Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2])));
            const u = Math.max(0, Math.min(1, (gap - gapFrom) / (gapTo - gapFrom))), tau = slow + (fast - slow) * u * u * (3 - 2 * u);
            const ks = ease(dt, tau);
            for (let k = 0; k < 3; k++) shown[k] += (quickDir[k] - shown[k]) * ks;
            return unit(shown);
        },
        reset() { quickDir = null; shown = null; },
    };
}

/* The labels' state for one person. step() takes this frame's inputs and returns what to show. */
export function createBodyAnnotationState() {
    const facing = createFacingFilter();
    let activity = '', candidate = '', candidateAt = -Infinity, since = null, objectUntil = -Infinity, objectLabel = null, objectConfidence = null;
    let right = true, ringZ = null, eye = null, speed = 0, lastCentroid = null;
    return {
        /* now (s), dt (s), joints {name: [x,y,z]} in the apartment frame, centroid [x,y,z], activity (the house's
         * word for what they're doing, or null), object: {label, confidence} | null when this frame shows none,
         * headScreenX and width (px) for the caption's side, zoomT (0 closest .. 1 overview). */
        step({ now, dt, joints, centroid, activity: incoming = null, object = null, headScreenX = null, width = null, zoomT = 1 }) {
            if (centroid && lastCentroid && dt > 0) {
                const v = Math.hypot(centroid[0] - lastCentroid[0], centroid[1] - lastCentroid[1]) / dt;
                speed += (v - speed) * Math.min(1, dt / 0.4);
            }
            if (centroid) lastCentroid = centroid.slice();
            const pelvisZ = has(joints, 'left_hip', 'right_hip') ? (joints.left_hip[2] + joints.right_hip[2]) / 2 : null;
            const seated = pelvisZ != null && pelvisZ < SEATED_PELVIS_M, walking = !seated && speed > WALKING_MPS;
            const posture = pelvisZ == null ? '' : seated ? 'Seated' : walking ? 'Walking' : 'Standing';

            const wanted = typeof incoming === 'string' ? incoming.slice(0, 40) : '';
            if (wanted !== candidate) { candidate = wanted; candidateAt = now; }
            if (candidate !== activity && (!activity && candidate || now - candidateAt >= DEBOUNCE_ACTIVITY_S)) { activity = candidate; since = activity ? now : null; }

            if (object) { objectUntil = now + OBJECT_HOLD_S; objectLabel = object.label ?? null; objectConfidence = object.confidence ?? null; }
            const holding = now < objectUntil;

            if (Number.isFinite(headScreenX) && width > 0) {
                if (headScreenX < width * SIDE_FLIP[0]) right = true;
                else if (headScreenX > width * SIDE_FLIP[1]) right = false;
            }

            const ringTarget = seated ? Math.max(0.02, pelvisZ - 0.1) : 0.01;
            ringZ = ringZ == null ? ringTarget : ringZ + (ringTarget - ringZ) * ease(dt, 0.5);

            let dir = null, head = null;
            const raw = facingOf(joints);
            if (raw) dir = facing.step(raw, dt);
            if (has(joints, 'nose', 'left_ear', 'right_ear')) {
                const ears = mid(joints.left_ear, joints.right_ear);
                head = ears.map((x, k) => x + (joints.nose[k] - x) * 0.3);
                eye = eye ? eye.map((x, k) => x + (head[k] - x) * ease(dt, 0.25)) : head.slice();
            }
            return { detail: detailFor(zoomT), right, posture, seated, walking, speed, activity, since: activity && since != null ? now - since : 0,
                object: holding ? { label: objectLabel, confidence: objectConfidence } : null,
                ringZ, ringRadius: seated ? 0.42 : 0.45, head, eye, facing: dir, facingAz: dir ? Math.atan2(dir[1], dir[0]) : null };
        },
        reset() { facing.reset(); activity = ''; candidate = ''; since = null; objectUntil = -Infinity; ringZ = null; eye = null; speed = 0; lastCentroid = null; },
    };
}

/* The caption's lines for a detail level. person: {name, known, source, confidence} from Home's identity store (or
 * null); house: what the house did, e.g. "Reading light · 70%" (or null). */
export function captionLines(person, state, house = null) {
    const known = !!(person && person.known !== false && typeof person.name === 'string' && person.name.trim());
    const name = known ? person.name.trim().slice(0, 40) : 'Someone';
    const doing = [state.posture, state.activity].filter(Boolean).join(' · ');
    const lines = { name, doing: '', provenance: '', house: '' };
    if (state.detail >= 1) lines.doing = state.detail === 1 ? (state.activity || state.posture) : doing + (state.since ? ' · ' + formatDuration(state.since) : '');
    if (state.detail >= 2) {
        const pct = Number.isFinite(person?.confidence) ? ` · ${Math.round(person.confidence * 100)}%` : '';
        lines.provenance = known ? `${person.source || 'identity'}${pct}` : 'not recognised';
        lines.house = typeof house === 'string' ? house.slice(0, 60) : '';
    }
    return lines;
}

/* The glass card's rows, shown on tap at any zoom: everything the caption would say up close. */
export function cardRows(person, state, house = null) {
    const lines = captionLines(person, { ...state, detail: 2 }, house);
    return { name: lines.name, confidence: lines.provenance.includes('%') ? lines.provenance.split(' · ').pop() : '',
        posture: state.posture, activity: state.activity ? `${state.activity}${state.since ? ' · ' + formatDuration(state.since) : ''}` : '',
        house: lines.house, known: lines.provenance !== 'not recognised' };
}
