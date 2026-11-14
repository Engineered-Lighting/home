/* What the den's live caption says for one tracked person: pure, no DOM, no network (home-den-inference-caption.mjs
 * draws it). The design is direction A of docs/design/den-inference/SPEC.md: a caption beside the person with who
 * they are, what Qwen last said they're doing and how long that answer stays good, what they hold, what Qwen saw,
 * and what the light is doing for them.
 *
 * The rules, so the caption never claims more than the data holds:
 * - Qwen's answer shows only while it's good: at most ANSWER_S (10 s, contract 3) after it was given, and no longer
 *   than its own activity.valid_for_s when den-live sends one. Its age is den-live's own clock (the event's emitted_ts
 *   less the answer's at) plus how long ago this page got the event, as home-den-live-qwen.mjs counts it. An expired
 *   answer goes at once: there's no debounce on expiry.
 * - The facts carry no confidence. In its place the caption shows Qwen's visible_basis ("book in hands, pages open"),
 *   when den-live sends it.
 * - Qwen asks about a seated person only. A track says so with seated (contract 2, planned); until then, the track with
 *   the answer, or the only track, is the one asked about.
 * - A name needs identity with a kind of household and a match at or over IDENTITY_STRONG; a weaker household match
 *   reads "Name?", a face that matched nobody "Guest", and no face "Someone". Never a face image.
 * - The light line belongs to a person only when the spot snapshot's subject names their track, in the same camera
 *   epoch and association revision.
 * Fields den-live doesn't send yet (identity, activity.facts, activity.basis, activity.valid_for_s,
 * health.qwen.last_outcome, health.qwen.inflight_since, seated) are read when present and ignored otherwise. */

export const ANSWER_S = 10;
export const IDENTITY_STRONG = 0.8;   // placeholder until the face model's own threshold is chosen
const ACTIVITY = Object.freeze({ reading: 'Reading', hobby: 'Close-up hobby', phone_use: 'On the phone',
    computer_work: 'At a computer', resting: 'Resting', transition: 'Settling in', other: 'Something else', unclear: 'Unclear' });
const OBJECT = Object.freeze({ book: 'Book', phone: 'Phone', cup: 'Cup', craft: 'Craft', other: 'Something' });
const BASIS = Object.freeze({ person_visible: 'person in view', no_person_visible: 'no one in view',
    multiple_people_visible: 'two people in view', subject_partly_out_of_frame: 'partly out of view', seated_posture: 'seated',
    standing_posture: 'standing', lying_or_reclined: 'reclined', moving_between_frames: 'moving', still_between_frames: 'still',
    book_in_hands: 'book in hands', book_pages_open: 'pages open', book_closed: 'book closed', phone_in_hands: 'phone in hands',
    phone_screen_toward_face: 'screen toward face', cup_in_hands: 'cup in hands', hands_on_keyboard: 'hands on keys',
    hands_on_mouse: 'hand on mouse', hands_resting: 'hands resting', head_down: 'head down',
    head_toward_monitor_side: 'head toward screen', eyes_closed: 'eyes closed', low_light_or_blur: 'low light' });
const OUTCOME = Object.freeze({ unbound_two_people: "Can't tell whose answer: two people in view",
    unbound_person_changed: 'Answer dropped: the person changed', failed: "Qwen didn't answer" });
export const REASON = Object.freeze({ task: 'reading light', pre_aim: 'aimed, dark, ready', task_released: 'fading out',
    stopped: 'Stop is holding', off: 'automation off' });
const POSTURE = Object.freeze({ sitting: 'Seated', standing: 'Standing', walking: 'Walking', lying: 'Lying down' });

const finite = value => typeof value === 'number' && Number.isFinite(value);
const words = (text, max = 80) => (typeof text === 'string' ? text : '').replace(/\s+/g, ' ').trim().slice(0, max);
const own = (object, key) => object != null && typeof object === 'object' && Object.hasOwn(object, key) ? object[key] : undefined;

/* Who: { name, kind: household | guest | unknown, strength: strong | weak | none, score, age_s }. */
export function identityOf(track, event, sinceEventS = 0) {
    const id = own(track, 'identity');
    const age = id && finite(id.at) && finite(event?.emitted_ts) ? Math.max(0, event.emitted_ts - id.at + Math.max(0, sinceEventS)) : null;
    if (id && id.kind === 'household' && words(id.label, 40) && finite(id.score)) {
        const strong = id.score >= IDENTITY_STRONG;
        return { name: strong ? words(id.label, 40) : `${words(id.label, 40)}?`, kind: 'household', strength: strong ? 'strong' : 'weak',
            score: id.score, age_s: age };
    }
    if (id && id.kind === 'guest') return { name: 'Guest', kind: 'guest', strength: 'none', score: finite(id.score) ? id.score : null, age_s: age };
    return { name: 'Someone', kind: 'unknown', strength: 'none', score: null, age_s: null };
}

/* Qwen's current answer for this track, or null once it has expired. */
export function answerOf(track, event, sinceEventS = 0) {
    const a = own(track, 'activity');
    if (!a || typeof a.word !== 'string' || !finite(a.at) || !finite(event?.emitted_ts)) return null;
    const since = Math.max(0, sinceEventS);
    const age = Math.max(0, event.emitted_ts - a.at + since);
    const ownLeft = finite(a.valid_for_s) && a.valid_for_s >= 0 ? a.valid_for_s - since : Infinity;
    const left = Math.min(ANSWER_S - age, ownLeft);
    if (!(left > 0)) return null;
    const facts = own(a, 'facts') || {};
    const basis = Array.isArray(a.basis) ? a.basis.filter(token => typeof token === 'string').slice(0, 6).map(token => BASIS[token] || token.replace(/_/g, ' ')) : [];
    return { word: a.word, words: Object.hasOwn(ACTIVITY, a.word) ? ACTIVITY[a.word] : 'Unclear', close_up: a.close_up === true,
        age_s: age, left_s: left, life: Math.max(0, Math.min(1, left / ANSWER_S)), at: a.at,
        object: Object.hasOwn(OBJECT, facts.held_object) ? OBJECT[facts.held_object] : null, basis,
        request_id: typeof a.request_id === 'string' ? a.request_id : null };
}

/* The house line: what the spot is doing for this track, from the applied spot snapshot (home-den-spot-playback's
 * current()), or null. names: fixture id -> display name. */
export function houseOf(track, spot, names = {}) {
    const subject = spot?.subject;
    if (!subject || subject.track_id !== track?.track_id) return null;
    if (track.camera_epoch != null && subject.camera_epoch != null && subject.camera_epoch !== track.camera_epoch) return null;
    if (track.association_revision != null && subject.association_revision != null && subject.association_revision !== track.association_revision) return null;
    const rows = Array.isArray(spot.fixtures) ? spot.fixtures : Object.entries(spot.fixtures || {}).map(([fixture_id, row]) => ({ fixture_id, ...row }));
    const lit = rows.find(row => row.on === true && finite(row.level) && row.level > 0);
    const aimed = lit || rows.find(row => Array.isArray(row.target_m) && (row.reason === 'pre_aim' || row.reason === 'task'));
    if (spot.stopped) return { words: 'Stop is holding the spots', dark: true, level: null, cct_k: null, fixture_id: null, request_id: null };
    if (!aimed) return null;
    const name = words(names[aimed.fixture_id], 40) || 'Spot';
    if (lit) return { words: `${name} · ${REASON[lit.reason] || 'lit'}`, dark: false, level: lit.level, cct_k: finite(lit.cct_k) ? lit.cct_k : null,
        fixture_id: lit.fixture_id, request_id: typeof subject.request_id === 'string' ? subject.request_id : null, moving: lit.motion_phase === 'moving' };
    return { words: `${name} aimed, dark`, dark: true, level: 0, cct_k: null, fixture_id: aimed.fixture_id, request_id: null, moving: aimed.motion_phase === 'moving' };
}

/* Everything the caption shows for one track of one people event.
 * sinceEventS: seconds since this page received the event. spot: the applied snapshot or null. */
export function captionFor({ event, track, sinceEventS = 0, spot = null, names = {} }) {
    const tracks = Array.isArray(event?.tracks) ? event.tracks : [];
    const answer = answerOf(track, event, sinceEventS);
    const seated = typeof own(track, 'seated') === 'boolean' ? track.seated : null;
    const asked = seated != null ? seated : !!answer || tracks.length === 1;
    const qwen = own(event?.health, 'qwen') || null;
    let line;
    if (answer) line = { kind: 'answer', words: answer.words, detail: `${Math.round(answer.age_s)} s`, life: answer.life };
    else if (!asked) {
        const posture = POSTURE[own(track, 'posture')];
        line = { kind: 'posture', words: posture || (track?.state === 'coasting' ? 'Leaving view' : 'Not seated'), detail: '', life: null };
    } else line = qwenStatus(qwen, event, sinceEventS);
    return { track_id: track?.track_id ?? null, identity: identityOf(track, event, sinceEventS), asked, answer, line,
        house: houseOf(track, spot, names), near: typeof track?.near === 'string' ? words(track.near, 160) : null,
        coasting: track?.state === 'coasting', heading: finite(track?.heading_deg) ? track.heading_deg : null };
}

/* What Qwen is doing when there's no answer to show. */
export function qwenStatus(qwen, event, sinceEventS = 0) {
    if (!qwen || typeof qwen !== 'object') return { kind: 'status', words: '', detail: '', life: 0 };
    const since = Math.max(0, sinceEventS);
    const outcome = own(qwen, 'last_outcome');
    if (outcome && Object.hasOwn(OUTCOME, outcome.kind) && finite(outcome.at) && finite(event?.emitted_ts)
        && event.emitted_ts - outcome.at + since < ANSWER_S && outcome.kind !== 'failed')
        return { kind: 'status', words: OUTCOME[outcome.kind], detail: '', life: 0 };
    const replied = finite(qwen.last_reply_at) && finite(event?.emitted_ts) ? Math.max(0, event.emitted_ts - qwen.last_reply_at + since) : null;
    const detail = replied == null ? '' : replied < 1 ? 'replied just now' : `replied ${Math.round(replied)} s ago`;
    if (qwen.state === 'asking') return { kind: 'status', words: finite(own(qwen, 'inflight_since')) ? 'Asking Qwen…' : 'Waiting for Qwen', detail, life: 0,
        asking: finite(own(qwen, 'inflight_since')) };
    if (qwen.state === 'unavailable') return { kind: 'status', words: `Qwen unavailable${words(qwen.why) ? ` · ${words(qwen.why)}` : ''}`, detail, life: 0 };
    return { kind: 'status', words: `Qwen off${words(qwen.why) ? ` · ${words(qwen.why)}` : ''}`, detail: '', life: 0 };
}

/* 0 overview, 1 room, 2 close: how much the caption says at this zoom (the System labels' detail steps). */
export function detailFor(zoomT) { return zoomT < 0.35 ? 2 : zoomT < 0.8 ? 1 : 0; }
