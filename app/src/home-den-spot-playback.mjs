const SCHEMA = 'den-virtual-spots-1';
const finite = Number.isFinite;
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

export function createSpotPlayback({ home, site, clock = () => performance.now(), onChange = () => {},
    later = setTimeout, cancel = clearTimeout }) {
    let overlay = null, applied = null, candidate = null, control = null, controlVersion = 0;
    let anchor = null, openedAt = null, transform = null, scan = null, highestSeq = -1, stopCounter = -1, timer = null;
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
        openedAt = null; anchor = null; transform = null; scan = null; highestSeq = -1;
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
        const fixtures = scope.fixture_ids.map(id => ({ fixture_id: id, ...value.fixtures[id] }));
        if (!overlay.applyCoordinatorSpots(fixtures, scope.generation)) {
            overlay.applyCoordinatorSpots(null); clear(); return;
        }
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
        },
        receive(value) {
            if (!anchor) return;
            if (value?.epoch !== anchor.epoch) { clear(); return; }
            if (revision(value.seq) && value.seq <= highestSeq) return;
            if (revision(value.seq)) highestSeq = value.seq;
            if (!validSnapshot(value, home, site)) { clear(); return; }
            if (value.stop_counter < stopCounter) return;
            candidate = value;
            applyCandidate();
        },
        controlVersion: () => controlVersion, control: () => control, holds: () => ({ ...hold }), current: () => applied };
}
