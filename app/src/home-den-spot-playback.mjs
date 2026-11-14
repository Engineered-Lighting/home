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
 * The coordinator sends one MOVE per glide, and the board's planner (C12 v2 plan 5.1) flies both axes along one
 * jerk-limited S-curve on a shared path parameter s, from rest to rest. Each den-virtual-spots-1 row that is moving
 * carries that move as `glide {from_deg, to_deg, start_mono, duration_s, limits: [v, a, j], shape: "scurve-7"}`, and
 * Home evaluates the same curve every frame instead of stepping between 10 Hz snapshots. A row's level comes as the
 * level train's committed steps; Home eases between them over SPOT_EASE_MS, about one coordinator tick.
 *
 * Shared vectors: tools/den-live/fixtures/direction-scurve-vectors.json (den-live's test_direction.py writes them). */
export const SPOT_EASE_MS = 120;
const SHAPE = 'scurve-7';

function timeToSpeed(v, a, j) {
    return v >= a * a / j ? v / a + a / j : 2 * Math.sqrt(v / j);
}

/* A rest-to-rest move of s from 0 to 1 under speed v, acceleration a and jerk j (per second, squared, cubed):
 * den-core's scurve_time for its duration, and the jerk profile integrated for s(t). */
export function scurve(v, a, j) {
    if (!(v > 0 && a > 0 && j > 0)) throw RangeError('scurve_limits');
    let peak = v;
    if (v * timeToSpeed(v, a, j) > 1) {
        let low = 0, high = v;
        for (let i = 0; i < 100; i++) {
            const middle = (low + high) / 2;
            if (middle * timeToSpeed(middle, a, j) < 1) low = middle; else high = middle;
        }
        peak = high;
    }
    const [jerkS, flatS] = peak >= a * a / j ? [a / j, peak / a - a / j] : [Math.sqrt(peak / j), 0];
    const accelerate = 2 * jerkS + flatS, cruise = Math.max(0, (1 - peak * accelerate) / peak);
    const segments = [[jerkS, j], [flatS, 0], [jerkS, -j], [cruise, 0], [jerkS, -j], [flatS, 0], [jerkS, j]];
    const duration = 2 * accelerate + cruise;
    function integrate(t) {
        let position = 0, speed = 0, acceleration = 0;
        for (const [length, jerk] of segments) {
            if (t <= 0) break;
            const step = Math.min(length, t);
            if (step <= 0) continue;
            position += speed * step + acceleration * step * step / 2 + jerk * step ** 3 / 6;
            speed += acceleration * step + jerk * step * step / 2;
            acceleration += jerk * step;
            t -= step;
        }
        return [position, speed];
    }
    const total = integrate(duration)[0] || 1;
    return {
        duration,
        s: t => t <= 0 ? 0 : t >= duration ? 1 : Math.min(1, Math.max(0, integrate(t)[0] / total)),
        speed: t => t <= 0 || t >= duration ? 0 : integrate(t)[1] / total,
    };
}

/* A snapshot row's glide, checked, on Home's clock: startMs is the glide's start as Home's performance clock reads
 * it, and at(nowMs) the [pan_deg, tilt_deg] it has reached. Null when the row has no glide Home can draw. */
function localGlideCurve(glide, startMs) {
    if (!glide || glide.shape !== SHAPE || !Array.isArray(glide.limits) || glide.limits.length !== 3
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

export function createSpotPlayback({ home, site, clock = () => performance.now(), onChange = () => {},
    later = setTimeout, cancel = clearTimeout }) {
    let overlay = null, applied = null, candidate = null, control = null, controlVersion = 0;
    let anchor = null, openedAt = null, transform = null, scan = null, highestSeq = -1, stopCounter = -1, timer = null;
    let renderOffset = null;   // Home's clock minus the coordinator's, in ms: the lowest transit seen, creeping up
    const hold = { off: null, stop: null };

    function clear() {
        if (timer !== null) cancel(timer);
        timer = null;
        candidate = null;
        if (!applied) return;
        applied = null;
        overlay?.applyCoordinatorSpots?.(null);
        onChange();
    }
    function attach(nextOverlay) {
        if (overlay === nextOverlay) return;
        disconnect();
        overlay = nextOverlay;
    }
    function disconnect() {
        openedAt = null; anchor = null; transform = null; scan = null; highestSeq = -1; renderOffset = null;
        clear();
    }
    function observeControl(value, version = controlVersion) {
        if (version !== controlVersion || !revision(value?.stop_counter) || value.stop_counter < stopCounter
            || typeof value.on !== 'boolean' || typeof value.stopped !== 'boolean') return;
        const changed = control?.on !== value.on || control?.stopped !== value.stopped || control?.stop_counter !== value.stop_counter;
        stopCounter = value.stop_counter;
        control = { on: value.on, stopped: value.stopped, stop_counter: value.stop_counter };
        if (changed) { controlVersion += 1; onChange(); }
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
        if (!overlay.applyCoordinatorSpots(fixtures, scope.generation, { ambient: ambientOf(value.ambient) })) {
            overlay.applyCoordinatorSpots(null); clear(); return;
        }
        stampPaint(value.epoch, value.seq, fixtures.some(fixture => fixture.on === true));
        if (timer !== null) cancel(timer);
        applied = { ...value, fixtures, generation: scope.generation, deadline };
        timer = deadline === Infinity ? null : later(check, Math.ceil(deadline - clock()));
        onChange();
    }

    return { attach, block, acknowledge, release, disconnect, observeControl, check, sourceLost: clear,
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
            if (value.stop_counter < stopCounter) return;
            const transit = clock() - value.sampled_mono * 1000;
            renderOffset = renderOffset === null ? transit : Math.min(transit, renderOffset + RENDER_CREEP_MS);
            candidate = value;
            applyCandidate();
        },
        controlVersion: () => controlVersion, control: () => control, holds: () => ({ ...hold }), current: () => applied };
}
