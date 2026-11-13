const RADIANS_PER_CENTIDEGREE = Math.PI / 18000;

const finite = Number.isFinite;
const integerIn = (value, min, max) => Number.isInteger(value) && value >= min && value <= max;
const vector = (value, size) => Array.isArray(value) && value.length === size
    && Array.from(value).every(finite);

/**
 * Projects a simulated FC13 receipt into the registered Z-up room frame.
 * The caller accepts snapshots in order within the active subscription. After
 * receipt, pass as_of plus elapsed monotonic seconds as nowUnix. A dark result
 * without a ray leaves the head at its last displayed position.
 */
export function projectC12Preview(snapshot, fixtureId, nowUnix = snapshot?.as_of) {
    const dark = { on: false, level: 0, cctKelvin: null, origin: null, direction: null,
        runId: null, eventIndex: null, moveSeq: null, moveDecisionId: null };
    const { run_id: runId, event_index: eventIndex, as_of: asOf, fixtures } = snapshot || {};
    if (typeof runId !== 'string' || !runId || !Number.isSafeInteger(eventIndex) || eventIndex < 0
        || !finite(asOf) || !finite(nowUnix) || nowUnix < asOf || !fixtures || !Object.hasOwn(fixtures, fixtureId)) return dark;
    dark.runId = runId;
    dark.eventIndex = eventIndex;

    const row = fixtures[fixtureId];
    if (!row) return dark;
    const { aim_origin_m: origin, horizontal_world_reference: reference, joints_cdeg: joints,
        spot_level: level, spot_mired: mired, expires_at: expiresAt, move_seq: moveSeq,
        move_decision_id: moveDecisionId } = row;
    if (!vector(origin, 3) || !vector(reference, 3) || !vector(joints, 2)
        || !joints.every(angle => integerIn(angle, -32768, 32767)) || !integerIn(level, 0, 254)
        || !integerIn(moveSeq, 0, 65535) || typeof moveDecisionId !== 'string' || !moveDecisionId) return dark;
    if (level > 0 && (!finite(expiresAt) || nowUnix >= expiresAt || !integerIn(mired, 154, 556))) return dark;

    const forwardLength = Math.hypot(reference[0], reference[1]);
    if (!finite(forwardLength) || forwardLength < 1e-9) return dark;
    const forwardX = reference[0] / forwardLength;
    const forwardY = reference[1] / forwardLength;
    const [pan, tilt] = joints.map(angle => angle * RADIANS_PER_CENTIDEGREE);
    const cosPan = Math.cos(pan), sinPan = Math.sin(pan), cosTilt = Math.cos(tilt);
    return {
        on: level > 0,
        level: level / 254,
        cctKelvin: integerIn(mired, 154, 556) ? Math.round(1_000_000 / mired) : null,
        origin: [...origin],
        direction: [
            cosTilt * (cosPan * forwardX - sinPan * forwardY),
            cosTilt * (cosPan * forwardY + sinPan * forwardX),
            Math.sin(tilt),
        ],
        runId, eventIndex, moveSeq, moveDecisionId,
    };
}
