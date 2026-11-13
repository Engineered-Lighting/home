/* One lease for the coordinator's complete, scene-bound spot snapshot. The
 * control readback supplies producer time and generation, never fixture state. */
const MAX_LEASE_MS = 3000;
const CLOCK_MAX_AGE_MS = 10000;
const CLOCK_MAX_RTT_MS = 1000;
const BINDING_KEYS = ['content_revision', 'registration_version', 'manifest_digest', 'frame_digest', 'canonical_collision_digest'];
const revision = value => Number.isSafeInteger(value) && value >= 0;
const identifier = value => typeof value === 'string' && value.length > 0 && value.length <= 160;
const optionalText = (value, max) => value == null || typeof value === 'string' && value.length <= max;
const inRange = (value, min, max) => Number.isFinite(value) && value >= min && value <= max;

export function createSpotPlayback({ home, site, clock = () => performance.now(), onChange = () => {} }) {
    let overlay = null, applied = null, candidate = null, authority = null, clockAnchor = null, floor = null, reconnectAt = null;
    const hold = { off: null, stop: null };
    let controlFloor = -1, timer = null;

    function clear(force = false) {
        clearTimeout(timer); timer = null;
        if (!applied && !force) return;
        applied = null;
        overlay?.applyCoordinatorSpots?.(null);
        onChange(null);
    }
    function attach(nextOverlay) {
        if (overlay === nextOverlay) return;
        clear(); overlay = nextOverlay; candidate = null; authority = null; clockAnchor = null;
        overlay?.applyCoordinatorSpots?.(null);
    }
    function block(kind) {
        const entry = { after: Math.max(controlFloor, authority?.control_revision ?? -1, floor?.control_revision ?? -1) };
        hold[kind] = entry;
        clear(true);
        return entry;
    }
    function release(kind, entry, controlRevision) {
        if (!revision(controlRevision)) return;
        if (entry && hold[kind] === entry) entry.releasedAt = controlRevision;
        controlFloor = Math.max(controlFloor, controlRevision);
        clear();
    }
    function acknowledge(kind, entry, controlRevision) {
        if (!revision(controlRevision)) return;
        if (entry && hold[kind] === entry) entry.blockedAt = controlRevision;
        controlFloor = Math.max(controlFloor, controlRevision);
    }
    function reconcileHolds(value) {
        for (const [kind, isBlocked] of [['off', !value.enabled], ['stop', value.stopped]]) {
            const entry = hold[kind];
            if (entry && (isBlocked && value.control_revision > entry.after || value.control_revision > (entry.blockedAt ?? Infinity)
                || value.control_revision >= (entry.releasedAt ?? Infinity))) hold[kind] = null;
        }
    }
    function sourceLost() {
        candidate = null;
        clear();
    }
    function disconnect() {
        reconnectAt = clock(); authority = null; clockAnchor = null;
        sourceLost();
    }
    function observeControl(value, sentAt, receivedAt = clock()) {
        if (!identifier(value?.epoch) || !revision(value.control_revision) || !revision(value.server_time_ms)
            || typeof value.enabled !== 'boolean' || typeof value.stopped !== 'boolean'
            || !inRange(receivedAt - sentAt, 0, CLOCK_MAX_RTT_MS) || reconnectAt != null && sentAt < reconnectAt) return;
        if (value.control_revision < controlFloor || floor && value.control_revision < floor.control_revision) return;
        if (authority?.epoch === value.epoch && value.control_revision === authority.control_revision
            && (value.enabled !== authority.enabled || value.stopped !== authority.stopped)) { sourceLost(); return; }
        if (authority?.epoch !== value.epoch) {
            clear();
            if (candidate?.epoch !== value.epoch) candidate = null;
        }
        const received = receivedAt;
        const upperNow = value.server_time_ms + (receivedAt - sentAt);
        clockAnchor = { upperNow, received };
        const sinceReconnect = reconnectAt != null ? value.server_time_ms - (receivedAt - reconnectAt) : null;
        reconnectAt = null;
        if (sinceReconnect != null && candidate && candidate.sampled_at_ms < sinceReconnect) candidate = null;
        authority = { epoch: value.epoch, control_revision: value.control_revision, enabled: value.enabled, stopped: value.stopped };
        reconcileHolds(value);
        if (!value.enabled || value.stopped || applied && value.control_revision > applied.control_revision) clear();
        applyCandidate();
    }
    function check() {
        const scope = overlay?.coordinatorSpotScope?.();
        if (applied && (!scope || applied.generation !== scope.generation || clock() >= applied.deadline)) clear();
    }
    function receive(value) {
        if (!value || value.schema !== 'den-virtual-spots-1' || value.home !== home || value.site !== site
            || !identifier(value.epoch) || !revision(value.seq) || !revision(value.control_revision)
            || !revision(value.sampled_at_ms) || !revision(value.expires_at_ms)
            || !inRange(value.expires_at_ms - value.sampled_at_ms, 1, MAX_LEASE_MS)
            || typeof value.enabled !== 'boolean' || typeof value.stopped !== 'boolean'
            || !Array.isArray(value.fixtures)) { sourceLost(); return; }
        if (floor?.epoch === value.epoch && value.seq <= floor.seq) return;
        if (value.control_revision < controlFloor || floor && value.control_revision < floor.control_revision) return;
        if (candidate?.epoch === value.epoch && value.seq <= candidate.seq) return;
        candidate = value;
        applyCandidate();
    }
    function applyCandidate() {
        const value = candidate;
        if (!value || !authority || !clockAnchor) return;
        if (value.epoch !== authority.epoch) { clear(); return; }
        const now = clock(), elapsed = now - clockAnchor.received, upperNow = clockAnchor.upperNow + elapsed;
        if (elapsed >= CLOCK_MAX_AGE_MS || value.expires_at_ms <= upperNow || value.sampled_at_ms > upperNow + 1) {
            sourceLost(); return;
        }
        if (value.control_revision < authority.control_revision) { sourceLost(); return; }
        if (value.control_revision === authority.control_revision && (value.enabled !== authority.enabled || value.stopped !== authority.stopped)) {
            sourceLost(); return;
        }
        const scope = overlay?.coordinatorSpotScope?.();
        if (!scope?.binding || value.equipment_revision !== scope.equipment_revision || value.equipment_digest !== scope.equipment_digest
            || !value.binding || !BINDING_KEYS.every(key => value.binding[key] === scope.binding[key])
            || value.fixtures.length !== scope.fixture_ids.length) { sourceLost(); return; }
        const expected = new Set(scope.fixture_ids);
        if (!value.fixtures.every(row => row && expected.delete(row.fixture_id) && typeof row.on === 'boolean'
            && inRange(row.pan_deg, -180, 180) && inRange(row.tilt_deg, -90, 90) && inRange(row.level, 0, 1)
            && Number.isFinite(row.cct_k) && optionalText(row.why, 320) && optionalText(row.trace_id, 160))) {
            sourceLost(); return;
        }
        candidate = null;
        floor = { epoch: value.epoch, seq: value.seq, control_revision: value.control_revision };
        authority = { epoch: value.epoch, control_revision: value.control_revision, enabled: value.enabled, stopped: value.stopped };
        reconcileHolds(value);
        if (hold.off || hold.stop || (!value.enabled || value.stopped) && value.fixtures.some(row => row.on)) {
            clear(); return;
        }
        if (!overlay.applyCoordinatorSpots(value.fixtures, scope.generation)) {
            overlay.applyCoordinatorSpots(null); clear(); return;
        }
        clearTimeout(timer);
        const deadline = now + Math.min(value.expires_at_ms - upperNow, CLOCK_MAX_AGE_MS - elapsed);
        applied = { ...value, generation: scope.generation, deadline };
        timer = setTimeout(check, Math.ceil(deadline - now));
        onChange(applied);
    }
    return { attach, block, release, acknowledge, sourceLost, disconnect, observeControl, receive, check,
        control: () => authority, holds: () => ({ off: hold.off, stop: hold.stop }), current: () => applied };
}
