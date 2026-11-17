const SCHEMA = 'den-virtual-spots-1';
const finite = Number.isFinite;
const AMBIENT_KELVIN = [2200, 6500];
// The render clock's creep: Home maps the coordinator's monotonic clock to its own by the lowest transit seen, and lets
// that floor rise this much per snapshot so a slow drift between the two clocks can't strand it.
const RENDER_CREEP_MS = .5;
const revision = value => Number.isSafeInteger(value) && value >= 0;
const text = value => typeof value === 'string' && value.length > 0;
const object = value => value != null && typeof value === 'object' && !Array.isArray(value);
const sequence = value => Number.isInteger(value) && value >= 0 && value <= 65535;
const point = value => Array.isArray(value) && value.length === 3 && value.every(finite);

function validRow(row) {
    return object(row) && typeof row.on === 'boolean' && finite(row.level) && row.level >= 0 && row.level <= 1
        && (row.on ? row.level > 0 && finite(row.cct_k) : row.level === 0)
        && finite(row.pan_deg) && finite(row.tilt_deg) && (row.target_m == null || point(row.target_m))
        && text(row.reason) && text(row.motion_phase)
        && (row.last_move_trace_id == null
            ? row.move_sequence == null && row.move_decision_id == null
            : text(row.last_move_trace_id) && sequence(row.move_sequence) && text(row.move_decision_id))
        && (row.on ? sequence(row.keep_lit_token) && row.keep_lit_token === row.move_sequence : row.keep_lit_token == null);
}

function validSnapshot(value, home, site) {
    if (!object(value) || value.schema !== SCHEMA || value.home !== home || (value.site != null && value.site !== site)
        || !text(value.epoch) || !revision(value.seq) || !object(value.binding)
        || !text(value.binding.transform_id) || !text(value.binding.scan_revision)
        || !revision(value.binding.equipment_revision) || !text(value.binding.equipment_digest)
        || !finite(value.sampled_mono) || typeof value.on !== 'boolean' || typeof value.stopped !== 'boolean'
        || !revision(value.stop_counter) || !object(value.fixtures)) return false;
    const rows = Object.values(value.fixtures);
    if (!rows.every(validRow) || ((!value.on || value.stopped) && rows.some(row => row.on))) return false;
    if (value.valid_until_mono == null) return rows.every(row => !row.on && row.motion_phase === 'settled');
    return finite(value.valid_until_mono) && value.valid_until_mono >= value.sampled_mono;
}

/* The cue-to-light instrument's paint stamp (den program workstream 03, latency-budget.md "The instrument"): on the frame
 * after the spots are applied, keep {epoch, seq, lit, paint_unix_ms} in window.HomeDenPaintStamps, at most 600, numbers
 * only. The workstation's cue_light.py lines them up with the cue marks and the snapshots on this PC's clock. */
const PAINT_STAMPS_MAX = 600;
function stampPaint(epoch, seq, lit) {
    if (typeof requestAnimationFrame !== 'function' || typeof window === 'undefined') return;
    requestAnimationFrame(() => {
        const stamps = window.HomeDenPaintStamps || (window.HomeDenPaintStamps = []);
        stamps.push({ epoch, seq, lit, paint_unix_ms: Date.now() });
        if (stamps.length > PAINT_STAMPS_MAX) stamps.splice(0, stamps.length - PAINT_STAMPS_MAX);
    });
}

/* The den spot's motion, drawn the way the fixture makes it (den-live's direction.py; the lighting-direction note).
 *
 * The coordinator sends one MOVE per glide, and the board's planner flies both axes along one jerk-limited S-curve on
 * a shared path parameter s, from rest to rest. Each den-virtual-spots-1 row that is moving carries that move as
 * `glide {from_deg, to_deg, start_mono, duration_s, limits: [v, a, j, d], shape: "scurve-7"}`. Home evaluates the same
 * curve every frame instead of stepping between 10 Hz snapshots. The curve is the firmware's own (tools/c12v2/
 * duration.py at 62ea043): an acceleration ramp and a deceleration ramp on one jerk, with the same 100-step bisection.
 * A timed MOVE's min_duration stretch arrives already in the limits (speed / k, acceleration and deceleration / k^2,
 * jerk / k^3), so the duration Home computes is the stretched predicted_ms the board reports. A row's level comes as
 * the coordinator's committed steps, or as its samples of the board's own fade; Home eases between them over
 * SPOT_EASE_MS, about one coordinator tick.
 *
 * Shared vectors: tools/den-live/fixtures/direction-scurve-vectors.json (den-live's test_direction.py writes them). */
export const SPOT_EASE_MS = 120;
const SHAPE = 'scurve-7';

function ramp(vp, A, J) {
    if (vp <= 0) return { vp, A, J, tj: 0, T: 0 };
    if (vp * J >= A * A) return { vp, A, J, tj: A / J, T: vp / A + A / J };
    const tj = Math.sqrt(vp / J);
    return { vp, A, J, tj, T: 2 * tj };
}
const rampDistance = r => r.vp * r.T / 2;
function rampAt(r, t) {
    if (r.T <= 0 || t <= 0) return [0, 0];
    if (t >= r.T) return [rampDistance(r), r.vp];
    const { J, tj, T, vp } = r, alim = J * tj;
    if (t < tj) return [J * t ** 3 / 6, J * t * t / 2];
    if (t < T - tj) return [alim / 6 * (3 * t * t - 3 * t * tj + tj * tj), alim * (t - tj / 2)];
    const rest = T - t;
    return [rampDistance(r) - vp * rest + J * rest ** 3 / 6, vp - J * rest * rest / 2];
}

/* A rest-to-rest move of s from 0 to 1 under speed v, acceleration a, jerk j and deceleration d (per second,
 * squared, cubed and squared; d defaults to a), as the board plans it. */
export function scurve(v, a, j, d = a) {
    if (!(v > 0 && a > 0 && j > 0 && d > 0)) throw RangeError('scurve_limits');
    let up = ramp(v, a, j), down = ramp(v, d, j), peak = v, cruise;
    if (rampDistance(up) + rampDistance(down) <= 1) cruise = (1 - rampDistance(up) - rampDistance(down)) / v;
    else {
        let low = 0, high = v;
        for (let i = 0; i < 100; i++) {
            const middle = (low + high) / 2;
            if (rampDistance(ramp(middle, a, j)) + rampDistance(ramp(middle, d, j)) > 1) high = middle; else low = middle;
        }
        peak = low; cruise = 0; up = ramp(low, a, j); down = ramp(low, d, j);
    }
    const duration = up.T + cruise + down.T;
    function at(t) {
        if (t <= 0) return [0, 0];
        if (t >= duration) return [1, 0];
        if (t < up.T) return rampAt(up, t);
        if (t < up.T + cruise) return [rampDistance(up) + peak * (t - up.T), peak];
        const [gone, speed] = rampAt(down, duration - t);
        return [1 - gone, speed];
    }
    return { duration, s: t => Math.min(1, Math.max(0, at(t)[0])), speed: t => at(t)[1] };
}

/* A snapshot row's glide, checked, on Home's clock: startMs is the glide's start as Home's performance clock reads
 * it, and at(nowMs) the [pan_deg, tilt_deg] it has reached. Null when the row has no glide Home can draw. */
function localGlideCurve(glide, startMs) {
    if (!glide || glide.shape !== SHAPE || !Array.isArray(glide.limits) || ![3, 4].includes(glide.limits.length)
        || !glide.limits.every(value => finite(value) && value > 0) || !finite(startMs)
        || ![glide.from_deg, glide.to_deg].every(pair => Array.isArray(pair) && pair.length === 2 && pair.every(finite))) return null;
    try {
        const curve = scurve(...glide.limits);
        const from = glide.from_deg.slice(), to = glide.to_deg.slice(), endMs = startMs + curve.duration * 1000;
        const at = nowMs => glideAt({ from, to, startMs, curve }, nowMs);
        return { from, to, startMs, endMs, curve, at };
    } catch (_) { return null; }
}

/* [pan_deg, tilt_deg] of a local glide at nowMs: the board's straight line in joint space, along s(t). */
export function glideAt(glide, nowMs) {
    const s = glide.curve.s((nowMs - glide.startMs) / 1000);
    return [glide.from[0] + (glide.to[0] - glide.from[0]) * s, glide.from[1] + (glide.to[1] - glide.from[1]) * s];
}

/* One frame of the level's ease toward its committed step: a first-order lag of SPOT_EASE_MS. */
export function easeLevel(shown, target, elapsedMs) {
    if (!(elapsedMs > 0)) return shown;
    const next = shown + (target - shown) * -Math.expm1(-elapsedMs / SPOT_EASE_MS);
    return Math.abs(target - next) <= .002 ? target : next;
}

export { localGlideCurve as localGlide };

/* The snapshot's room glow (den-live's direction: dimmed under a task light), checked: {level, cct_k}, null for "the
 * coordinator directs none" (Off), undefined when the producer sends none. */
export function ambientOf(value) {
    if (value === undefined) return undefined;
    if (!object(value) || !finite(value.level) || value.level < 0 || value.level > 1 || !finite(value.cct_k)) return null;
    return { level: value.level, cct_k: Math.min(AMBIENT_KELVIN[1], Math.max(AMBIENT_KELVIN[0], value.cct_k)) };
}

/* A person's change, for den-live to learn from (den-manual-1, plan-04b): POST /coordinator/manual once per settled
 * change, with the snapshot Home was drawing. A retry on a dropped connection or a 5xx reuses its event_id. */
export const MANUAL_SCHEMA = 'den-manual-1';
const MANUAL_RETRIES = 2;
const eventId = () => 'home-' + (globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`);

/* post(path, body) → Promise<{status, answer}>: the coordinator's writes through the gateway (home-den-live-view.mjs),
 * or null when this page sends none (look-only). */
export function createSpotPlayback({ home, site, clock = () => performance.now(), onChange = () => {},
    later = setTimeout, cancel = clearTimeout, post = null, makeEventId = eventId, onPlayback = null }) {
    let overlay = null, applied = null, candidate = null, control = null, controlVersion = 0;
    let anchor = null, openedAt = null, transform = null, scan = null, highestSeq = -1, stopCounter = -1, timer = null;
    let renderOffset = null;   // Home's clock minus the coordinator's, in ms: the lowest transit seen, creeping up
    let unsubscribe = null;
    const hold = { off: null, stop: null };
    /* fixture id → its latest manual report: {event_id, epoch, state (sending, recorded, not_recorded, unsupported,
     * failed), from_seq, seen, why_not, learned}. A hold ends from den-live's snapshot only after that snapshot's epoch
     * and seq (≥ from_seq) have shown the fixture's hand, and then show it null. */
    const manual = new Map();
    // fixture id → the snapshot Home had applied just before a burst of changes started: den-live checks "before" at it.
    const burstFrom = new Map();

    const observe = (phase, value) => { try { onPlayback?.(phase, value); } catch (_) { /* optional timing observer */ } };

    function clear() {
        if (timer !== null) cancel(timer);
        timer = null;
        candidate = null;
        observe('clear', null);
        if (!applied) return;
        applied = null;
        overlay?.applyCoordinatorSpots?.(null);
        onChange();
    }
    function attach(nextOverlay) {
        if (overlay === nextOverlay) return;
        disconnect();
        unsubscribe?.(); unsubscribe = null;
        overlay = nextOverlay;
        try { unsubscribe = overlay?.onVirtualFixtureHand?.(reportManual) || null; } catch (_) { unsubscribe = null; }
    }
    function reportManual(change) {
        const id = change?.fixture_id;
        if (typeof id !== 'string' || !post) return;
        if (change.phase === 'start') {
            if (applied) burstFrom.set(id, { epoch: applied.epoch, seq: applied.seq, subject: applied.subject, situation: applied.situation });
            else burstFrom.delete(id);
            return;
        }
        const from = burstFrom.get(id);
        burstFrom.delete(id);
        if (!from) {   // no snapshot to learn against: the hold stands, and nothing is sent
            manual.set(id, { event_id: null, epoch: null, state: 'unsent', from_seq: null, seen: false, why_not: null, learned: null });
            onChange(); return;
        }
        const body = { schema: MANUAL_SCHEMA, event_id: makeEventId(), fixture_id: id, before: change.before, after: change.after,
            at_ms: change.at_ms, snapshot: { epoch: from.epoch, seq: from.seq },
            ...(from.subject !== undefined ? { subject: from.subject } : {}),
            ...(from.situation !== undefined ? { situation: from.situation } : {}) };
        const entry = { event_id: body.event_id, epoch: from.epoch, state: 'sending', from_seq: null, seen: false, why_not: null, learned: null };
        manual.set(id, entry);
        onChange();
        void send(body, entry, 0);
    }
    async function send(body, entry, attempt) {
        let status = 0, answer = null;
        try { ({ status, answer } = await post('manual', body)); } catch (_) { status = 0; }
        if (manual.get(body.fixture_id) !== entry) return;   // a newer change has its own report
        if (status === 200 && answer?.ok === true && answer.event_id === body.event_id) {
            entry.state = answer.recorded === false ? 'not_recorded' : 'recorded';
            entry.from_seq = revision(answer.from_seq) ? answer.from_seq : null;
            if (text(answer.epoch)) entry.epoch = answer.epoch;   // the run den-live's from_seq counts in
            entry.why_not = typeof answer.why_not === 'string' ? answer.why_not.slice(0, 160) : null;
            entry.learned = answer.learned ?? null;
        } else if (status === 404 || status === 501) {
            // A den-live without the route: it can't learn, so the chip never promises it, and nothing is retried.
            entry.state = 'unsupported';
        } else if ((status === 0 || status >= 500) && attempt < MANUAL_RETRIES) {
            later(() => { void send(body, entry, attempt + 1); }, 1000 * (attempt + 1));
            return;
        } else entry.state = 'failed';
        onChange();
    }
    /* den-live releases a fixture at its next reason to act: once the snapshot has shown the hand Home reported, the
     * first one in the same run that shows none ends Home's hold. */
    function endHands(value) {
        for (const [id, entry] of manual) {
            if (entry.from_seq === null || value.epoch !== entry.epoch || value.seq < entry.from_seq) continue;
            const hand = value.fixtures[id]?.hand;
            if (hand && typeof hand === 'object') entry.seen = true;
            else if (entry.seen) {
                manual.delete(id);
                try { overlay?.releaseVirtualFixtureHand?.(id); } catch (_) { /* the next snapshot tries nothing more */ }
            }
        }
    }
    function disconnect() {
        openedAt = null; anchor = null; transform = null; scan = null; highestSeq = -1; renderOffset = null;
        clear();
    }
    function observeControl(value, version = controlVersion) {
        if (version !== controlVersion || !revision(value?.stop_counter) || value.stop_counter < stopCounter
            || typeof value.on !== 'boolean' || typeof value.stopped !== 'boolean') return;
        const changed = control?.on !== value.on || control?.stopped !== value.stopped || control?.stop_counter !== value.stop_counter;
        const turnedOn = control?.on === false && value.on === true;
        stopCounter = value.stop_counter;
        control = { on: value.on, stopped: value.stopped, stop_counter: value.stop_counter };
        // The coordinator turning on takes back every fixture a person held (equipment-virtual-fixtures.mjs).
        if (turnedOn) handBack();
        if (changed) { controlVersion += 1; onChange(); }
    }
    /* The fixtures a person holds, each with what den-live made of the change: learning is pending (not answered yet),
     * recorded, not_recorded (with why_not), unsupported (an older den-live), failed, or none (this page sends none);
     * and warning, den-live's hand.warning while the held spot's beam crosses a head. */
    function handHolds() {
        let holds = [];
        try { holds = overlay?.virtualFixtureHandHolds?.() || []; } catch (_) { holds = []; }
        return holds.map(row => {
            const entry = manual.get(row.fixture_id);
            const learning = !post ? 'none' : !entry || entry.state === 'sending' ? 'pending' : entry.state;
            // den-live's hand.warning in the snapshot drawn now: the held spot's beam crosses a head.
            const hand = applied?.fixtures?.find(fixture => fixture.fixture_id === row.fixture_id)?.hand;
            const warning = hand && typeof hand === 'object' && text(hand.warning) ? hand.warning.slice(0, 60) : null;
            return { ...row, learning, why_not: entry?.why_not ?? null, warning };
        });
    }
    /* Resume, or the coordinator turning on: hand every held fixture back, and tell den-live. */
    function handBack() {
        let ids = [];
        try { ids = overlay?.releaseVirtualFixtureHands?.() || []; } catch (_) { ids = []; }
        for (const id of ids) {
            manual.delete(id); burstFrom.delete(id);
            if (post) void Promise.resolve().then(() => post('manual/resume', { fixture_id: id })).catch(() => {});
        }
        if (ids.length) onChange();
        return ids.length;
    }
    function block(kind) {
        const entry = { after: stopCounter };
        hold[kind] = entry;
        clear();
        onChange();
        return entry;
    }
    function acknowledge(kind, entry, value) {
        if (entry && hold[kind] === entry && kind === 'stop' && value.stopped) entry.confirmed = value.stop_counter;
    }
    function release(kind, entry) {
        if (entry && hold[kind] !== entry) return;
        if (hold[kind]) {
            hold[kind] = null;
            // A write confirms the control only; a new sample supplies the aim.
            clear();
        }
    }
    function check() {
        if (applied && (clock() >= applied.deadline || overlay?.coordinatorSpotScope()?.generation !== applied.generation)) clear();
        applyCandidate();
    }
    function applyCandidate() {
        const value = candidate, scope = overlay?.coordinatorSpotScope?.();
        if (!value || !scope) return;
        const deadline = value.valid_until_mono == null ? Infinity : openedAt + (value.valid_until_mono - anchor.server) * 1000;
        if (clock() >= deadline) { clear(); return; }
        if (transform === null || scan === null) return;
        const binding = value.binding;
        if (binding.transform_id !== transform || binding.scan_revision !== scan || binding.scan_revision !== scope.binding?.content_revision
            || binding.equipment_revision !== scope.equipment_revision || binding.equipment_digest !== scope.equipment_digest
            || Object.keys(value.fixtures).length !== scope.fixture_ids.length
            || !scope.fixture_ids.every(id => Object.hasOwn(value.fixtures, id))) { clear(); return; }

        candidate = null;
        if (!value.on) hold.off = null;
        const stop = hold.stop;
        if (stop && value.stopped && value.stop_counter >= (stop.confirmed ?? stop.after + 1)) stop.observed = value.stop_counter;
        if (stop && !value.stopped && stop.observed != null && value.stop_counter >= stop.observed) hold.stop = null;
        observeControl(value);
        if ((hold.off || hold.stop) && value.on && !value.stopped) { clear(); return; }
        // A moving row's glide goes to the overlay on Home's own clock, so each frame draws the board's S-curve.
        const fixtures = scope.fixture_ids.map(id => {
            const row = { fixture_id: id, ...value.fixtures[id] };
            const glide = row.glide && renderOffset !== null && localGlideCurve(row.glide, row.glide.start_mono * 1000 + renderOffset);
            return glide ? { ...row, glide_local: glide } : row;
        });
        endHands(value);
        if (!overlay.applyCoordinatorSpots(fixtures, scope.generation, { ambient: ambientOf(value.ambient) })) {
            overlay.applyCoordinatorSpots(null); clear(); return;
        }
        stampPaint(value.epoch, value.seq, fixtures.some(fixture => fixture.on === true));
        if (timer !== null) cancel(timer);
        applied = { ...value, fixtures, generation: scope.generation, deadline };
        observe('applied', applied);
        timer = deadline === Infinity ? null : later(check, Math.ceil(deadline - clock()));
        onChange();
    }

    return { attach, block, acknowledge, release, disconnect, observeControl, check, sourceLost: clear, handHolds, handBack,
        connect(start) { disconnect(); openedAt = start; },
        tracking(value, revision) { transform = value ?? null; scan = revision ?? null; if (!scan) clear(); else applyCandidate(); },
        anchor(value) {
            if (openedAt === null || !text(value?.epoch) || !finite(value.server_mono) || value.epoch === anchor?.epoch) return;
            if (anchor) { clear(); highestSeq = -1; }
            anchor = { epoch: value.epoch, server: value.server_mono };
            renderOffset = null;
        },
        receive(value) {
            if (!anchor) return;
            if (value?.epoch !== anchor.epoch) { clear(); return; }
            if (revision(value.seq) && value.seq <= highestSeq) return;
            if (revision(value.seq)) highestSeq = value.seq;
            if (!validSnapshot(value, home, site)) { clear(); return; }
            observe('decoded', value);
            if (value.stop_counter < stopCounter) return;
            const transit = clock() - value.sampled_mono * 1000;
            renderOffset = renderOffset === null ? transit : Math.min(transit, renderOffset + RENDER_CREEP_MS);
            candidate = value;
            observe('scheduled', value);
            applyCandidate();
        },
        controlVersion: () => controlVersion, control: () => control, holds: () => ({ ...hold }), current: () => applied };
}
