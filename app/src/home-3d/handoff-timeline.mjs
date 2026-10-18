/*
 * handoff-timeline.mjs — the pure Apartment-to-world scale handoff.
 *
 * Every number here is carried over from the validated apartment-scale-handoff
 * prototype, which was visually approved. The module is deliberately pure: no
 * DOM, no renderer, no clock. The compositor and the coordinator read from it;
 * nothing writes back. That is what makes the keyframe assertions meaningful.
 *
 * One number is NOT carried over. The prototype collapsed the Apartment to a
 * fixed scale of 0.0035, tuned against a 1280x785 stage so the final image
 * landed exactly on its 4px singularity dot. Home renders at three materially
 * different sizes (a resizable desktop window, an embedded stage narrowed by the
 * chat rail, and a 390px portrait phone), where that constant would produce a
 * 6.7px blob or a 1.4px nothing. apartmentScale() takes the host width and
 * targets the dot instead.
 *
 * Two shapes are authored, and both are kept so they can be compared live.
 *
 * The default `particles` timeline, in milliseconds along 1,000ms:
 *
 *   0    the Apartment begins shrinking toward the viewport centre
 *   150  it begins fading
 *   260  the black scale firewall begins closing over it
 *   300  the Apartment is fully invisible; the firewall is fully closed, and
 *        AI-still-coloured particles begin streaming in from the perimeter
 *   650  the live world begins resolving underneath them
 *   880  the particles have landed
 *   1000 the world owns the viewport
 *
 * The `legacy` timeline is the prototype's own 650ms shape (the firewall closing
 * 250-300, particles 330-620, the world resolving from 500) at a 420ms tempo.
 *
 * The firewall exists so that Apartment geometry and world geometry are never
 * both visible. noCoexistenceViolations() proves that from the curves alone; a
 * DOM-level assertion has to prove it again against computed style, because CSS
 * transitions can and did desynchronise these values from what actually paints.
 */

export const REDUCED_MOTION_CUT_MS = 120;

/** The largest single wheel event the Apartment boundary reads, in CSS pixels.
 * Pathological deltas are capped here; commit is counted in notches, not in
 * accumulated pixels (see apartment-boundary-momentum.mjs). */
export const SNAP_THRESHOLD = 220;
export const PREVIEW_SMOOTH_TIME_S = 0.075;

/** Progress units per second and derivative limits at the reference tempo
 * below. Every tray tempo scales these by inverse time so every preset has the
 * same feel. */
export const HANDOFF_MAX_SPEED = 5.2;
export const HANDOFF_ACCELERATION = 40;
/** A direction change gets a little more braking authority, while preserving
 * the velocity and acceleration already on screen. */
export const HANDOFF_REVERSAL_ACCELERATION = 80;
export const HANDOFF_MAX_JERK = 2400;
/** The tempo those limits were tuned at on the real scene. */
export const HANDOFF_MOTION_REFERENCE_MS = 420;

/** Diameter of the singularity the Apartment collapses onto, in CSS pixels. */
export const SINGULARITY_PX = 4;

function freezeTimeline(timeline) {
    return Object.freeze({ ...timeline, keyframes: Object.freeze({ ...timeline.keyframes }) });
}

/* The prototype's proportions at the tempo that was approved on the real scene.
 * At 650ms the particles became the subject: you watched a field of dots travel
 * rather than watching one place become another. Every keyframe is the
 * prototype's own value scaled by one factor, so the approved shape survives. */
const LEGACY_AUTHORED_MS = 650;
const LEGACY_DURATION_MS = 420;
const LEGACY_TEMPO = LEGACY_DURATION_MS / LEGACY_AUTHORED_MS;
const LEGACY = freezeTimeline({
    id: 'legacy',
    durationMs: LEGACY_DURATION_MS,
    /* Progress ceiling of the reversible pre-zoom band. */
    previewProgress: 0.115,
    keyframes: {
        apartmentFadeStartMs: 150 * LEGACY_TEMPO,
        apartmentEndMs: 300 * LEGACY_TEMPO,
        firewallStartMs: 250 * LEGACY_TEMPO,
        singularityStartMs: 225 * LEGACY_TEMPO,
        singularityFadeInMs: 55 * LEGACY_TEMPO,
        singularityFadeStartMs: 285 * LEGACY_TEMPO,
        singularityFadeOutMs: 45 * LEGACY_TEMPO,
        particleStartMs: 330 * LEGACY_TEMPO,
        particleEndMs: 620 * LEGACY_TEMPO,
        mapResolveStartMs: 500 * LEGACY_TEMPO,
    },
});

/*
 * The default: the particles are the transition, not a blink inside it.
 *
 * Measured on the 420ms shape, the black firewall was opaque for most of the
 * handoff and the particles were on screen for under 190ms, so the whole thing
 * read as a cut to black. Here the firewall closes only over the last 40ms of
 * the collapse, the field starts arriving the moment the Apartment is gone, and
 * the world resolves under a field that is still landing. The singularity is
 * held across the moment the firewall closes (it is painted inside the
 * firewall, so it cannot show before it), and leaves while the first streaks
 * are still at the frame edge: there is no frame of pure black to speak of.
 *
 * The collapse keeps its absolute 300ms, so the preview band is re-derived to
 * end at exactly the Apartment scale the legacy band ended at: the elastic
 * pull at the boundary looks the same whichever shape follows it.
 */
const PARTICLES_DURATION_MS = 1_000;
const PARTICLES_KEYFRAMES = Object.freeze({
    apartmentFadeStartMs: 150,
    apartmentEndMs: 300,
    firewallStartMs: 260,
    singularityStartMs: 245,
    singularityFadeInMs: 40,
    singularityFadeStartMs: 305,
    singularityFadeOutMs: 40,
    particleStartMs: 300,
    particleEndMs: 880,
    mapResolveStartMs: 650,
});
const PARTICLES = freezeTimeline({
    id: 'particles',
    durationMs: PARTICLES_DURATION_MS,
    previewProgress: LEGACY.previewProgress
        * (PARTICLES_KEYFRAMES.apartmentEndMs / PARTICLES_DURATION_MS)
        / (LEGACY.keyframes.apartmentEndMs / LEGACY.durationMs),
    keyframes: PARTICLES_KEYFRAMES,
});

export const HANDOFF_TIMELINES = Object.freeze({ particles: PARTICLES, legacy: LEGACY });
export const DEFAULT_HANDOFF_TIMELINE = PARTICLES;

export const HANDOFF_DURATION_MS = DEFAULT_HANDOFF_TIMELINE.durationMs;
export const HANDOFF_KEYFRAMES = DEFAULT_HANDOFF_TIMELINE.keyframes;
/** Progress ceiling of the reversible pre-zoom band on the default timeline.
 * The Apartment boundary always speaks in these units. */
export const PREVIEW_PROGRESS = DEFAULT_HANDOFF_TIMELINE.previewProgress;

/**
 * The timeline a handoff should run.
 *
 * An explicit timeline object or id wins. Otherwise the shape authored nearest
 * to the requested tempo is used, which is what lets the compositor pick the
 * right curves from the one number the host already passes (the preset's
 * durationMs): 300/420/650 keep the legacy shape, 1000 gets the particles one.
 */
export function handoffTimeline(timeline = null, durationMs = null) {
    if (timeline && typeof timeline === 'object' && timeline.keyframes) return timeline;
    if (typeof timeline === 'string'
        && Object.prototype.hasOwnProperty.call(HANDOFF_TIMELINES, timeline)) {
        return HANDOFF_TIMELINES[timeline];
    }
    const requested = Number(durationMs);
    if (!Number.isFinite(requested) || requested <= 0) return DEFAULT_HANDOFF_TIMELINE;
    let nearest = DEFAULT_HANDOFF_TIMELINE;
    for (const candidate of Object.values(HANDOFF_TIMELINES)) {
        if (Math.abs(candidate.durationMs - requested)
            < Math.abs(nearest.durationMs - requested)) nearest = candidate;
    }
    return nearest;
}

/**
 * Re-express a boundary seed (default-timeline progress, velocity, and
 * acceleration) on another timeline.
 *
 * Inside the preview band only the collapse scale moves, and that scale is a
 * function of timelineMs / apartmentEndMs alone. Equal scale therefore means
 * progress in proportion to each timeline's collapse fraction, and the
 * derivatives carry the same constant. Outside the band the seed is not a
 * boundary seed and is returned unchanged.
 */
export function rebaseBoundarySeed(
    { progress = 0, velocity = 0, acceleration = 0 } = {},
    toTimeline = DEFAULT_HANDOFF_TIMELINE,
    fromTimeline = DEFAULT_HANDOFF_TIMELINE,
) {
    const to = handoffTimeline(toTimeline);
    const from = handoffTimeline(fromTimeline);
    const p = clamp(progress);
    const seed = { progress: p, velocity: Number(velocity) || 0, acceleration: Number(acceleration) || 0 };
    if (to === from || p > from.previewProgress + 1e-12) return Object.freeze(seed);
    const ratio = (to.keyframes.apartmentEndMs / to.durationMs)
        / (from.keyframes.apartmentEndMs / from.durationMs);
    return Object.freeze({
        progress: p * ratio,
        velocity: seed.velocity * ratio,
        acceleration: seed.acceleration * ratio,
    });
}

/**
 * NaN-safe by design. These values become CSS opacities and transforms, and a
 * NaN reaching the compositor produces an invalid style rather than a visible
 * error, so a non-finite input degrades to the low end of the range instead of
 * propagating.
 */
export function clamp(value, min = 0, max = 1) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return min;
    return Math.min(max, Math.max(min, numeric));
}

export function lerp(from, to, amount) {
    return from + (to - from) * amount;
}

/** 6t^5 - 15t^4 + 10t^3: zero first and second derivatives at both ends. */
export function smootherstep(value) {
    const t = clamp(value);
    return t * t * t * (t * (t * 6 - 15) + 10);
}

/**
 * Quadratic ease over the first and last 20%, linear between. The collapse needs
 * a constant-rate middle so the Apartment reads as receding at a steady speed
 * rather than easing out early.
 */
export function softLinear(value) {
    const t = clamp(value);
    const ramp = 0.2;
    const slope = 1 / (1 - ramp);
    if (t < ramp) return slope * t * t / (2 * ramp);
    if (t > 1 - ramp) {
        const remaining = 1 - t;
        return 1 - slope * remaining * remaining / (2 * ramp);
    }
    return slope * (t - ramp * 0.5);
}

/**
 * Unity-style critically damped spring with an overshoot clamp. Used for the
 * reversible pre-zoom band and for world log-range zoom, where a wheel burst
 * must retarget live motion rather than restart it.
 */
export function smoothDamp(current, target, velocity, smoothTimeS, deltaTimeS) {
    const safeSmoothTime = Math.max(0.0001, smoothTimeS);
    const omega = 2 / safeSmoothTime;
    const x = omega * deltaTimeS;
    const decay = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
    const change = current - target;
    const temporary = (velocity + omega * change) * deltaTimeS;
    let nextVelocity = (velocity - omega * temporary) * decay;
    let next = target + (change + temporary) * decay;
    if ((target - current > 0) === (next > target)) {
        next = target;
        nextVelocity = 0;
    }
    return { value: next, velocity: nextVelocity };
}

/**
 * Scale that lands the Apartment exactly on the singularity dot at this host
 * width, clamped so a pathological viewport cannot produce a scale of zero or
 * one. At the prototype's 1280px stage this reproduces its 0.0035.
 */
export function finalApartmentScale(hostWidthPx, singularityPx = SINGULARITY_PX) {
    const width = Number(hostWidthPx);
    if (!Number.isFinite(width) || width <= 0) return 0.0035;
    return clamp(singularityPx / width, 0.0002, 0.5);
}

/**
 * The whole visual state of the handoff at progress p in [0, 1].
 *
 * `hostWidthPx` only affects the collapse scale. Everything else is
 * resolution-independent. `timeline` selects the authored shape; it defaults
 * to the shipped one.
 */
export function handoffFrame(progress, {
    hostWidthPx = 1280,
    singularityPx = SINGULARITY_PX,
    timeline = DEFAULT_HANDOFF_TIMELINE,
} = {}) {
    const { durationMs, keyframes: KEYFRAMES } = handoffTimeline(timeline);
    const p = clamp(progress);
    const t = p * durationMs;

    const collapse = softLinear(t / KEYFRAMES.apartmentEndMs);
    const apartmentScale = lerp(1, finalApartmentScale(hostWidthPx, singularityPx), collapse);
    const apartmentOpacity = 1 - softLinear(
        (t - KEYFRAMES.apartmentFadeStartMs) / (KEYFRAMES.apartmentEndMs - KEYFRAMES.apartmentFadeStartMs),
    );

    // The dot blooms as the Apartment vanishes into it and is gone before the
    // particles reach it, so the eye has one continuous object to follow.
    const singularityOpacity = smootherstep(
        (t - KEYFRAMES.singularityStartMs) / KEYFRAMES.singularityFadeInMs,
    ) * (1 - smootherstep(
        (t - KEYFRAMES.singularityFadeStartMs) / KEYFRAMES.singularityFadeOutMs,
    ));

    // The firewall closes over the tail of the collapse and opens again only as
    // the world resolves, so it is opaque from the Apartment's last pixel to
    // the first world pixel.
    const firewallIn = softLinear(
        (t - KEYFRAMES.firewallStartMs) / (KEYFRAMES.apartmentEndMs - KEYFRAMES.firewallStartMs),
    );
    const worldOpacity = softLinear(
        (t - KEYFRAMES.mapResolveStartMs) / (durationMs - KEYFRAMES.mapResolveStartMs),
    );
    const compositorOpacity = firewallIn * (1 - worldOpacity);

    const particleProgress = clamp(
        (t - KEYFRAMES.particleStartMs) / (KEYFRAMES.particleEndMs - KEYFRAMES.particleStartMs),
    );

    return {
        progress: p,
        timelineMs: t,
        apartmentScale,
        apartmentOpacity,
        singularityOpacity,
        compositorOpacity,
        particleProgress,
        worldOpacity,
        phase: handoffPhase(p, timeline),
    };
}

export function handoffPhase(progress, timeline = DEFAULT_HANDOFF_TIMELINE) {
    const { durationMs, keyframes: KEYFRAMES } = handoffTimeline(timeline);
    const t = clamp(progress) * durationMs;
    if (t <= 0) return 'apartment';
    if (t < KEYFRAMES.firewallStartMs) return 'collapsing';
    if (t < KEYFRAMES.particleStartMs) return 'firewall';
    if (t < KEYFRAMES.mapResolveStartMs) return 'particles';
    if (t < durationMs) return 'resolving';
    return 'world';
}

/**
 * One canonical progress clock for a reversible Apartment/world handoff.
 *
 * The visual timeline is always expressed in the same coordinate system:
 * Apartment is 0 and world is 1. Reversing therefore changes only the target
 * endpoint; it never constructs a second animation and never re-bases the
 * current frame. That invariant is what lets a wheel or pinch reversal retrace
 * the exact values returned by handoffFrame() without an opacity or transform
 * discontinuity.
 *
 * Time is supplied by the caller as frame deltas. Keeping the clock pure makes
 * 35/50/75-percent reversals deterministic in Node rather than assertions about
 * whichever frame cadence a browser happened to deliver.
 */
export function createReversibleHandoffMotion({
    direction = 'outward',
    startProgress,
    startVelocity = 0,
    startAcceleration = 0,
    durationMs = HANDOFF_DURATION_MS,
} = {}) {
    if (direction !== 'outward' && direction !== 'inward') {
        throw new TypeError(`Unknown handoff direction: ${String(direction)}`);
    }
    const resolvedDurationMs = Number(durationMs);
    if (!Number.isFinite(resolvedDurationMs) || resolvedDurationMs <= 0) {
        throw new TypeError('durationMs must be a positive finite number');
    }

    const sourceProgress = direction === 'outward' ? 0 : 1;
    const destinationProgress = direction === 'outward' ? 1 : 0;
    let progress = startProgress === undefined
        ? sourceProgress
        : clamp(startProgress);
    let targetProgress = destinationProgress;
    let reversals = 0;
    let segmentElapsedS = 0;
    let segment = null;

    const durationS = resolvedDurationMs / 1000;
    const tempo = HANDOFF_MOTION_REFERENCE_MS / resolvedDurationMs;
    const speedLimit = HANDOFF_MAX_SPEED * tempo;
    const normalAccelerationLimit = HANDOFF_ACCELERATION * tempo * tempo;
    const reversalAccelerationLimit = HANDOFF_REVERSAL_ACCELERATION * tempo * tempo;
    const jerkLimit = HANDOFF_MAX_JERK * tempo * tempo * tempo;
    const numericStartVelocity = Number(startVelocity);
    const numericStartAcceleration = Number(startAcceleration);
    let velocity = Number.isFinite(numericStartVelocity)
        ? Math.max(-speedLimit, Math.min(speedLimit, numericStartVelocity))
        : 0;
    let acceleration = Number.isFinite(numericStartAcceleration)
        ? Math.max(-normalAccelerationLimit, Math.min(normalAccelerationLimit, numericStartAcceleration))
        : 0;
    let jerk = 0;

    function directionForTarget(target) {
        return target === 1 ? 'outward' : 'inward';
    }

    function snapshot() {
        const settled = !segment && progress === targetProgress;
        return Object.freeze({
            progress,
            velocity,
            acceleration,
            jerk,
            targetProgress,
            targetDirection: directionForTarget(targetProgress),
            sourceProgress,
            destinationProgress,
            reversing: targetProgress === sourceProgress,
            reversals,
            brakingToEndpoint: Boolean(segment && segment.targetProgress !== targetProgress),
            brakingToInterior: segment?.kind === 'brake',
            segmentDurationMs: segment ? segment.durationS * 1000 : 0,
            speedLimit,
            accelerationLimit: segment?.accelerationLimit ?? normalAccelerationLimit,
            jerkLimit,
            settled,
            endpoint: settled
                ? (progress === destinationProgress ? 'destination' : 'source')
                : null,
        });
    }

    function evaluate(candidate, elapsedS) {
        const t = Math.min(candidate.durationS, Math.max(0, elapsedS));
        if (candidate.kind === 'brake') {
            let state = {
                progress: candidate.startProgress,
                velocity: candidate.startVelocity,
                acceleration: candidate.startAcceleration,
                jerk: 0,
            };
            let remaining = t;
            for (const phase of candidate.phases) {
                if (remaining <= 0) break;
                const phaseTime = Math.min(remaining, phase.durationS);
                const phaseTime2 = phaseTime * phaseTime;
                const phaseTime3 = phaseTime2 * phaseTime;
                state = {
                    progress: state.progress
                        + state.velocity * phaseTime
                        + state.acceleration * phaseTime2 / 2
                        + phase.jerk * phaseTime3 / 6,
                    velocity: state.velocity
                        + state.acceleration * phaseTime
                        + phase.jerk * phaseTime2 / 2,
                    acceleration: state.acceleration + phase.jerk * phaseTime,
                    jerk: phase.jerk,
                };
                remaining -= phaseTime;
            }
            return state;
        }
        const [c0, c1, c2, c3, c4, c5] = candidate.coefficients;
        const t2 = t * t;
        const t3 = t2 * t;
        const t4 = t3 * t;
        const t5 = t4 * t;
        return {
            progress: c0 + c1 * t + c2 * t2 + c3 * t3 + c4 * t4 + c5 * t5,
            velocity: c1 + 2 * c2 * t + 3 * c3 * t2 + 4 * c4 * t3 + 5 * c5 * t4,
            acceleration: 2 * c2 + 6 * c3 * t + 12 * c4 * t2 + 20 * c5 * t3,
            jerk: 6 * c3 + 24 * c4 * t + 60 * c5 * t2,
        };
    }

    function coefficientsForState(
        startPosition,
        startVelocityValue,
        startAccelerationValue,
        target,
        segmentDurationS,
    ) {
        const t = segmentDurationS;
        const t2 = t * t;
        const t3 = t2 * t;
        const t4 = t3 * t;
        const t5 = t4 * t;
        const x0 = startPosition;
        const v0 = startVelocityValue;
        const a0 = startAccelerationValue;
        const x1 = target;
        // End velocity and acceleration are deliberately zero. This makes the
        // exact semantic endpoint a rest state instead of a clamped overshoot.
        return [
            x0,
            v0,
            a0 / 2,
            (20 * (x1 - x0) - 12 * v0 * t - 3 * a0 * t2) / (2 * t3),
            (30 * (x0 - x1) + 16 * v0 * t + 3 * a0 * t2) / (2 * t4),
            (12 * (x1 - x0) - 6 * v0 * t - a0 * t2) / (2 * t5),
        ];
    }

    function coefficientsFor(target, segmentDurationS) {
        return coefficientsForState(
            progress,
            velocity,
            acceleration,
            target,
            segmentDurationS,
        );
    }

    function candidateIsBounded(candidate) {
        // Sampling is only used to choose duration; runtime evaluation remains
        // analytic. Ninety-six intervals catch the extrema with ample margin
        // because velocity/acceleration/jerk are degree 4/3/2 polynomials.
        for (let index = 0; index <= 96; index += 1) {
            const state = evaluate(candidate, candidate.durationS * index / 96);
            if (state.progress < -1e-7 || state.progress > 1 + 1e-7) return false;
            if (Math.abs(state.velocity) > speedLimit * 1.0001) return false;
            if (Math.abs(state.acceleration) > candidate.accelerationLimit * 1.0001) return false;
            if (Math.abs(state.jerk) > jerkLimit * 1.0001) return false;
        }
        return true;
    }

    function buildSegment({ reversal = false, preferredDurationS = null } = {}) {
        const distance = Math.abs(targetProgress - progress);
        const accelerationLimit = reversal
            ? reversalAccelerationLimit
            : normalAccelerationLimit;
        /* sqrt(distance) keeps acceleration consistent across a partial
         * preview start while a full, rest-to-rest trip remains the selected
         * 300/420/650/1000ms tempo. */
        const requestedDurationS = Number(preferredDurationS);
        const nominalDurationS = Number.isFinite(requestedDurationS)
            ? Math.min(durationS, Math.max(1 / 240, requestedDurationS))
            : Math.max(1 / 240, durationS * Math.sqrt(Math.max(distance, 1e-6)));
        /* Feasibility is not monotonic in duration once velocity and
         * acceleration are carried across a reversal: a 170ms curve can stay
         * in bounds while both 120ms and 210ms curves overshoot. Search on
         * both sides of the intended tempo, capped at the selected full-trip
         * duration, rather than growing blindly into a multi-second handoff. */
        const factors = [
            1, 0.92, 1.08, 0.84, 1.16, 0.76, 1.24, 0.68,
            1.32, 0.60, 1.40, 0.52, 1.48, 0.44, 1.56, 0.36,
            1.64, 0.28, 1.72, 0.20,
        ];
        const durations = [];
        const seen = new Set();
        for (const factor of factors) {
            const candidateDurationS = Math.min(durationS, Math.max(1 / 240, nominalDurationS * factor));
            const key = candidateDurationS.toFixed(9);
            if (!seen.has(key)) {
                seen.add(key);
                durations.push(candidateDurationS);
            }
        }
        if (!seen.has(durationS.toFixed(9))) durations.push(durationS);
        for (const candidateDurationS of durations) {
            const candidate = {
                durationS: candidateDurationS,
                accelerationLimit,
                targetProgress,
                coefficients: coefficientsFor(targetProgress, candidateDurationS),
            };
            if (candidateIsBounded(candidate)) {
                segment = candidate;
                segmentElapsedS = 0;
                return true;
            }
        }
        /* Never accept an unbounded polynomial. When a rapid reversal creates
         * a temporarily infeasible tangent, advance() uses the prior bounded
         * curve only as a short braking envelope and retries this search in
         * fine-grained slices until the latest target becomes feasible. */
        return false;
    }

    /*
     * Stop an infeasible tangent at the nearest physically reachable interior
     * point, then spend the rest of the selected tempo on the latest endpoint.
     *
     * A direct rest-to-rest quintic is not always possible after several rapid
     * reversals.  The old fallback kept following the previous endpoint until
     * a new quintic became feasible.  It stayed within the numeric limits, but
     * could rewind almost the entire visual journey after the user had already
     * asked to go the other way.  That reads as ignored input, not momentum.
     *
     * This is a standard jerk-limited S-curve stop. In coordinates aligned to
     * the current velocity it ramps acceleration toward -A, optionally holds
     * it, then ramps back to zero. Position, velocity and acceleration are
     * continuous at both joins; jerk is bounded and may change only at a join.
     * The resulting stop point and a follow-on quintic are both certified by
     * the same sampled bound check used for every other segment.
     */
    function buildInteriorBrake() {
        const motionSign = Math.sign(velocity) || Math.sign(acceleration);
        if (!motionSign) return false;
        const speedAlong = motionSign * velocity;
        const accelerationAlong = motionSign * acceleration;
        if (!(speedAlong > 1e-9)) return false;

        const limit = reversalAccelerationLimit;
        let peak = Math.sqrt(
            Math.max(0, jerkLimit * speedAlong + accelerationAlong * accelerationAlong / 2),
        );
        let rampInS;
        let holdS = 0;
        let rampOutS;
        if (peak <= limit && accelerationAlong + peak >= 0) {
            rampInS = (accelerationAlong + peak) / jerkLimit;
            rampOutS = peak / jerkLimit;
        } else {
            peak = limit;
            rampInS = (accelerationAlong + peak) / jerkLimit;
            if (rampInS < 0) return false;
            const rampVelocityDelta = (
                accelerationAlong * accelerationAlong - 2 * peak * peak
            ) / (2 * jerkLimit);
            holdS = (speedAlong + rampVelocityDelta) / peak;
            if (holdS < 0) return false;
            rampOutS = peak / jerkLimit;
        }

        const brakeDurationS = rampInS + holdS + rampOutS;
        if (!(brakeDurationS > 0) || brakeDurationS >= durationS - 1 / 240) return false;
        const phases = [
            { durationS: rampInS, jerk: -motionSign * jerkLimit },
            { durationS: holdS, jerk: 0 },
            { durationS: rampOutS, jerk: motionSign * jerkLimit },
        ].filter((phase) => phase.durationS > 1e-12);
        const candidate = {
            kind: 'brake',
            durationS: brakeDurationS,
            accelerationLimit: reversalAccelerationLimit,
            targetProgress,
            startProgress: progress,
            startVelocity: velocity,
            startAcceleration: acceleration,
            phases,
            endProgress: null,
            followSegment: null,
        };
        const stopped = evaluate(candidate, brakeDurationS);
        if (stopped.progress <= Number.EPSILON
            || stopped.progress >= 1 - Number.EPSILON
            || Math.abs(stopped.velocity) > 1e-7
            || Math.abs(stopped.acceleration) > 1e-7
            || !candidateIsBounded(candidate)) {
            return false;
        }
        candidate.endProgress = stopped.progress;

        /* The latest intent outranks a total-duration promise. A pathological
         * carried tangent may need a short physical stop plus as much as one
         * normal selected-tempo trip; rejecting that valid stop merely to fit
         * both pieces inside one timer resurrects the stale-endpoint rewind. */
        const maximumFollowS = durationS;
        const followAt = (candidateDurationS) => ({
            kind: 'endpoint',
            durationS: candidateDurationS,
            accelerationLimit: reversalAccelerationLimit,
            targetProgress,
            coefficients: coefficientsForState(
                stopped.progress,
                0,
                0,
                targetProgress,
                candidateDurationS,
            ),
        });
        let follow = followAt(maximumFollowS);
        if (!candidateIsBounded(follow)) return false;
        /* A rest-to-rest quintic becomes less demanding as its duration grows,
         * so bisect the feasible interval and keep the shortest certified
         * continuation. The brake now feels like a response, not a mandatory
         * full-tempo pause before the latest intent starts moving. */
        let lowerS = 1 / 240;
        let upperS = maximumFollowS;
        for (let iteration = 0; iteration < 18; iteration += 1) {
            const midpointS = (lowerS + upperS) / 2;
            const probe = followAt(midpointS);
            if (candidateIsBounded(probe)) {
                upperS = midpointS;
                follow = probe;
            } else {
                lowerS = midpointS;
            }
        }
        candidate.followSegment = follow;
        segment = candidate;
        segmentElapsedS = 0;
        return true;
    }

    function buildLatestSegment({ reversal = false } = {}) {
        const targetDelta = targetProgress - progress;
        const movingAway = targetDelta * velocity < -1e-9;
        if (reversal && movingAway && buildInteriorBrake()) return true;
        return buildSegment({ reversal }) || buildInteriorBrake();
    }

    function retarget(nextDirection) {
        if (nextDirection !== 'outward' && nextDirection !== 'inward') {
            throw new TypeError(`Unknown handoff direction: ${String(nextDirection)}`);
        }
        const nextTarget = nextDirection === 'outward' ? 1 : 0;
        if (nextTarget !== targetProgress) {
            if (!segment && !buildSegment()) {
                /* External seed derivatives can be physically impossible at
                 * an endpoint. A rest-to-rest segment is the only bounded
                 * initialization available before anything has painted. */
                velocity = 0;
                acceleration = 0;
                buildSegment();
            }
            targetProgress = nextTarget;
            reversals += 1;
            /* progress, velocity, and acceleration are intentionally untouched.
             * The new quintic begins with all three boundary conditions, making
             * the reversal C2-continuous. */
            buildLatestSegment({ reversal: true });
        }
        // Deliberately do not advance here. The frame at the moment of reversal
        // is bit-for-bit the frame that was already on screen.
        return snapshot();
    }

    function advance(deltaMs) {
        const numericDelta = Number(deltaMs);
        const safeDeltaMs = Number.isFinite(numericDelta) && numericDelta > 0
            ? numericDelta
            : 0;
        /* A settled semantic endpoint is an absorbing state until retarget().
         * Building a zero-distance quintic here puts progress at 1-epsilon on
         * the next frame and reports the already-complete handoff as moving. */
        if (!segment && progress === targetProgress) return snapshot();
        /* A rapid latest-intent change can arrive while the preserved C2
         * tangent makes a direct segment temporarily infeasible. Keep the
         * existing bounded curve only as a short braking envelope. While it is
         * stale, consume even a long/dropped frame in <=240Hz slices and retry
         * the newest target before every slice; one 50ms frame therefore cannot
         * bypass arbitration and drive all the way to an old endpoint. */
        if (!segment && !buildLatestSegment()) {
            velocity = 0;
            acceleration = 0;
            if (!buildSegment()) {
                /* A rest-to-rest full-trip quintic is always feasible under
                 * the authored limits; keep this guard fail-closed if those
                 * limits are ever changed. */
                progress = targetProgress;
                return snapshot();
            }
        }
        let remainingS = safeDeltaMs / 1000;
        let guard = 0;
        do {
            guard += 1;
            if (segment && segment.targetProgress !== targetProgress) {
                buildLatestSegment({ reversal: true });
            }
            if (!segment) {
                if (progress === targetProgress) break;
                if (!buildLatestSegment({ reversal: true })) break;
            }
            const stale = segment.targetProgress !== targetProgress;
            const toSegmentEndS = Math.max(0, segment.durationS - segmentElapsedS);
            const consumedS = Math.min(
                remainingS,
                toSegmentEndS,
                stale ? 1 / 240 : remainingS,
            );
            segmentElapsedS = Math.min(segment.durationS, segmentElapsedS + consumedS);
            remainingS = Math.max(0, remainingS - consumedS);
            if (segmentElapsedS >= segment.durationS - 1e-12) {
                const completed = segment;
                const completedTarget = completed.targetProgress;
                progress = completed.kind === 'brake'
                    ? completed.endProgress
                    : completedTarget;
                velocity = 0;
                acceleration = 0;
                jerk = 0;
                segment = null;
                segmentElapsedS = 0;
                if (completed.kind === 'brake'
                    && completedTarget === targetProgress
                    && completed.followSegment) {
                    segment = completed.followSegment;
                } else if (progress !== targetProgress) {
                    buildLatestSegment({ reversal: true });
                }
            } else {
                const next = evaluate(segment, segmentElapsedS);
                // Duration selection guarantees these are in range; clamp only the
                // final floating-point ulp so CSS never receives 1.0000000000000002.
                /* A polynomial evaluated a few ulps before its endpoint can round
                 * just beyond [0, 1]. Keep an active segment strictly interior so
                 * endpoint equality always means zero velocity/acceleration and a
                 * completed semantic handoff, never a clamped moving frame. */
                progress = Math.min(1 - Number.EPSILON, Math.max(Number.EPSILON, next.progress));
                velocity = next.velocity;
                acceleration = next.acceleration;
                jerk = next.jerk;
            }
        } while (remainingS > 1e-12 && guard < 4096);
        if (guard >= 4096 && remainingS > 1e-12) {
            /* Fail closed on pathological caller deltas. A subsequent advance
             * resumes from the exact bounded state instead of jumping. */
            jerk = 0;
        }
        return snapshot();
    }

    return Object.freeze({ advance, retarget, snapshot });
}

/**
 * Every progress step at which Apartment pixels and world pixels are both
 * visible. The product contract is that this is empty: the firewall exists to
 * make the two scales mutually exclusive, and a non-empty result means the
 * curves no longer enforce it.
 *
 * `epsilon` is the visibility floor. Anything at or below it is not perceptible
 * and is not a violation.
 */
export function noCoexistenceViolations(steps = 651, epsilon = 0.002, timeline = DEFAULT_HANDOFF_TIMELINE) {
    const violations = [];
    for (let index = 0; index < steps; index += 1) {
        const p = index / (steps - 1);
        const frame = handoffFrame(p, { timeline });
        // What the viewer can actually see of each surface, after the firewall
        // that paints over both of them.
        const apartmentVisible = frame.apartmentOpacity * (1 - frame.compositorOpacity);
        const worldVisible = frame.worldOpacity * (1 - frame.compositorOpacity);
        if (apartmentVisible > epsilon && worldVisible > epsilon) {
            violations.push({
                progress: p,
                timelineMs: frame.timelineMs,
                apartmentVisible,
                worldVisible,
            });
        }
    }
    return violations;
}
