/*
 * descent-cloud-plan.mjs -- the cloud passes of the inter-home flight, the
 * turn onto the destination's bearing, and the scripted approach down through
 * the destination's still.
 *
 * WHY PASSES AND NOT A HOLD. The flight used to park just short of the
 * destination (t = 0.86) for up to four seconds while the tiles caught up.
 * Parked in the open, that read as the journey stalling; the owner saw it on
 * every trip. A pass is the same wait spent inside cloud: the camera slows but
 * never stops (speedScale >= MIN_SPEED_SCALE), the cloud is opaque while the
 * next level of detail streams, and it clears on a clock the viewer can feel,
 * 1.2-1.65 s, never an open-ended wait.
 *
 * WHY TWO, AND WHERE. Plan B2 (2026-09-17): pass A at about 420 -> 90 km
 * hides the change from the planet to the region, pass B at about 18 -> 4 km
 * hides the change from the region to the neighbourhood, and the destination's
 * own cloud band (pass C, cloud-handoff.mjs) hides the change from the live map
 * to the still. The journey harness (tools/audit-home-journey-full.mjs, S9)
 * judges exactly that: >= 2 flight passes plus pass C, each 1.0-2.0 s from the
 * first frame above 5 % coverage to the first frame back at or below it, with
 * >= 0.8 s of clear sky between consecutive covers.
 *
 * Everything here is pure and deterministic: the caller supplies elapsed time,
 * the distance to the destination and whether the tiles have gone quiet.
 */
import { CLOUD_CLEAR, CLOUD_ENTER, CLOUD_FULL_START, cloudHandoffRangeFor } from './cloud-handoff.mjs';

/* Where the flight hands over to the approach: far enough out that the
 * destination's cloud band (which begins at 1.9x the handoff range, about
 * 670 m for a 300 m still) is still ahead, close enough that the approach is
 * a few seconds rather than a second flight. */
export const FLIGHT_END_M = 1_200;

/* The passes, outermost first. A pass starts when the destination comes
 * within startM while the flight is descending. endM is the pass's inner
 * edge: a pass that could only begin once the camera is already inside it
 * (deferred by the clear rule, or a flight that never rose above it) is
 * skipped rather than drawn over the wrong altitude. */
export const DESCENT_CLOUD_PASSES = Object.freeze([
    Object.freeze({ id: 'pass-a', startM: 420_000, endM: 90_000 }),
    Object.freeze({ id: 'pass-b', startM: 18_000, endM: 4_000 }),
]);

/* The destination's cloud band, which the approach flies through. The
 * harness's S9 judge recognises these three ids (pass-a/b are flight passes,
 * pass-c is the destination band). */
export const DESTINATION_PASS_ID = 'pass-c';

/* The camera never stops: a quarter speed is the slowest the flight goes. */
export const MIN_SPEED_SCALE = 0.25;

/*
 * The envelope of one pass.
 *
 * fullMinS is 0.55, not the plan's 0.4, and the difference is measured, not
 * taste. The S9 judge times a cover from the first frame above 5 % coverage,
 * and a smoothstep fade is only above 5 % for 87 % of its length: fade-in
 * 0.3 s contributes 0.26 s, fade-out 0.35 s contributes 0.30 s. With a
 * 0.4 s plateau that is 0.96 s -- a pass the judge counts as too short. 0.55 s
 * gives 1.12 s at the shortest and 1.57 s fully extended, inside 1.0-2.0 s
 * with several frames of margin either side even at 30 fps.
 */
export const DESCENT_PASS_TIMING = Object.freeze({
    fadeInS: 0.3,
    fullMinS: 0.55,
    /* How long the plateau may stretch while the tiles below are still
     * loading. The journey preset may shorten it (the 'Tile arrival' group). */
    fullMaxS: 1.0,
    fadeOutS: 0.35,
    /* Clear sky required between two covers. */
    clearMinS: 0.8,
    /* The flight's pace under a plateau, and while a plateau is extended. */
    coverSpeedScale: 0.5,
    waitSpeedScale: MIN_SPEED_SCALE,
    /*
     * The flight's pace in the clear sky BETWEEN two passes (after one has
     * run while another is still to come); 1 before the first and after the
     * last. Measured, not taste: at full pace the 0.8 s of clear sky after
     * pass A crossed 0.85 decades, so pass B could only begin at 9-11 km
     * (7 km with the tiles never quiet) instead of near its 18 km outer
     * edge, and hid the neighbourhood-to-street change rather than the
     * region-to-neighbourhood one. 0.7 lets B begin at 14-18 km. The sky
     * between the passes is the visible part of the descent, so this is
     * time spent where the viewer can see it.
     */
    gapSpeedScale: 0.7,
    /* The pace changes through a first-order lag, so a flapping readiness
     * signal cannot make the camera lurch. */
    speedEaseS: 0.12,
});

function finite(value, fallback) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : fallback;
}

function clamp01(value) {
    return Math.min(1, Math.max(0, finite(value, 0)));
}

/* C1 at both ends: the cloud has no edge where it starts or stops. */
function smoothstep01(value) {
    const t = clamp01(value);
    return t * t * (3 - 2 * t);
}

function smootherstep01(value) {
    const t = clamp01(value);
    return t * t * t * (t * (t * 6 - 15) + 10);
}

/** Merge a partial timing over the defaults, keeping every field finite and
 * the speed floor at MIN_SPEED_SCALE whatever a preset asks for. */
export function normalizePassTiming(timing = null) {
    const source = timing && typeof timing === 'object' ? timing : {};
    const pick = (key, min = 0) => Math.max(min, finite(source[key], DESCENT_PASS_TIMING[key]));
    const fullMinS = pick('fullMinS');
    return Object.freeze({
        fadeInS: pick('fadeInS', 0.01),
        fullMinS,
        fullMaxS: Math.max(fullMinS, pick('fullMaxS')),
        fadeOutS: pick('fadeOutS', 0.01),
        clearMinS: pick('clearMinS'),
        coverSpeedScale: Math.min(1, pick('coverSpeedScale', MIN_SPEED_SCALE)),
        waitSpeedScale: Math.min(1, pick('waitSpeedScale', MIN_SPEED_SCALE)),
        gapSpeedScale: Math.min(1, pick('gapSpeedScale', MIN_SPEED_SCALE)),
        speedEaseS: pick('speedEaseS', 0.001),
    });
}

function normalizePasses(passes) {
    const list = Array.isArray(passes) ? passes : DESCENT_CLOUD_PASSES;
    return list
        .map((pass) => ({
            id: typeof pass?.id === 'string' && pass.id ? pass.id : null,
            startM: finite(pass?.startM, NaN),
            endM: finite(pass?.endM, 0),
        }))
        .filter((pass) => pass.id && pass.startM > 0 && pass.endM >= 0 && pass.endM < pass.startM)
        .sort((a, b) => b.startM - a.startM);
}

/** Shortest and longest a pass can be, fade-in to fade-out. */
export function passEnvelopeS(timing = DESCENT_PASS_TIMING) {
    const t = normalizePassTiming(timing);
    return Object.freeze({
        minS: t.fadeInS + t.fullMinS + t.fadeOutS,
        maxS: t.fadeInS + t.fullMaxS + t.fadeOutS,
    });
}

/**
 * The flight's cloud passes as a small state machine.
 *
 * step({ dtS, destinationM, descending, contentReady }) advances it by dtS
 * seconds of WALL time -- the cloud keeps its own clock, so a slowed camera
 * does not stretch the cover -- and returns the frame to draw:
 *   passId       the pass in force, or null in clear sky
 *   phase        'clear' | 'fade-in' | 'full' | 'fade-out'
 *   coverage     0..1, what the cloud stage and the journey trace report
 *   parallax     0..1 for the fly-through slab: 1 above the deck, 0 at its top
 *   speedScale   what the flight multiplies its own clock by, >= 0.25
 *   extended     the plateau is being held for tiles past fullMinS
 *
 * contentReady false holds the plateau up to fullMaxS; after that the pass
 * clears whatever the tiles are doing. It never blocks and never waits
 * without the camera moving.
 */
export function createDescentCloudPlan({ passes = DESCENT_CLOUD_PASSES, timing = null } = {}) {
    const schedule = normalizePasses(passes);
    const t = normalizePassTiming(timing);
    let nextIndex = 0;
    let active = null;
    /* Clear sky since the last pass ended. Infinity before the first. */
    let clearS = Number.POSITIVE_INFINITY;
    let speed = 1;
    let totalS = 0;
    const history = [];
    let frame = null;

    const compose = (contentReady) => {
        if (!active) {
            return Object.freeze({ passId: null, phase: 'clear', coverage: 0, parallax: 1,
                speedScale: speed, extended: false, passElapsedS: 0 });
        }
        let coverage = 1;
        if (active.phase === 'fade-in') coverage = smoothstep01(active.phaseS / t.fadeInS);
        else if (active.phase === 'fade-out') coverage = 1 - smoothstep01(active.phaseS / t.fadeOutS);
        /* Progress through the pass by its nominal length, so the slab rises
         * from beneath the lens to its top as the camera descends through it. */
        const nominalS = t.fadeInS + Math.max(t.fullMinS, active.fullS) + t.fadeOutS;
        return Object.freeze({
            passId: active.pass.id,
            phase: active.phase,
            coverage,
            parallax: 1 - smootherstep01(active.elapsedS / nominalS),
            speedScale: speed,
            extended: active.phase === 'full' && active.phaseS > t.fullMinS && contentReady !== true,
            passElapsedS: active.elapsedS,
        });
    };

    /* The pace in clear sky: the gap pace between two passes, full speed
     * before the first and after the last (or once the rest are skipped). */
    let passesRun = 0;
    const clearPace = () => (passesRun > 0 && nextIndex < schedule.length ? t.gapSpeedScale : 1);

    const targetSpeed = (contentReady) => {
        if (!active) return clearPace();
        if (active.phase === 'fade-in') {
            return active.fromPace + (t.coverSpeedScale - active.fromPace) * smoothstep01(active.phaseS / t.fadeInS);
        }
        if (active.phase === 'full') {
            return active.phaseS >= t.fullMinS && contentReady !== true ? t.waitSpeedScale : t.coverSpeedScale;
        }
        return t.coverSpeedScale + (clearPace() - t.coverSpeedScale) * smoothstep01(active.phaseS / t.fadeOutS);
    };

    const api = {
        step({ dtS = 0, destinationM = NaN, descending = true, contentReady = true } = {}) {
            let dt = Math.max(0, finite(dtS, 0));
            totalS += dt;
            /* Phases consume the step in order, so one long frame cannot skip
             * a plateau's minimum or lose the overflow into the next phase. */
            while (active && dt > 0) {
                const spend = (limit) => {
                    const used = Math.min(dt, Math.max(0, limit - active.phaseS));
                    active.phaseS += used;
                    active.elapsedS += used;
                    dt -= used;
                    return active.phaseS >= limit;
                };
                if (active.phase === 'fade-in') {
                    if (!spend(t.fadeInS)) break;
                    active.phase = 'full';
                    active.phaseS = 0;
                } else if (active.phase === 'full') {
                    const limit = contentReady === true ? t.fullMinS : t.fullMaxS;
                    const done = active.phaseS >= limit || spend(limit);
                    active.fullS = active.phaseS;
                    if (!done) break;
                    active.extendedS = Math.max(0, active.fullS - t.fullMinS);
                    active.phase = 'fade-out';
                    active.phaseS = 0;
                } else {
                    if (!spend(t.fadeOutS)) break;
                    history.push(Object.freeze({ id: active.pass.id, startS: active.startS,
                        endS: totalS - dt, fullS: active.fullS, extendedS: active.extendedS ?? 0 }));
                    active = null;
                    clearS = 0;
                }
            }
            if (!active) clearS += dt;
            /* Start the next pass once the destination is inside its outer
             * edge on the way DOWN, with enough clear sky behind it. */
            if (!active && nextIndex < schedule.length && descending === true
                && Number.isFinite(destinationM) && clearS >= t.clearMinS) {
                const pass = schedule[nextIndex];
                if (destinationM <= pass.endM) {
                    history.push(Object.freeze({ id: pass.id, skipped: true, atM: destinationM }));
                    nextIndex += 1;
                } else if (destinationM <= pass.startM) {
                    active = { pass, phase: 'fade-in', phaseS: 0, elapsedS: 0, fullS: 0, startS: totalS,
                        fromPace: clearPace() };
                    nextIndex += 1;
                    passesRun += 1;
                }
            }
            const target = Math.max(MIN_SPEED_SCALE, Math.min(1, targetSpeed(contentReady)));
            const lag = 1 - Math.exp(-Math.max(0, finite(dtS, 0)) / t.speedEaseS);
            speed = Math.max(MIN_SPEED_SCALE, Math.min(1, speed + (target - speed) * lag));
            frame = compose(contentReady);
            return frame;
        },
        snapshot() {
            return frame ?? compose(true);
        },
        /* Seconds until another cover may begin: the rest of the pass in
         * force (assuming the tiles are ready) plus the clear-sky minimum. */
        clearReadyInS() {
            if (!active) return Math.max(0, t.clearMinS - clearS);
            let remaining = 0;
            if (active.phase === 'fade-in') remaining = (t.fadeInS - active.phaseS) + t.fullMinS + t.fadeOutS;
            else if (active.phase === 'full') remaining = Math.max(0, t.fullMinS - active.phaseS) + t.fadeOutS;
            else remaining = t.fadeOutS - active.phaseS;
            return Math.max(0, remaining) + t.clearMinS;
        },
        get active() { return Boolean(active); },
        get remainingPasses() { return schedule.length - nextIndex; },
        history() { return history.slice(); },
        timing: t,
        passes: Object.freeze(schedule.map((pass) => Object.freeze({ ...pass }))),
    };
    return Object.freeze(api);
}

/*
 * An even descent: log altitude falls at a constant rate between a short
 * ease out of the apex and a longer ease into the flight's end.
 *
 * Why the flight needs its own curve for the passes. The GTA 'settle' curve
 * spends its speed at the top: simulated against these passes (a 5.2 s
 * flight), it fell from 383 km to 6 km inside pass A's 1.07 s, so one cover
 * hid four decades and pass B, arriving below its own inner edge, was skipped. Two
 * passes 1.5 decades apart with 0.8 s of clear sky between them need the
 * decades to go by at an even rate. The velocity ramps are smoothstep, so the
 * acceleration is continuous at every join and the apex and the arrival are
 * both reached at rest.
 */
export const EVEN_DESCENT_RAMP_IN = 0.1;
export const EVEN_DESCENT_RAMP_OUT = 0.2;

export function evenLogDescent(fraction, { rampIn = EVEN_DESCENT_RAMP_IN, rampOut = EVEN_DESCENT_RAMP_OUT } = {}) {
    const d = clamp01(fraction);
    const a = Math.min(0.45, Math.max(0.01, finite(rampIn, EVEN_DESCENT_RAMP_IN)));
    /* rampOut may be zero: a flight that hands over to the approach ends at
     * its cruising rate, and the approach takes that rate up (see
     * planHeroApproach's startLogRate). */
    const b = Math.min(0.45, Math.max(0, finite(rampOut, EVEN_DESCENT_RAMP_OUT)));
    /* Integral of smoothstep from 0 to x. */
    const ramp = (x) => x * x * x - (x * x * x * x) / 2;
    const peak = 1 / (1 - (a + b) / 2);
    let area;
    if (d <= a) area = a * ramp(d / a);
    else if (d <= 1 - b || b === 0) area = a / 2 + (d - a);
    else area = a / 2 + (1 - a - b) + b * (0.5 - ramp((1 - d) / b));
    return Math.min(1, Math.max(0, area * peak));
}

/*
 * The turn onto the destination's bearing.
 *
 * While the camera crosses, heading and pitch are FIXED: the plan's traverse,
 * and what S8 measures (heading change <= 5 deg while both homes are more than
 * 50 km away). The rotation to the destination's registered heading and
 * pitch happens only below turnBelowM, and is complete by turnCompleteM so
 * the last stretch of the flight is already on the arrival ray. Log distance,
 * like every other rate in this project; smootherstep so the turn has no
 * corner at either end.
 */
export const TRAVERSE_PITCH_DEG = -70;
export const TURN_BELOW_M = 50_000;
export const TURN_COMPLETE_M = 2_000;

export function descentTurnBlend(destinationM, { turnBelowM = TURN_BELOW_M, turnCompleteM = TURN_COMPLETE_M } = {}) {
    const distance = finite(destinationM, NaN);
    const high = Math.max(2, finite(turnBelowM, TURN_BELOW_M));
    const low = Math.min(high / 1.01, Math.max(1, finite(turnCompleteM, TURN_COMPLETE_M)));
    if (!Number.isFinite(distance)) return 0;
    if (distance >= high) return 0;
    if (distance <= low) return 1;
    return smootherstep01((Math.log(high) - Math.log(distance)) / (Math.log(high) - Math.log(low)));
}

/*
 * The approach: from the end of the flight (about 1.2 km out) through the
 * destination's cloud band and down every still level to the handback range.
 *
 * Five knots in log range -- flight end, the band's outer edge (cloud
 * begins), the point where the band has thinned to half and the still shows
 * through (clearM), a quarter further in (the still on show), the handback --
 * joined by a monotone cubic (Fritsch-Carlson), so the camera only ever
 * moves inward and its velocity is continuous at every join. It STARTS at
 * the flight's own terminal rate (startLogRate), so the flight and the
 * approach are one glide rather than two moves meeting at rest, and it ends
 * at rest: the particle handoff wants a camera that has stopped.
 *
 * The durations carry the plan's numbers. The band from its outer edge to
 * clearM takes passCS, and the cover the S9 judge times (above 5 %) runs on
 * a little into the next segment, which puts pass C at 1.2-1.3 s against its
 * 1.0-2.0 s. showcaseS is the still on show beyond the 180 m S10 asks for:
 * the band's half point for a 300 m still is 241 m, and a quarter in from it
 * is 181 m, so the camera spends showcaseS where the still is opaque and the
 * cloud under half (0.27 s before this knot, which left about six of the
 * harness's 50 ms samples to find it). Each still level takes about
 * perLevelS; the lead-in at least leadInS -- more when a flight pass is still
 * clearing, so pass C never starts inside the 0.8 s of clear sky the judge
 * requires.
 */
export const HERO_APPROACH_TIMING = Object.freeze({
    leadInS: 0.6,
    passCS: 1.1,
    showcaseS: 0.4,
    perLevelS: 0.4,
    minLevelsS: 0.8,
});

/* How far in from the band's half point the still stays on show. */
export const STILL_SHOWCASE_RATIO = 0.75;

function monotoneCubic(xs, ys, endSlopes = [0, 0]) {
    const n = xs.length;
    const h = [];
    const delta = [];
    for (let i = 0; i < n - 1; i += 1) {
        h.push(xs[i + 1] - xs[i]);
        delta.push((ys[i + 1] - ys[i]) / h[i]);
    }
    const m = new Array(n).fill(0);
    m[0] = endSlopes[0];
    m[n - 1] = endSlopes[1];
    for (let i = 1; i < n - 1; i += 1) {
        if (delta[i - 1] === 0 || delta[i] === 0 || Math.sign(delta[i - 1]) !== Math.sign(delta[i])) {
            m[i] = 0;
        } else {
            /* Fritsch-Carlson weighted harmonic mean: never overshoots. */
            const w1 = 2 * h[i] + h[i - 1];
            const w2 = h[i] + 2 * h[i - 1];
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i]);
        }
    }
    return (x) => {
        if (x <= xs[0]) return ys[0];
        if (x >= xs[n - 1]) return ys[n - 1];
        let i = 0;
        while (i < n - 2 && x > xs[i + 1]) i += 1;
        const s = (x - xs[i]) / h[i];
        const s2 = s * s;
        const s3 = s2 * s;
        return (2 * s3 - 3 * s2 + 1) * ys[i] + (s3 - 2 * s2 + s) * h[i] * m[i]
            + (-2 * s3 + 3 * s2) * ys[i + 1] + (s3 - s2) * h[i] * m[i + 1];
    };
}

/**
 * The destination's cloud band for the approach, from the outermost still's
 * capture range: where the cloud begins on the way in (CLOUD_CLEAR times the
 * handoff range), where it has thinned to half and the still shows through
 * (clearM, the log midpoint of CLOUD_ENTER and CLOUD_FULL_START, where the
 * smootherstep fade is exactly 0.5) and where it is gone (CLOUD_ENTER). The
 * same band the wheel zoom draws, so pass C is the ordinary cloud, not a
 * second weather.
 */
export function heroApproachBand(outermostCaptureM) {
    const handoffM = cloudHandoffRangeFor(outermostCaptureM);
    if (!(handoffM > 0)) return null;
    return Object.freeze({
        handoffM,
        outerM: handoffM * CLOUD_CLEAR,
        clearM: handoffM * Math.sqrt(CLOUD_ENTER * CLOUD_FULL_START),
        innerM: handoffM * CLOUD_ENTER,
    });
}

/*
 * Where the flight hands over to the approach, for this destination.
 *
 * FLIGHT_END_M suits a still captured at about 300 m, whose band begins at
 * 670 m. A home whose outermost still was captured further out (beyond about
 * 530 m) has a band that begins above 1.2 km, and the band is only drawn once
 * the destination's levels are active -- at the anchor adoption, when the
 * flight ends -- so ending the flight inside it would pop the cloud in at
 * full strength. A quarter of clear air above the band's outer edge keeps
 * pass C a fade-in.
 */
export const FLIGHT_END_BAND_MARGIN = 1.25;

export function flightEndRangeFor(outermostCaptureM, flightEndM = FLIGHT_END_M) {
    const requested = finite(flightEndM, FLIGHT_END_M);
    const floor = requested > 0 ? requested : FLIGHT_END_M;
    const band = heroApproachBand(outermostCaptureM);
    return band ? Math.max(floor, band.outerM * FLIGHT_END_BAND_MARGIN) : floor;
}

/**
 * The approach as a pure function of time.
 *
 * bandOuterM / bandClearM / bandInnerM come from heroApproachBand. startLogRate
 * is how fast the flight was closing at the handover, in natural-log range per
 * second (positive inward); 0 starts from rest. Returns
 * { durationS, rangeAt(s), passC: { startS, endS } | null, startLogRate, knots }.
 */
export function planHeroApproach({
    startRangeM,
    endRangeM,
    bandOuterM = null,
    bandClearM = null,
    bandInnerM = null,
    levelCount = 1,
    timing = null,
    leadInMinS = 0,
    startLogRate = 0,
} = {}) {
    const source = timing && typeof timing === 'object' ? timing : {};
    const pick = (key) => Math.max(0, finite(source[key], HERO_APPROACH_TIMING[key]));
    const start = finite(startRangeM, NaN);
    const end = finite(endRangeM, NaN);
    if (!(start > 0) || !(end > 0)) throw new RangeError('approach ranges must be positive');
    if (!(end < start)) {
        return Object.freeze({ durationS: 0, rangeAt: () => end, passC: null, startLogRate: 0, knots: Object.freeze([]) });
    }
    /* Every segment takes some time, or two knots would share an instant. */
    const MIN_SEGMENT_S = 0.05;
    const levelsS = Math.max(MIN_SEGMENT_S, pick('minLevelsS'),
        pick('perLevelS') * Math.max(1, Math.floor(finite(levelCount, 1))));
    const knots = [{ s: 0, m: start }];
    let band = null;
    const outer = finite(bandOuterM, NaN);
    const inner = finite(bandInnerM, NaN);
    if (outer > inner && inner > end && inner < start) {
        /* The band's half point; without one, the same point derived from
         * the inner edge the way heroApproachBand places both. */
        const given = bandClearM === null || bandClearM === undefined
            ? inner * Math.sqrt(CLOUD_FULL_START / CLOUD_ENTER) : finite(bandClearM, NaN);
        const clear = given > inner && given < outer ? given : inner;
        const leadIn = Math.max(MIN_SEGMENT_S, pick('leadInS'), finite(leadInMinS, 0));
        if (outer < start * (1 - 1e-9)) knots.push({ s: leadIn, m: outer });
        const bandStartS = knots.at(-1).s;
        if (clear < start * (1 - 1e-9)) {
            /* A flight ending inside the band crosses only what is left of it. */
            const share = Math.log(Math.min(outer, start) / clear) / Math.log(outer / clear);
            const bandS = Math.max(MIN_SEGMENT_S, pick('passCS') * Math.max(0.25, Math.min(1, share)));
            knots.push({ s: bandStartS + bandS, m: clear });
        }
        const showcase = clear * STILL_SHOWCASE_RATIO;
        if (clear > inner && showcase > end * 1.05 && showcase < knots.at(-1).m) {
            knots.push({ s: knots.at(-1).s + Math.max(MIN_SEGMENT_S, pick('showcaseS')), m: showcase });
        }
        band = { startS: bandStartS, innerM: inner };
    } else if (finite(leadInMinS, 0) > 0) {
        /* No band to fly through: still wait out a clearing flight pass on the way. */
        knots.push({ s: Math.max(MIN_SEGMENT_S, finite(leadInMinS, 0)), m: Math.sqrt(start * Math.max(end, start / 4)) });
    }
    knots.push({ s: knots.at(-1).s + levelsS, m: end });
    const xs = knots.map((k) => k.s);
    const ys = knots.map((k) => Math.log(k.m));
    /* The flight's closing rate, never so fast that the first segment would
     * overshoot its knot (Fritsch-Carlson's bound is three times the secant). */
    const firstSecant = (ys[1] - ys[0]) / (xs[1] - xs[0]);
    const startSlope = Math.max(3 * firstSecant, -Math.max(0, finite(startLogRate, 0)));
    const curve = monotoneCubic(xs, ys, [startSlope, 0]);
    const durationS = knots.at(-1).s;
    const rangeAt = (s) => Math.exp(curve(Math.min(durationS, Math.max(0, finite(s, 0)))));
    let passC = null;
    if (band) {
        /* Where the camera leaves the band: bisect the monotone curve. */
        let lo = band.startS;
        let hi = durationS;
        for (let i = 0; i < 60; i += 1) {
            const mid = (lo + hi) / 2;
            if (rangeAt(mid) > band.innerM) lo = mid; else hi = mid;
        }
        passC = Object.freeze({ startS: band.startS, endS: hi });
    }
    return Object.freeze({
        durationS,
        rangeAt,
        passC,
        startLogRate: -startSlope,
        knots: Object.freeze(knots.map((k) => Object.freeze({ ...k }))),
    });
}

/*
 * The arrival ray, joined gradually.
 *
 * The flight's ground track ends under its final pose, so its last stretch
 * was a vertical drop onto that point while the pitch turned: the home rose
 * from the bottom of the frame, and the camera moved at right angles to the
 * ray the approach then glides along. Ending the flight moving would have put
 * a corner into the motion there. Instead, on the way down, the camera's
 * bearing from the destination swings onto the arrival ray by the same blend
 * that turns its heading and pitch (descentTurnBlend), keeping its distance:
 * the trace's distance to go is unchanged, the turn and the swing finish
 * together, and from turnCompleteM in the camera is ON the ray, looking
 * straight at the home -- so the flight's last frames and the approach's
 * first move in the same direction at the same rate.
 *
 * offset: camera minus target; rayUnit: unit vector from the target along
 * the ray. Returns the joined offset, the same length as offset.
 */
export function joinArrivalRay(offset, rayUnit, blend) {
    const w = clamp01(blend);
    const length = Math.hypot(offset[0], offset[1], offset[2]);
    if (!(length > 0) || w <= 0) return [offset[0], offset[1], offset[2]];
    const from = [offset[0] / length, offset[1] / length, offset[2] / length];
    const mixed = from.map((value, i) => value + (rayUnit[i] - value) * w);
    const size = Math.hypot(mixed[0], mixed[1], mixed[2]);
    /* Opposite directions have no midpoint to swing through; take the ray. */
    const unit = size > 1e-9 ? mixed.map((value) => value / size) : [rayUnit[0], rayUnit[1], rayUnit[2]];
    return unit.map((value) => value * length);
}

/*
 * Whether a flight that has reached its end may be adopted.
 *
 * Elapsed time still cannot authorise a destination that rendered nothing:
 * with live tiles required, a doorstep that is not ready AND drew nothing
 * blocks the journey with a recovery (the caller names a faulted provider as
 * the reason). A doorstep that drew tiles lands and refines behind the
 * approach.
 */
export function flightArrivalBlocked({ liveRequired = false, ready = false, frameEmpty = false } = {}) {
    return liveRequired === true && ready !== true && frameEmpty === true;
}

/**
 * The nominal planet-to-interior timeline of a journey preset, in seconds:
 * the flight at speed 1, the time the passes and the gap pace add by slowing
 * it, the approach, the host's wait between the approach and the particles
 * (hostS, which the caller measures or assumes) and the particle handoff. The
 * preset exposes the parts; this adds them up so the "about eleven seconds"
 * is a number a test can hold.
 */
export function estimateJourneyTimeline(preset = {}, { levelCount = 3, handoffS = 1.0, hostS = 0 } = {}) {
    const flightS = Math.max(0, finite(preset?.durationS, 0));
    const timing = normalizePassTiming(preset?.passTiming);
    const passes = normalizePasses(preset?.passes ?? DESCENT_CLOUD_PASSES);
    const envelope = passEnvelopeS(timing);
    /* Under a plateau the flight clock runs at coverSpeedScale, and roughly
     * halfway there on the fades; that lost flight time is added back. */
    const slowS = timing.fullMinS * (1 - timing.coverSpeedScale)
        + (timing.fadeInS + timing.fadeOutS) * (1 - timing.coverSpeedScale) / 2;
    /* Between two passes the clear-sky minimum runs at the gap pace. */
    const gapS = Math.max(0, passes.length - 1) * timing.clearMinS * (1 - timing.gapSpeedScale);
    const approach = preset?.approach ?? {};
    const levelsS = Math.max(finite(approach.minLevelsS, HERO_APPROACH_TIMING.minLevelsS),
        finite(approach.perLevelS, HERO_APPROACH_TIMING.perLevelS) * Math.max(1, levelCount));
    const approachS = finite(approach.leadInS, HERO_APPROACH_TIMING.leadInS)
        + finite(approach.passCS, HERO_APPROACH_TIMING.passCS)
        + finite(approach.showcaseS, HERO_APPROACH_TIMING.showcaseS) + levelsS;
    const passesS = passes.length * slowS + gapS;
    const hostWaitS = Math.max(0, finite(hostS, 0));
    return Object.freeze({
        flightS: flightS + passesS,
        passS: envelope.minS,
        approachS,
        hostS: hostWaitS,
        handoffS,
        totalS: flightS + passesS + approachS + hostWaitS + handoffS,
    });
}
