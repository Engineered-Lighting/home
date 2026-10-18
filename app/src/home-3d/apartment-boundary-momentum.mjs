/*
 * apartment-boundary-momentum.mjs — pure Apartment-side world-boundary input.
 *
 * The resident Apartment rig has discrete zoom detents. At its outermost
 * detent, however, a wheel gesture should feel continuous before it commits the
 * renderer handoff: the Apartment recedes a little, the gesture can still be
 * taken back, then a deliberate continuation snaps exactly once into the world.
 *
 * This module owns that small reversible band. It deliberately owns no DOM,
 * clock, rig, renderer, or coordinator. Callers feed real WheelEvent fields and
 * frame deltas, apply `state.progress` to the existing handoff timeline, and
 * translate emitted effects into coordinator intents. That keeps input-device
 * normalization and latest-intent behavior deterministic enough to test.
 *
 * Commit is counted in NOTCHES, not in accumulated pixels. One notch of any
 * size never commits: a Windows detent is 100px, a pathological one is capped
 * at 220px, and a high-resolution wheel splits one detent across several small
 * events, so a pixel threshold either commits on one big notch or needs four
 * small ones. Instead an event carrying a whole detent is one notch, smaller
 * events share one notch per notch window, one notch is worth well under the
 * threshold, and the charge is HELD for a beat after the last notch so a
 * deliberate second one, even half a second later, always lands on top of the
 * first.
 *
 * The commit is decided AT a notch, never on the coast after it. The coast is
 * exponential, so `charge + velocity * coastTimeS` is exactly where a notch will
 * leave the pull. A notch that leaves it within `commitTolerance` of the
 * threshold commits within `commitDelayS`; any other notch cannot commit on its
 * own, however long it coasts. Without that, notches just too far apart to
 * commit promptly reached the threshold asymptotically and started the handoff
 * half a second after the user stopped, at almost no velocity.
 *
 * SLOW NOTCHES STILL LEAVE. The charge springs back after its hold, so notches
 * spaced more than ~0.8 s apart never add up, and a deliberate slow scroll --
 * or a device or embedded browser that delivers each flick as ONE coalesced
 * event -- could not leave the Apartment at all (measured: 0.9 s spacing never
 * committed). So outward travel is also counted as a STREAK, in whole notches,
 * across `streakWindowS` of wall-clock time between outward events, stamped by
 * the WheelEvent's own timeStamp because frames stop once the pull is idle.
 * `streakCommitNotches` of streak commits through the same prompt-velocity path
 * as a near miss. One event is at most one notch of streak, so a single notch,
 * however large, still never commits; a reversal ends the streak.
 */

import {
    PREVIEW_PROGRESS,
    SNAP_THRESHOLD,
    handoffFrame,
} from './handoff-timeline.mjs';

export const BOUNDARY_PHASE = Object.freeze({
    IDLE: 'idle',
    PREVIEW: 'preview',
    TRIGGERED: 'triggered',
    RETURNING: 'returning',
    COMMITTED: 'committed',
});

export const BOUNDARY_WHEEL_LINE_PX = 33;
export const BOUNDARY_MAX_EVENT_PX = SNAP_THRESHOLD;
const BOUNDARY_RESISTANCE = 0.5;

const DEFAULTS = Object.freeze({
    previewProgress: PREVIEW_PROGRESS,
    /* Wheel travel that makes one whole notch. A Windows/Chromium detent is
     * 100px and Firefox's three lines are 99px, so either saturates a notch;
     * trackpad deltas below it count in proportion. */
    notchPx: 50,
    /* An event at least this large is a whole detent and is its own notch,
     * however soon after the previous one it arrives: Chromium's 100px and
     * Firefox's 99px, and those at browser zoom up to 133%. That is what lets
     * a fast flick of two or three detents 10-40ms apart commit. */
    detentPx: 75,
    /* Smaller events inside one window share one notch budget, so a
     * high-resolution wheel that delivers a detent as several events (or as
     * two half-detent events of 50px) is still one notch. The window is
     * measured in frame time, so at 60Hz it is effectively 50-66ms. */
    notchWindowS: 0.05,
    /* Charge per notch: `directGain` at once, plus `impulseGain * coastTimeS`
     * coasting in over the next few hundred milliseconds. Their sum (0.598)
     * is what makes one notch a legible pull that can never commit, and two
     * notches a commit even after the first has fully coasted. */
    directGain: 0.4,
    impulseGain: 0.9,
    reversalGain: 0.5,
    reversalImpulseGain: 2.4,
    maxVelocity: 4.4,
    coastTimeS: 0.22,
    /* The pull stays put this long after the last notch, then springs back
     * with an exponential whose time constant leaves ~5% after 0.45s. */
    holdTimeS: 0.6,
    returnTimeS: 0.15,
    /* A notch whose coast would end this close to the threshold commits, and
     * does so within `commitDelayS` of that notch (its velocity is raised just
     * enough). Well above one notch's 0.598, so one notch still never commits,
     * and the near-miss a notch can leave behind is at most 95% of the pull. */
    commitTolerance: 0.05,
    commitDelayS: 0.1,
    /* Outward notches no further apart than this continue one streak, and a
     * streak of this many whole notches commits whatever their spacing. */
    streakWindowS: 2.5,
    streakCommitNotches: 2,
    /* A streak usually completes after the pull has sprung back, so it gets a
     * longer run-up than a near miss: 0.25 s crosses from ~0.4 of the pull at
     * under maxVelocity, where commitDelayS would need a jump to the snap. */
    streakCommitDelayS: 0.25,
    restVelocity: 0.025,
    settleCharge: 0.001,
    rearmCharge: 0.16,
    reversalDeadzonePx: 1.5,
    intentPrefix: 'apartment-world-boundary',
});

function finite(value, fallback) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : fallback;
}

function clamp(value, min = 0, max = 1) {
    return Math.min(max, Math.max(min, finite(value, min)));
}

function positive(value, fallback, minimum = Number.EPSILON) {
    return Math.max(minimum, finite(value, fallback));
}

function normalizeConfig(options = {}) {
    return Object.freeze({
        previewProgress: clamp(options.previewProgress ?? DEFAULTS.previewProgress),
        notchPx: positive(options.notchPx, DEFAULTS.notchPx, 1),
        detentPx: positive(options.detentPx, DEFAULTS.detentPx, 1),
        notchWindowS: positive(options.notchWindowS, DEFAULTS.notchWindowS, 0),
        directGain: positive(options.directGain, DEFAULTS.directGain, 0),
        impulseGain: positive(options.impulseGain, DEFAULTS.impulseGain, 0),
        reversalGain: positive(options.reversalGain, DEFAULTS.reversalGain, 0),
        reversalImpulseGain: positive(
            options.reversalImpulseGain,
            DEFAULTS.reversalImpulseGain,
            0,
        ),
        maxVelocity: positive(options.maxVelocity, DEFAULTS.maxVelocity, 0.01),
        coastTimeS: positive(options.coastTimeS, DEFAULTS.coastTimeS, 0.001),
        holdTimeS: positive(options.holdTimeS, DEFAULTS.holdTimeS, 0),
        returnTimeS: positive(options.returnTimeS, DEFAULTS.returnTimeS, 0.001),
        commitTolerance: clamp(options.commitTolerance ?? DEFAULTS.commitTolerance, 0, 0.2),
        commitDelayS: positive(options.commitDelayS, DEFAULTS.commitDelayS, 0.001),
        streakWindowS: positive(options.streakWindowS, DEFAULTS.streakWindowS, 0),
        // Below 1.5 a single notch could commit; that guarantee is not configurable.
        streakCommitNotches: Math.max(1.5, finite(options.streakCommitNotches, DEFAULTS.streakCommitNotches)),
        streakCommitDelayS: positive(options.streakCommitDelayS, DEFAULTS.streakCommitDelayS, 0.001),
        restVelocity: positive(options.restVelocity, DEFAULTS.restVelocity, 0),
        settleCharge: clamp(options.settleCharge ?? DEFAULTS.settleCharge, 0, 0.1),
        rearmCharge: clamp(options.rearmCharge ?? DEFAULTS.rearmCharge, 0.01, 0.8),
        reversalDeadzonePx: positive(
            options.reversalDeadzonePx,
            DEFAULTS.reversalDeadzonePx,
            0,
        ),
        intentPrefix: typeof options.intentPrefix === 'string' && options.intentPrefix.trim()
            ? options.intentPrefix.trim()
            : DEFAULTS.intentPrefix,
    });
}

/**
 * The charge one isolated notch contributes once it has fully coasted. The
 * default is below the snap threshold by construction; that margin is the
 * "one notch never commits" guarantee, independent of the notch's pixel size.
 */
export function boundaryNotchCharge(options = {}) {
    const config = normalizeConfig(options);
    return config.directGain + Math.min(config.impulseGain, config.maxVelocity) * config.coastTimeS;
}

/**
 * Convert native WheelEvent units to signed CSS-pixel-equivalent travel.
 * Positive is outward, matching wheel-down. Magnitude is preserved—especially
 * sub-pixel trackpad input—and only pathological single events are capped.
 */
export function normalizeBoundaryWheel({
    deltaY = 0,
    deltaMode = 0,
    viewportHeightPx = 800,
} = {}) {
    const raw = finite(deltaY, 0);
    if (raw === 0) return 0;
    const multiplier = deltaMode === 1
        ? BOUNDARY_WHEEL_LINE_PX
        : deltaMode === 2
            ? positive(viewportHeightPx, 800, 1)
            : 1;
    return clamp(raw * multiplier, -BOUNDARY_MAX_EVENT_PX, BOUNDARY_MAX_EVENT_PX);
}

/** The preview resists as it nears the snap point, like a short elastic pull. */
export function elasticBoundaryPreview(charge) {
    const t = clamp(charge);
    /* Blend a linear pull with the old ease-out curve. The old `t * (2 - t)`
     * reached the snap with zero slope, which mathematically forced the visible
     * Apartment to stop before the compositor could continue. Keeping a
     * positive slope at t=1 retains the resistant feel while giving the
     * handoff clock real momentum to inherit. */
    return t + BOUNDARY_RESISTANCE * t * (1 - t);
}

export function elasticBoundaryPreviewSlope(charge) {
    const t = clamp(charge);
    return 1 + BOUNDARY_RESISTANCE * (1 - 2 * t);
}

/**
 * Convert canonical handoff progress into the rig's radius multiplier.
 *
 * Perspective size is inverse distance. Pulling the rig back by
 * `1 / apartmentScale - 1` therefore paints the same apparent Apartment size
 * as the first compositor frame. At the snap, the host can atomically clear
 * rig overshoot and apply the compositor transform with no size discontinuity.
 */
export function boundaryRigOvershoot(progress, {
    hostWidthPx = 1_280,
    maxOvershoot = 0.25,
} = {}) {
    const previewProgress = Math.min(PREVIEW_PROGRESS, clamp(progress));
    const scale = handoffFrame(previewProgress, { hostWidthPx }).apartmentScale;
    return clamp((1 / Math.max(1e-6, scale)) - 1, 0, Math.max(0, finite(maxOvershoot, 0.25)));
}

function canonicalProgress(charge, phase, reducedMotion, config) {
    if (phase === BOUNDARY_PHASE.COMMITTED) return 1;
    if (reducedMotion) return 0;
    return config.previewProgress * elasticBoundaryPreview(charge);
}

function makeState(fields) {
    const config = fields.config;
    const charge = clamp(fields.charge);
    const phase = Object.values(BOUNDARY_PHASE).includes(fields.phase)
        ? fields.phase
        : BOUNDARY_PHASE.IDLE;
    const reducedMotion = fields.reducedMotion === true;
    const velocity = clamp(fields.velocity, -config.maxVelocity, config.maxVelocity);
    const progressVelocity = reducedMotion || phase === BOUNDARY_PHASE.COMMITTED
        ? 0
        : config.previewProgress * elasticBoundaryPreviewSlope(charge) * velocity;
    /* Once input is released, charge velocity decays exponentially. Supplying
     * that local derivative to the compositor makes the warm-path seam C2 in
     * the same model instead of pretending the gesture reached a rest state. */
    const chargeAcceleration = -velocity / config.coastTimeS;
    const progressAcceleration = reducedMotion || phase === BOUNDARY_PHASE.COMMITTED
        ? 0
        : config.previewProgress * (
            -2 * BOUNDARY_RESISTANCE * velocity * velocity
            + elasticBoundaryPreviewSlope(charge) * chargeAcceleration
        );
    return Object.freeze({
        phase,
        charge,
        progress: canonicalProgress(charge, phase, reducedMotion, config),
        velocity,
        progressVelocity,
        progressAcceleration,
        /* Frame-integrated time since the last outward event (the hold), and
         * the open notch window: its age and the pixels it has consumed. */
        sinceInputS: Math.max(0, finite(fields.sinceInputS, 0)),
        notchAgeS: Math.max(0, finite(fields.notchAgeS, config.notchWindowS)),
        notchPx: Math.max(0, finite(fields.notchPx, 0)),
        /* Whole notches of outward travel in the current streak, and the
         * WheelEvent timeStamp (ms) of its latest outward event. */
        streakNotches: Math.max(0, finite(fields.streakNotches, 0)),
        streakAtMs: Number.isFinite(fields.streakAtMs) ? fields.streakAtMs : null,
        armed: fields.armed !== false,
        sequence: Math.max(0, Math.trunc(finite(fields.sequence, 0))),
        activeIntentId: typeof fields.activeIntentId === 'string'
            ? fields.activeIntentId
            : null,
        reducedMotion,
        config,
    });
}

function result(state, effects = [], accepted = true) {
    return Object.freeze({
        state,
        effects: Object.freeze(effects.map((effect) => Object.freeze(effect))),
        accepted,
    });
}

export function createBoundaryMomentumState(options = {}) {
    const config = normalizeConfig(options);
    return makeState({
        phase: BOUNDARY_PHASE.IDLE,
        charge: 0,
        velocity: 0,
        armed: true,
        sequence: 0,
        activeIntentId: null,
        reducedMotion: options.reducedMotion === true,
        config,
    });
}

function emitSnap(state) {
    if (!state.armed || state.activeIntentId) return result(state, [], false);
    const sequence = state.sequence + 1;
    const intentId = `${state.config.intentPrefix}:${sequence}`;
    const next = makeState({
        ...state,
        phase: BOUNDARY_PHASE.TRIGGERED,
        charge: 1,
        velocity: Math.max(0, state.velocity),
        armed: false,
        sequence,
        activeIntentId: intentId,
        streakNotches: 0,
        streakAtMs: null,
    });
    return result(next, [{
        type: 'snap',
        direction: 'outward',
        intentId,
        fromProgress: next.progress,
        fromVelocity: next.progressVelocity,
        fromAcceleration: next.progressAcceleration,
        reducedMotion: next.reducedMotion,
    }]);
}

function cancelActive(state, reason) {
    const effects = state.activeIntentId
        ? [{ type: 'cancel', intentId: state.activeIntentId, reason }]
        : [];
    const next = makeState({
        ...state,
        phase: state.charge > state.config.settleCharge
            ? BOUNDARY_PHASE.RETURNING
            : BOUNDARY_PHASE.IDLE,
        velocity: Math.min(state.velocity, -state.config.restVelocity * 2),
        armed: state.activeIntentId ? false : state.armed,
        activeIntentId: null,
    });
    return result(next, effects);
}

function wheel(state, action) {
    const pixels = normalizeBoundaryWheel(action);
    if (pixels === 0 || state.phase === BOUNDARY_PHASE.COMMITTED) {
        return result(state, [], false);
    }

    const config = state.config;

    // Wheel up is an immediate reversal. Tiny sign jitter is ignored only once
    // a snap is in flight; a deliberate reverse invalidates it in this event.
    if (pixels < 0) {
        if (state.activeIntentId && Math.abs(pixels) < config.reversalDeadzonePx) {
            return result(state, [], false);
        }
        const notches = Math.min(1, Math.abs(pixels) / config.notchPx);
        const cancellation = cancelActive(state, 'gesture-reversed');
        const base = cancellation.state;
        const charge = clamp(base.charge - notches * config.reversalGain);
        const velocity = Math.min(0, base.velocity)
            - notches * config.reversalImpulseGain;
        const next = makeState({
            ...base,
            phase: charge > config.settleCharge
                ? BOUNDARY_PHASE.RETURNING
                : BOUNDARY_PHASE.IDLE,
            charge,
            velocity,
            // The next outward event starts a fresh notch, and a fresh streak.
            notchAgeS: config.notchWindowS,
            notchPx: 0,
            streakNotches: 0,
            streakAtMs: null,
        });
        return result(next, cancellation.effects);
    }

    // Hysteresis: after cancelling a snap, outward input cannot manufacture a
    // second intent until the preview has visibly returned near the detent.
    if (!state.armed || state.activeIntentId) return result(state, [], false);

    /* A whole detent is one notch on its own. Smaller events get at most one
     * notch of charge per notch window; events past the budget are still
     * input: they keep the pull held, they just cannot add to it. */
    const freshNotch = pixels >= config.detentPx
        || state.phase === BOUNDARY_PHASE.IDLE
        || state.notchAgeS >= config.notchWindowS;
    const usedPx = freshNotch ? 0 : state.notchPx;
    const notches = Math.min(pixels, Math.max(0, config.notchPx - usedPx)) / config.notchPx;
    const charge = clamp(state.charge + notches * config.directGain);
    const impelled = clamp(
        Math.max(0, state.velocity) + notches * config.impulseGain,
        0,
        config.maxVelocity,
    );
    /* The streak (see the header). Without a timestamp there is no wall clock
     * across an idle pull, so the event simply starts a new streak. */
    const atMs = finite(action.timeStampMs, Number.NaN);
    const continues = Number.isFinite(atMs) && state.streakAtMs !== null
        && atMs >= state.streakAtMs && (atMs - state.streakAtMs) / 1_000 <= config.streakWindowS;
    const streakNotches = (continues ? state.streakNotches : 0) + notches;
    const commit = commitVelocity(charge, impelled, config, {
        force: streakNotches >= config.streakCommitNotches - 1e-9,
    });
    const next = makeState({
        ...state,
        phase: BOUNDARY_PHASE.PREVIEW,
        charge,
        velocity: clamp(commit.velocity, 0, config.maxVelocity),
        sinceInputS: 0,
        notchAgeS: freshNotch ? 0 : state.notchAgeS,
        notchPx: usedPx + pixels,
        streakNotches,
        streakAtMs: Number.isFinite(atMs) ? atMs : null,
    });
    return charge >= 1 || commit.now ? emitSnap(next) : result(next);
}

/*
 * The commit decision, taken at a notch.
 *
 * Between inputs the coast is exact exponential decay, so charge + velocity *
 * coastTimeS is invariant along it: it is where this notch leaves the pull.
 * Short of the threshold by more than the tolerance, the notch cannot commit
 * (the velocity is untouched and the coast stops short). Otherwise the velocity
 * is raised, never lowered, to the least that crosses the threshold within
 * commitDelayS, so the handoff starts promptly and inherits real momentum. A
 * crossing that would need more than maxVelocity snaps at once. A completed
 * streak (`force`) takes the same path from wherever the pull has sprung back
 * to, so a slow second notch commits as promptly and smoothly as a fast one.
 */
function commitVelocity(charge, velocity, config, { force = false } = {}) {
    if (charge >= 1) return { velocity, now: false };
    const reach = charge + velocity * config.coastTimeS;
    const nearMiss = reach >= 1 - config.commitTolerance;
    if (!force && !nearMiss) return { velocity, now: false };
    const delayS = nearMiss ? config.commitDelayS : config.streakCommitDelayS;
    const within = config.coastTimeS * (1 - Math.exp(-delayS / config.coastTimeS));
    // The margin lands the crossing on, not a rounding error short of, the delay.
    const needed = ((1 - charge) / within) * (1 + 1e-9);
    if (needed > config.maxVelocity) return { velocity, now: true };
    return { velocity: Math.max(velocity, needed), now: false };
}

function frame(state, action) {
    if ([BOUNDARY_PHASE.IDLE, BOUNDARY_PHASE.TRIGGERED, BOUNDARY_PHASE.COMMITTED]
        .includes(state.phase)) return result(state, [], false);

    const deltaS = clamp(finite(action.deltaMs, 0) / 1_000, 0, 1);
    if (deltaS === 0) return result(state, [], false);
    const config = state.config;
    let phase = state.phase;
    let armed = state.armed;
    const sinceInputS = state.sinceInputS + deltaS;
    const notchAgeS = state.notchAgeS + deltaS;
    /* A preview coasts only until its hold expires, even when that happens
     * mid-frame, so a 30 Hz and a 120 Hz run release at the same instant. */
    const holdLeftS = phase === BOUNDARY_PHASE.PREVIEW
        ? Math.max(0, config.holdTimeS - state.sinceInputS)
        : Number.POSITIVE_INFINITY;
    const coastS = Math.min(deltaS, holdLeftS);
    const decay = Math.exp(-coastS / config.coastTimeS);
    const coastDistance = state.velocity * config.coastTimeS * (1 - decay);
    let charge = clamp(state.charge + coastDistance);
    let velocity = state.velocity * decay;

    if (phase === BOUNDARY_PHASE.PREVIEW && charge >= 1) {
        return emitSnap(makeState({ ...state, charge: 1, velocity, sinceInputS, notchAgeS }));
    }

    // Charge cannot go below the detent, so a reversal that has already
    // arrived there has nothing left to carry.
    if (Math.abs(velocity) <= config.restVelocity || (charge <= 0 && velocity < 0)) velocity = 0;
    let returnS = phase === BOUNDARY_PHASE.RETURNING ? deltaS : 0;
    if (phase === BOUNDARY_PHASE.PREVIEW && deltaS >= holdLeftS) {
        /* The hold is over. Whatever coast remains after it is a fraction of a
         * percent of charge; letting it keep pushing would only delay a return
         * the user has been waiting on since the last notch. */
        phase = BOUNDARY_PHASE.RETURNING;
        velocity = 0;
        returnS = deltaS - holdLeftS;
    }

    if (phase === BOUNDARY_PHASE.RETURNING) {
        // Exact exponential spring-back: frame subdivision changes no outcome
        // when velocity is zero, so 30 Hz and 120 Hz feel like the same gesture.
        charge *= Math.exp(-returnS / config.returnTimeS);
        if (!armed && charge <= config.rearmCharge) armed = true;
        if (charge <= config.settleCharge && velocity === 0) {
            charge = 0;
            phase = BOUNDARY_PHASE.IDLE;
            armed = true;
        }
    }

    return result(makeState({
        ...state, charge, velocity, phase, armed, sinceInputS, notchAgeS,
    }));
}

function settle(state, action) {
    const intentId = typeof action.intentId === 'string' ? action.intentId : '';
    if (!intentId || intentId !== state.activeIntentId) {
        return result(state, [], false);
    }
    const status = String(action.status || '');
    if (status === 'committed') {
        return result(makeState({
            ...state,
            phase: BOUNDARY_PHASE.COMMITTED,
            charge: 1,
            velocity: 0,
            armed: false,
            activeIntentId: null,
            streakNotches: 0,
            streakAtMs: null,
        }));
    }
    if (!['cancelled', 'failed', 'superseded'].includes(status)) {
        throw new TypeError('settled status must be committed, cancelled, failed, or superseded');
    }
    return result(makeState({
        ...state,
        phase: BOUNDARY_PHASE.RETURNING,
        charge: 1,
        velocity: -state.config.restVelocity * 2,
        armed: false,
        activeIntentId: null,
    }));
}

/**
 * Pure reducer. `wheel` consumes native WheelEvent-compatible fields, `frame`
 * consumes caller-measured elapsed milliseconds, `settled` consumes the exact
 * coordinator intent id, and `cancel` is for lifecycle or explicit reversal.
 */
export function reduceBoundaryMomentum(state, action) {
    if (!state?.config) throw new TypeError('boundary momentum state is required');
    if (!action || typeof action.type !== 'string') {
        throw new TypeError('boundary momentum action type is required');
    }
    if (action.type === 'wheel') return wheel(state, action);
    if (action.type === 'frame') return frame(state, action);
    if (action.type === 'settled') return settle(state, action);
    if (action.type === 'cancel') return cancelActive(state, action.reason || 'cancelled');
    if (action.type === 'reduced-motion') {
        return result(makeState({ ...state, reducedMotion: action.value === true }));
    }
    if (action.type === 'reset') {
        const cancellation = cancelActive(state, action.reason || 'reset');
        return result(makeState({
            ...state,
            phase: BOUNDARY_PHASE.IDLE,
            charge: 0,
            velocity: 0,
            armed: true,
            activeIntentId: null,
            streakNotches: 0,
            streakAtMs: null,
            reducedMotion: action.reducedMotion == null
                ? state.reducedMotion
                : action.reducedMotion === true,
        }), cancellation.effects);
    }
    throw new TypeError(`unknown boundary momentum action: ${action.type}`);
}
