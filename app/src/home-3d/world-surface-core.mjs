/**
 * Renderer-independent state for the Apartment viewport's outer-world surface.
 *
 * This module deliberately has no DOM, Cesium, provider, or product-shell
 * dependencies.  Keeping intent authority and render holds here makes their
 * cancellation semantics deterministic enough to test without a GPU.
 */

const CAMERA_BAND_DEFINITIONS = {
    parcel: {
        rangeMeters: 420,
        pitchDegrees: -68,
        durationMs: 850,
        representation: 'confirmed-anchor-only',
    },
    city: {
        rangeMeters: 42_000,
        pitchDegrees: -80,
        durationMs: 1_050,
        representation: 'bundled-natural-earth',
    },
    country: {
        /* Must remain inside the continuous classifier's country band even
         * when entering from planet with inward hysteresis (4,000 km * 0.9).
         * 4,800 km labelled the product `country` while cameraState correctly
         * called the same pose `planet`. */
        rangeMeters: 3_000_000,
        pitchDegrees: -88,
        durationMs: 1_250,
        representation: 'bundled-natural-earth',
    },
    planet: {
        rangeMeters: 18_000_000,
        pitchDegrees: -90,
        durationMs: 1_450,
        representation: 'bundled-natural-earth',
    },
};

export const WORLD_CAMERA_BAND_ORDER = Object.freeze([
    'parcel',
    'city',
    'country',
    'planet',
]);

export const WORLD_CAMERA_BANDS = Object.freeze(Object.fromEntries(
    Object.entries(CAMERA_BAND_DEFINITIONS).map(([name, definition]) => [
        name,
        Object.freeze({ ...definition }),
    ]),
));

export function requireWorldCameraBand(value) {
    if (typeof value !== 'string' || !Object.hasOwn(WORLD_CAMERA_BANDS, value)) {
        throw new TypeError(`world camera band must be one of: ${WORLD_CAMERA_BAND_ORDER.join(', ')}`);
    }
    return value;
}

export function normalizeConfirmedAnchor(value) {
    if (!value || typeof value !== 'object' || value.confirmed !== true) {
        throw new TypeError('world surface requires a confirmed anchor');
    }

    const latitude = Number(value.latitude);
    const longitude = Number(value.longitude);
    const altitudeMeters = value.altitudeMeters == null ? 0 : Number(value.altitudeMeters);

    if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) {
        throw new RangeError('confirmed anchor latitude is outside the supported range');
    }
    if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) {
        throw new RangeError('confirmed anchor longitude is outside the supported range');
    }
    if (!Number.isFinite(altitudeMeters) || altitudeMeters < -500 || altitudeMeters > 10_000) {
        throw new RangeError('confirmed anchor altitude is outside the supported range');
    }

    /*
     * Keep the renderer-facing calibration fields that are part of the closed
     * anchor contract.  Dropping these here is deceptively dangerous: the
     * surface then samples every site with the fallback footprint, shares the
     * revision-zero cache namespace, and ignores the registered parcel range
     * even though the caller supplied all three values.
     *
     * Arbitrary source labels and site metadata still do not cross this
     * boundary.  Only finite, bounded numbers and one allowlisted provenance
     * token survive normalization.
     */
    const normalized = { latitude, longitude, altitudeMeters };
    if (value.siteId != null) {
        const siteId = String(value.siteId).trim();
        if (!/^[a-z0-9][a-z0-9_-]{0,63}$/i.test(siteId)) {
            throw new TypeError('confirmed anchor siteId is invalid');
        }
        normalized.siteId = siteId;
    }
    if (value.registrationVersion != null) {
        const registrationVersion = Number(value.registrationVersion);
        if (!Number.isSafeInteger(registrationVersion) || registrationVersion < 0
            || registrationVersion > 1_000_000_000) {
            throw new RangeError('confirmed anchor registrationVersion is outside the supported range');
        }
        normalized.registrationVersion = registrationVersion;
    }
    if (value.contentRevision != null) {
        const contentRevision = String(value.contentRevision);
        if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$/.test(contentRevision)) {
            throw new TypeError('confirmed anchor contentRevision is invalid');
        }
        normalized.contentRevision = contentRevision;
    }
    if (value.sampleWidthM != null) {
        const sampleWidthM = Number(value.sampleWidthM);
        if (!Number.isFinite(sampleWidthM) || sampleWidthM < 0.1 || sampleWidthM > 100) {
            throw new RangeError('confirmed anchor sampleWidthM is outside the supported range');
        }
        normalized.sampleWidthM = sampleWidthM;
    }
    if (value.parcelRangeM != null) {
        const parcelRangeM = Number(value.parcelRangeM);
        if (!Number.isFinite(parcelRangeM) || parcelRangeM < 24 || parcelRangeM > 10_000) {
            throw new RangeError('confirmed anchor parcelRangeM is outside the supported range');
        }
        normalized.parcelRangeM = parcelRangeM;
    }
    const allowedHeightProvenance = new Set([
        'unresolved',
        'fixed',
        'public-elevation',
        'sampled-surface',
    ]);
    if (allowedHeightProvenance.has(value.heightProvenance)) {
        normalized.heightProvenance = value.heightProvenance;
    }

    return Object.freeze(normalized);
}

/**
 * Monotonic navigation authority.  Intent IDs are opaque caller data; recency
 * is determined by call order so a delayed completion can never regain camera
 * ownership.  begin() returns the exact token it replaced for honest promise
 * settlement by the renderer adapter.
 */
export function createLatestIntentAuthority() {
    let sequence = 0;
    let active = null;

    return Object.freeze({
        begin({ intentId, band }) {
            if (typeof intentId !== 'string' || intentId.trim() === '') {
                throw new TypeError('navigation intentId must be a non-empty string');
            }
            requireWorldCameraBand(band);
            const replaced = active;
            const token = Object.freeze({
                sequence: ++sequence,
                intentId,
                band,
            });
            active = token;
            return Object.freeze({ token, replaced });
        },

        isCurrent(token) {
            return active === token;
        },

        complete(token) {
            if (active !== token) return false;
            active = null;
            return true;
        },

        invalidate() {
            const replaced = active;
            active = null;
            sequence += 1;
            return replaced;
        },

        snapshot() {
            return Object.freeze({
                sequence,
                activeIntentId: active?.intentId ?? null,
                activeBand: active?.band ?? null,
            });
        },
    });
}

/**
 * Monotonic ownership for operations whose public identity is irrelevant.
 *
 * Camera journeys are driven by requestAnimationFrame rather than Cesium's
 * flight API, so camera.cancelFlight() cannot invalidate them.  Keeping their
 * recency in a renderer-free authority makes it impossible for an older loop
 * to commit after a newer camera owner has taken over.
 */
export function createLatestOperationAuthority() {
    let sequence = 0;
    let active = null;

    return Object.freeze({
        begin() {
            const replaced = active;
            const token = Object.freeze({ sequence: ++sequence });
            active = token;
            return Object.freeze({ token, replaced });
        },

        isCurrent(token) {
            return active === token;
        },

        complete(token) {
            if (active !== token) return false;
            active = null;
            return true;
        },

        invalidate() {
            const replaced = active;
            active = null;
            sequence += 1;
            return replaced;
        },

        snapshot() {
            return Object.freeze({
                sequence,
                activeSequence: active?.sequence ?? null,
            });
        },
    });
}

/**
 * A Set, rather than a counter, makes continuous-render reasons idempotent.
 * The same identity cannot accidentally increment twice, and only the exact
 * identity can release its hold.
 */
export function createIdentityRenderHolds(onChange = () => {}) {
    if (typeof onChange !== 'function') {
        throw new TypeError('render hold change observer must be a function');
    }
    const identities = new Set();

    function notify() {
        onChange(identities.size);
    }

    return Object.freeze({
        acquire(identity) {
            if (identity == null) throw new TypeError('render hold identity is required');
            const before = identities.size;
            identities.add(identity);
            if (identities.size !== before) notify();
            let released = false;
            return () => {
                if (released) return false;
                released = true;
                const didRelease = identities.delete(identity);
                if (didRelease) notify();
                return didRelease;
            };
        },

        release(identity) {
            const didRelease = identities.delete(identity);
            if (didRelease) notify();
            return didRelease;
        },

        clear() {
            if (identities.size === 0) return false;
            identities.clear();
            notify();
            return true;
        },

        has(identity) {
            return identities.has(identity);
        },

        size() {
            return identities.size;
        },
    });
}

/**
 * Downsample a complete bottom-up RGBA framebuffer without changing its row
 * orientation. WebGL readPixels cannot scale: asking it for a smaller width and
 * height reads a smaller rectangle from the lower-left corner. Keeping this
 * operation pure makes the handoff's full-frame sampling contract testable
 * without a GPU.
 */
export function downsampleRgbaFrame(
    pixels,
    sourceWidth,
    sourceHeight,
    targetWidth,
    targetHeight,
) {
    const dimensions = [sourceWidth, sourceHeight, targetWidth, targetHeight].map(Number);
    if (dimensions.some((value) => !Number.isInteger(value) || value <= 0)) {
        throw new TypeError('RGBA frame dimensions must be positive integers');
    }
    const [srcWidth, srcHeight, dstWidth, dstHeight] = dimensions;
    if (!pixels || pixels.length !== srcWidth * srcHeight * 4) {
        throw new RangeError('RGBA frame byte length does not match its dimensions');
    }

    const output = new Uint8Array(dstWidth * dstHeight * 4);
    for (let y = 0; y < dstHeight; y += 1) {
        const sourceY = Math.max(0, Math.min(
            srcHeight - 1,
            ((y + 0.5) * srcHeight / dstHeight) - 0.5,
        ));
        const y0 = Math.floor(sourceY);
        const y1 = Math.min(srcHeight - 1, y0 + 1);
        const fy = sourceY - y0;

        for (let x = 0; x < dstWidth; x += 1) {
            const sourceX = Math.max(0, Math.min(
                srcWidth - 1,
                ((x + 0.5) * srcWidth / dstWidth) - 0.5,
            ));
            const x0 = Math.floor(sourceX);
            const x1 = Math.min(srcWidth - 1, x0 + 1);
            const fx = sourceX - x0;
            const destination = (y * dstWidth + x) * 4;
            const topLeft = (y0 * srcWidth + x0) * 4;
            const topRight = (y0 * srcWidth + x1) * 4;
            const bottomLeft = (y1 * srcWidth + x0) * 4;
            const bottomRight = (y1 * srcWidth + x1) * 4;

            for (let channel = 0; channel < 4; channel += 1) {
                const upper = pixels[topLeft + channel]
                    + (pixels[topRight + channel] - pixels[topLeft + channel]) * fx;
                const lower = pixels[bottomLeft + channel]
                    + (pixels[bottomRight + channel] - pixels[bottomLeft + channel]) * fx;
                output[destination + channel] = Math.round(upper + (lower - upper) * fy);
            }
        }
    }
    return output;
}

/*
 * Live-mode rules (B1).
 *
 * The rule these replace was "never show an unfinished frame": the camera was
 * frozen until a frame was complete, the readiness gate restarted on every
 * moved millimetre, and a timeout could only be escaped by an explicit retry.
 * Together that is the stutter and the "still loading" cloud. The rule now is
 * "never show an EMPTY frame": a moving view shows whatever tiles it has, and
 * the strict completeness test applies once the camera is at rest. Each
 * decision is pure so the per-frame policy can be exercised without Cesium.
 */

/* Full cover arms a pending reveal; the reveal is over once the cloud has
 * thinned to a trace or gone, or the still is fully on screen. The previous
 * rule waited for a cloud phase of 'gone', but a gone cloud is reported as no
 * cloud at all (null), so after the first pass the flag could never clear and
 * every later live frame stayed behind the cloud-reveal gate. */
export const CLOUD_REVEAL_ARM_COVERAGE = 0.995;
export const CLOUD_REVEAL_CLEAR_COVERAGE = 0.05;

export function nextCloudRevealPending({
    pending = false, cloudCoverage = null, heroFullyVisible = false,
} = {}) {
    const coverage = Number.isFinite(cloudCoverage) ? cloudCoverage : 0;
    if (coverage >= CLOUD_REVEAL_ARM_COVERAGE) return true;
    if (pending !== true || heroFullyVisible === true) return false;
    return coverage > CLOUD_REVEAL_CLEAR_COVERAGE;
}

/* The AI still's effective opacity in the presented frame, as the handoff
 * compositor's capture gate reads it (handoff-compositor.js worldCaptureReady):
 * null when no still applies at this range, 0 while one applies but is not
 * drawable or not ready yet, and otherwise its drawn opacity with any cloud
 * over it taken off. The particle field takes its colours from that frame, so
 * a still that is only half up would paint it in Google's colours. */
export function heroCaptureOpacity({
    withinBand = false, drawnOpacity = null, ready = false, cloudCoverage = null,
} = {}) {
    if (!Number.isFinite(drawnOpacity) || drawnOpacity <= 0) return withinBand === true ? 0 : null;
    if (ready !== true) return 0;
    const cover = Number.isFinite(cloudCoverage) ? Math.min(1, Math.max(0, cloudCoverage)) : 0;
    return Math.min(1, Math.max(0, drawnOpacity * (1 - cover)));
}

/* May the live photorealistic frame replace the retained one? Moving: any
 * frame that rendered tiles, so refinement streams in instead of the view
 * freezing on its last complete picture. At rest: complete tiles, and a
 * reveal through cloud waits for the readiness gate. A provider fault or an
 * empty render is never shown. */
export function liveTileFramePresentable({
    moving = false, providerFault = null, renderedTiles = null, complete = false,
    revealingThroughCloud = false, gateReady = false,
} = {}) {
    if (providerFault) return false;
    if (moving === true) return Number.isFinite(renderedTiles) && renderedTiles > 0;
    return complete === true && (revealingThroughCloud !== true || gateReady === true);
}

export const READINESS_VIEW_POSITION_TOLERANCE_M = 0.5;
export const READINESS_VIEW_ANGLE_TOLERANCE_RAD = 1e-3;

function angleDelta(a, b) {
    const finiteA = Number.isFinite(a);
    const finiteB = Number.isFinite(b);
    if (!finiteA && !finiteB) return 0;
    if (!finiteA || !finiteB) return Infinity;
    return Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b)));
}

/* Poses are { x, y, z, heading, pitch, fov } in the renderer's own frame.
 * Nothing here reads them as coordinates; only their differences matter. */
export function cameraPoseDiffers(previous, next, {
    positionToleranceM = READINESS_VIEW_POSITION_TOLERANCE_M,
    angleToleranceRad = READINESS_VIEW_ANGLE_TOLERANCE_RAD,
} = {}) {
    if (!previous || !next) return true;
    const distance = Math.hypot(next.x - previous.x, next.y - previous.y, next.z - previous.z);
    if (!(distance <= positionToleranceM)) return true;
    return ['heading', 'pitch', 'fov'].some((field) =>
        !(angleDelta(previous[field], next[field]) <= angleToleranceRad));
}

/*
 * Which view the readiness gate is judging, and whether it must start over.
 *
 * The identity (provider, site, content, scale band, cloud phase, CSS size)
 * restarts it at once. The pose restarts it only once the camera has SETTLED
 * somewhere measurably new: the old key rounded position to a millimetre and
 * angles to 1e-5 rad, so every moving frame reset the gate and it could never
 * see the quiet run it waits for. Backing-store size is deliberately not in
 * the identity: the adaptive DPR cap changes it without changing the view.
 *
 * Settling somewhere new restarts only a gate that is still waiting, giving
 * the view the user stopped at a budget of its own. A ready gate has judged
 * every frame on the way, the last of them at this pose, and any incomplete
 * frame revokes it; restarting it would only thicken a held cloud for a
 * quarter second at the moment the camera comes to rest.
 */
export function settleReadinessViewKey(previous, {
    identity = '', pose = null, settled = true, ready = false,
} = {}, tolerance = {}) {
    const key = Object.freeze({
        identity: String(identity),
        pose: pose ? Object.freeze({ ...pose }) : null,
    });
    if (!previous || previous.identity !== key.identity) return Object.freeze({ reset: true, key });
    if (settled !== true || !cameraPoseDiffers(previous.pose, key.pose, tolerance)) {
        return Object.freeze({ reset: false, key: previous });
    }
    return Object.freeze({ reset: ready !== true, key });
}

/* The covered-recovery deadline arms only for a view that is genuinely stuck:
 * painted, fully covered by cloud, idle and not ready. Never for the overview,
 * a moving camera, a descent cloud pass, a content seam or a visible still. */
export const COVERED_RECOVERY_IDLE_MS = 4_000;

export function coveredRecoveryArmable({
    painted = false, overview = false, moving = false, descentPass = false,
    covered = false, contentSeam = false, ready = false, heroVisible = false,
} = {}) {
    return painted === true && overview !== true && moving !== true && descentPass !== true
        && covered === true && contentSeam !== true && ready !== true && heroVisible !== true;
}

/* The descent cloud pass the journey is inside, as a frozen { id }, or null.
 * Only a short string or finite number id is kept, so the diagnostics and the
 * journey trace that report it can never carry an object the caller passed. */
export function normalizeDescentPass(value) {
    const id = value?.id;
    if (typeof id === 'string') {
        const trimmed = id.trim();
        return trimmed && trimmed.length <= 64 ? Object.freeze({ id: trimmed }) : null;
    }
    return typeof id === 'number' && Number.isFinite(id) ? Object.freeze({ id }) : null;
}

/* idleMs is Infinity for a camera that has never moved, which is idle. */
export function coveredRecoveryDue({ idleMs = 0, ...state } = {}) {
    return coveredRecoveryArmable(state)
        && typeof idleMs === 'number' && idleMs >= COVERED_RECOVERY_IDLE_MS;
}

/* A DPR-cap change resizes the backing store, which visibly resamples the
 * frame and invalidates tile evidence. Defer it while the camera moves and for
 * a settle period after; returns the milliseconds still to wait. */
export const RESOLUTION_CHANGE_SETTLE_MS = 1_000;

export function resolutionChangeDeferMs({
    moving = false, now = 0, lastMotionAt = -Infinity, settleMs = RESOLUTION_CHANGE_SETTLE_MS,
} = {}) {
    if (moving === true) return settleMs;
    const since = now - lastMotionAt;
    if (!Number.isFinite(since)) return 0;
    return Math.max(0, settleMs - since);
}

/* The outward swap-point cloud hold waits for the live map, but only so long:
 * past the cap the camera goes on through held cloud without a recovery. */
export const SWAP_POINT_HOLD_CAP_MS = 1_200;

export function advanceSwapPointHold({
    heldMs = 0, frameMs = 0, wantsHold = false, capMs = SWAP_POINT_HOLD_CAP_MS,
} = {}) {
    const spent = Number.isFinite(heldMs) && heldMs > 0 ? heldMs : 0;
    if (wantsHold !== true || spent >= capMs) return Object.freeze({ hold: false, heldMs: spent });
    const step = Number.isFinite(frameMs) && frameMs > 0 ? frameMs : 0;
    return Object.freeze({ hold: true, heldMs: spent + step });
}

/*
 * Per-rendered-frame journey trace, opt-in with ?journeyTrace=1.
 *
 * Same redaction as every other diagnostic here: ranges, angles, counts,
 * booleans and the site id the host already shows. No position, height,
 * coordinate, tile URL or key can be recorded, because an entry is built from
 * an allowlist rather than copied from what the caller passed. `destM` is the
 * straight-line distance to a flight's destination home while one is flying,
 * because `rangeM` still measures from the home being left.
 */
export const JOURNEY_TRACE_CAPACITY = 2048;
const JOURNEY_TRACE_GATES = new Set(['warming', 'ready', 'timed-out']);

export function journeyTraceRequested(search) {
    try {
        return new URLSearchParams(typeof search === 'string' ? search : '').get('journeyTrace') === '1';
    } catch {
        return false;
    }
}

export function journeyTraceEntry({
    frameId, at, rangeM, headingDeg, held, gate, gateGeneration, cloudCoverage,
    pass, moving, pending, processing, siteId, destinationM,
} = {}) {
    const round = (value, digits) => (Number.isFinite(value) ? +value.toFixed(digits) : null);
    const whole = (value) => (Number.isFinite(value) && value >= 0 ? Math.floor(value) : null);
    const coverage = Number.isFinite(cloudCoverage) ? Math.min(1, Math.max(0, cloudCoverage)) : 0;
    return Object.freeze({
        f: whole(frameId),
        t: round(at, 2),
        rangeM: round(rangeM, 2),
        headingDeg: round(headingDeg, 3),
        held: held === true,
        gate: gate === 'not-required' ? 'ready' : (JOURNEY_TRACE_GATES.has(gate) ? gate : null),
        gateGen: whole(gateGeneration),
        cloud: round(coverage, 4),
        pass: (typeof pass === 'string' && pass) || Number.isFinite(pass) ? pass : null,
        moving: moving === true,
        pending: whole(pending),
        processing: whole(processing),
        siteId: typeof siteId === 'string' && siteId ? siteId : null,
        destM: Number.isFinite(destinationM) && destinationM >= 0 ? Math.round(destinationM) : null,
    });
}

export function createJourneyTraceBuffer(capacity = JOURNEY_TRACE_CAPACITY) {
    const size = Math.max(1, Math.floor(Number.isFinite(capacity) ? capacity : JOURNEY_TRACE_CAPACITY));
    const entries = new Array(size);
    let start = 0;
    let count = 0;
    return Object.freeze({
        capacity: size,
        push(entry) {
            entries[(start + count) % size] = entry;
            if (count < size) count += 1;
            else start = (start + 1) % size;
            return count;
        },
        /* Oldest first, and empties the buffer. */
        drain() {
            const out = [];
            for (let i = 0; i < count; i += 1) {
                const index = (start + i) % size;
                out.push(entries[index]);
                entries[index] = undefined;
            }
            start = 0;
            count = 0;
            return out;
        },
        size() {
            return count;
        },
    });
}
