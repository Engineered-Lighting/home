import { PERFORMANCE_BUDGETS, PERFORMANCE_MODES } from './adaptive-performance.mjs';
import {
    DESCENT_CLOUD_PASSES,
    DESCENT_PASS_TIMING,
    EVEN_DESCENT_RAMP_IN,
    EVEN_DESCENT_RAMP_OUT,
    FLIGHT_END_M,
    HERO_APPROACH_TIMING,
    TRAVERSE_PITCH_DEG,
    TURN_BELOW_M,
    TURN_COMPLETE_M,
} from './descent-cloud-plan.mjs';

/*
 * world-view-options.mjs — the alternatives the world view can be built from.
 *
 * This exists because several things about how the world view should FEEL are
 * genuinely undecided, and deciding them by argument is how you get a defensible
 * answer nobody likes. Each group below is a set of real alternatives, and the
 * discovery panel switches between them live so they can be compared instead of
 * described.
 *
 * Every preset carries the actual numbers the renderer consumes. That is
 * deliberate: a preset that is only a label, with the real values living in the
 * renderer behind a switch, is how an option ends up changing nothing. This
 * module is the single source of truth, it is pure, and it is asserted.
 *
 * Nothing here is a slider on one underlying thing. "snappy" and "heavy" are not
 * two points on a continuum with the same character; they use different impulse
 * and coast constants and feel different in kind.
 */

/* ------------------------------------------------------------------ clouds */

/*
 * Cloud presets.
 *
 * `renderer` is the decisive field. "volumetric" means the raymarched
 * post-process; "impostor" means the cheap fallback, and it is labelled cheap in
 * the UI rather than presented as an equal. Billboards are NOT an option: a
 * camera-facing sprite has no volume, cannot self-shadow, and reads as a
 * sticker, which is settled and not worth re-litigating through a toggle.
 *
 * Coverage and density are the two controls that actually change the sky's
 * character. Coverage grows the regions that have any cloud at all, which is how
 * a sky goes from scattered to overcast; density changes how solid what is there
 * looks. Moving only one of them produces either thin overcast or dense specks.
 */
export const CLOUD_PRESETS = Object.freeze({
    off: Object.freeze({
        id: 'off',
        label: 'Off',
        note: 'No atmosphere pass at all. The baseline every other option is judged against.',
        renderer: 'none',
    }),
    scattered: Object.freeze({
        id: 'scattered',
        label: 'Scattered',
        note: 'Fair-weather cumulus with a high sun. Mostly open sky; the city is never hidden.',
        renderer: 'volumetric',
        coverage: 0.58,
        density: 0.85,
        baseM: 2_200,
        topM: 4_000,
        /* Metres across one cloud mass. Large, so the sky reads as a few big
         * amorphous groupings rather than a field of similar lumps. */
        featureM: 7_600,
        /* Domain-warp displacement, in cells. This is what makes each mass a
         * different shape instead of a rotation of its neighbour. */
        warp: 1.15,
        /* Opens the middle of the frame so the home stays visible during the
         * descent. Not physical; see the shader note. */
        edgeBias: 0.55,
        sunElevationDeg: 62,
        sunAzimuthDeg: 225,
        windSpeedMps: 4,
        windDirectionFromDeg: 250,
        relativeHumidity01: 0.56,
    }),
    brokenDeck: Object.freeze({
        id: 'brokenDeck',
        label: 'Broken deck',
        note: 'The aerial-photograph reference: dense banks, hard shadows on the city, gaps you see through.',
        renderer: 'volumetric',
        coverage: 0.72,
        density: 1.25,
        baseM: 1_900,
        topM: 4_300,
        featureM: 9_400,
        warp: 1.45,
        edgeBias: 0.62,
        sunElevationDeg: 38,
        sunAzimuthDeg: 220,
        windSpeedMps: 9,
        windDirectionFromDeg: 235,
        relativeHumidity01: 0.74,
    }),
    overcast: Object.freeze({
        id: 'overcast',
        label: 'Overcast ceiling',
        note: 'A closed layer you break through on the way out and drop back under on the way in.',
        renderer: 'volumetric',
        coverage: 0.86,
        density: 1.9,
        baseM: 1_700,
        topM: 3_900,
        featureM: 12_000,
        warp: 1.0,
        /* A closed ceiling that opened in the middle would stop being a
         * ceiling, so this one barely frames at all. */
        edgeBias: 0.22,
        sunElevationDeg: 26,
        sunAzimuthDeg: 205,
        windSpeedMps: 14,
        windDirectionFromDeg: 215,
        relativeHumidity01: 0.88,
    }),
    impostor: Object.freeze({
        id: 'impostor',
        label: 'Impostor (cheap)',
        note: 'A fast animated shell: two soft, sun-lit field samples instead of a volumetric raymarch.',
        renderer: 'impostor',
        coverage: 0.56,
        density: 0.92,
        baseM: 2_000,
        topM: 4_100,
        featureM: 10_500,
        warp: 0.0,
        edgeBias: 0.32,
        /* These are consumed only by the single-shell shader.  Keeping them in
         * the preset makes the cheaper visual treatment as tunable and honest
         * as the volumetric alternatives. */
        impostorSoftness: 0.17,
        impostorLightOffset: 0.16,
        impostorOpacity: 0.78,
        sunElevationDeg: 45,
        sunAzimuthDeg: 225,
        windSpeedMps: 6,
        windDirectionFromDeg: 245,
        relativeHumidity01: 0.65,
    }),
});

/* --------------------------------------------------------------- zoom feel */

/*
 * Zoom presets.
 *
 * The model is momentum, not target-chasing: a notch injects velocity into
 * log-range and the velocity decays exponentially. That is what gives instant
 * onset and a glide that outlives the input; a critically damped chase eases in
 * as well as out and can never feel immediate however it is tuned.
 *
 * `impulsePerFraction` is how hard a notch pushes. `coastTimeS` is how long it
 * keeps going. They are independent, which is why these are different characters
 * rather than three speeds: "snappy" is a hard push that stops promptly,
 * "heavy" is a softer push that carries a long way.
 */
export const ZOOM_PRESETS = Object.freeze({
    snappy: Object.freeze({
        id: 'snappy',
        label: 'Snappy',
        note: 'Hard onset, short coast. Lands where you point it.',
        impulsePerFraction: 7.5,
        coastTimeS: 0.42,
        maxVelocity: 5.0,
        wheelDivisor: 900,
        maxStep: 0.2,
    }),
    glide: Object.freeze({
        id: 'glide',
        label: 'Glide',
        note: 'Immediate, then a long easing coast. The current model.',
        impulsePerFraction: 5.5,
        coastTimeS: 0.78,
        maxVelocity: 4.2,
        wheelDivisor: 1_050,
        maxStep: 0.17,
    }),
    heavy: Object.freeze({
        id: 'heavy',
        label: 'Heavy',
        note: 'Softer push, long carry. Reads as mass; overshoots if you are impatient.',
        impulsePerFraction: 3.9,
        coastTimeS: 1.35,
        maxVelocity: 3.4,
        wheelDivisor: 1_250,
        maxStep: 0.13,
    }),
});

/* ---------------------------------------------------------------- journey */

/*
 * How the inter-home flight is paced and how high it goes.
 *
 * `apexM` is not a detail. Too low and the trip is a pan across a continent with
 * no sense of leaving; too high and the planet is a marble and the destination
 * arrives from nowhere. These are the three readings worth comparing.
 *
 * `throughClouds` is the whole planet-to-interior timeline of plan B2 (S8-S10)
 * as one tunable preset, and the default. Its numbers, in the order a viewer
 * meets them:
 *   durationS / ascentShare / traverseShare   the flight's own clock at full
 *       speed: a short climb, a level traverse (heading fixed, pitch
 *       traversePitchDeg) and an even descent (descentCurve 'even')
 *   passes / passTiming   the two descent cloud passes (descent-cloud-plan.mjs):
 *       each 1.2-1.65 s, 0.8 s of clear sky between; they slow the flight to
 *       half speed under cover (a quarter while waiting for tiles), and the
 *       clear sky between them runs at gapSpeedScale so pass B begins near
 *       its 18 km edge; together about 1 s
 *   turnBelowM / turnCompleteM   where the heading and pitch turn onto the
 *       destination's framing
 *   flightEndM   where the flight hands over to the approach
 *   approach   the lead-in, the destination's cloud band (pass C), the still
 *       on show beyond 180 m, and about 0.4 s per still level down to the
 *       handback
 * then the 1 s particle handoff. About eleven seconds in all; the descent-
 * cloud-plan tests hold the sum (estimateJourneyTimeline) and simulate the S9
 * judge against it, lateral crossing included.
 *
 * The other presets keep their old tempo and have NO descent passes
 * (`passes: []`). Their whole descent lasts 0.7-1.7 s, and two passes need
 * at least three seconds of it (1.1 s each and 0.8 s of clear sky between):
 * simulated with the passes, each drew one cover from about 130 km to the
 * flight's end and never started pass B, so one cover hid two decades of the
 * descent. They share the level traverse and the approach through the still.
 */
export const JOURNEY_PRESETS = Object.freeze({
    throughClouds: Object.freeze({
        id: 'throughClouds',
        label: 'Through clouds (~11s)',
        note: 'Planet to interior: a level traverse, two cloud passes on the way down, then the destination\'s own cloud and every level of its still before the particles.',
        /* 5.6 with the gap pace, not 6.4 without it: a little less planet-
         * to-handback time (10.1-10.8 s simulated with the crossing, against
         * 11.0-11.7), with more of it spent between the passes. */
        durationS: 5.6,
        ascentShare: 0.1,
        traverseShare: 0.12,
        /* The GTA settle curve spends its speed at the top, which put four
         * decades inside pass A and skipped pass B; see evenLogDescent. */
        descentCurve: 'even',
        descentRampIn: EVEN_DESCENT_RAMP_IN,
        descentRampOut: EVEN_DESCENT_RAMP_OUT,
        traversePitchDeg: TRAVERSE_PITCH_DEG,
        turnBelowM: TURN_BELOW_M,
        turnCompleteM: TURN_COMPLETE_M,
        flightEndM: FLIGHT_END_M,
        passes: DESCENT_CLOUD_PASSES,
        passTiming: Object.freeze({
            fadeInS: DESCENT_PASS_TIMING.fadeInS,
            fullMinS: DESCENT_PASS_TIMING.fullMinS,
            fadeOutS: DESCENT_PASS_TIMING.fadeOutS,
            clearMinS: DESCENT_PASS_TIMING.clearMinS,
            coverSpeedScale: DESCENT_PASS_TIMING.coverSpeedScale,
            waitSpeedScale: DESCENT_PASS_TIMING.waitSpeedScale,
            gapSpeedScale: DESCENT_PASS_TIMING.gapSpeedScale,
        }),
        approach: HERO_APPROACH_TIMING,
    }),
    swift: Object.freeze({
        id: 'swift',
        label: 'Swift (2.0s)',
        note: 'Barely time to register the planet. Reads as a cut with a gesture attached. No descent cloud passes.',
        durationS: 2.0,
        ascentShare: 0.30,
        traverseShare: 0.34,
        passes: Object.freeze([]),
    }),
    balanced: Object.freeze({
        id: 'balanced',
        label: 'Balanced (2.9s)',
        note: 'Long enough to read the globe, short enough not to wait. No descent cloud passes.',
        durationS: 2.9,
        ascentShare: 0.30,
        traverseShare: 0.36,
        passes: Object.freeze([]),
    }),
    grand: Object.freeze({
        id: 'grand',
        label: 'Grand (4.0s)',
        note: 'A journey rather than a transition. Risks feeling slow on repeat use. No descent cloud passes.',
        durationS: 4.0,
        ascentShare: 0.28,
        traverseShare: 0.42,
        passes: Object.freeze([]),
    }),
});

/* -------------------------------------------------------- journey pacing */

/*
 * Duration decides how long the trip takes; pacing decides where that time is
 * spent. Keeping these separate is important for the tuning tray: a slower
 * symmetric curve is not the same thing as the GTA-style fast orbital drop
 * followed by a controlled street-level settle.
 *
 * `climbPower` reshapes the minimum-jerk climb without changing any camera
 * pose. `settlePower` selects a C2 ease-out descent whose velocity is greatest
 * just after the apex and falls continuously toward the destination.
 */
export const JOURNEY_PACING_PRESETS = Object.freeze({
    minimumJerk: Object.freeze({
        id: 'minimumJerk',
        label: 'Even curve',
        note: 'Symmetric minimum-jerk rise and fall. The neutral motion baseline.',
        climbPower: 1,
        descentCurve: 'minimum-jerk',
        settlePower: 1,
        joinFraction: 0.1,
    }),
    gta: Object.freeze({
        id: 'gta',
        label: 'GTA drop',
        note: 'Builds speed leaving home, drops quickly from orbit, then decelerates all the way to the roof.',
        climbPower: 1.8,
        descentCurve: 'settle',
        settlePower: 2.6,
        joinFraction: 0.1,
    }),
    longSettle: Object.freeze({
        id: 'longSettle',
        label: 'Long settle',
        note: 'A gentler departure with more of the descent reserved for reading the city and final approach.',
        climbPower: 1.35,
        descentCurve: 'settle',
        settlePower: 3.4,
        joinFraction: 0.14,
    }),
});

export const APEX_PRESETS = Object.freeze({
    lowPass: Object.freeze({
        id: 'lowPass',
        label: 'Low pass',
        note: 'Continental. You see the coastline, not the curve.',
        apexM: 1_200_000,
    }),
    regional: Object.freeze({
        id: 'regional',
        /*
         * The height at which the journey is legible AS a journey.
         *
         * The crossing peaks at the apex by design, and how much of it you can
         * see is pure geometry: at nadir the frame is about 1.155 times the
         * altitude wide, so the 1,750 km between the two homes spans 0.17 of
         * the frame at orbital and 0.58 here. That is the difference between a
         * dot creeping across a still planet and watching yourself cross from
         * one place to the other.
         *
         * It is not a trade of the planet for legibility, which is how it first
         * got described. The visible cap is acos(R / (R + h)): 65.5 degrees at
         * orbital and 44.7 here, so this still shows the whole west coast out
         * into the Pacific with the curve plainly there. A smaller planet, not
         * an absent one.
         *
         * Not the default, because which of those two moments matters more is
         * a matter of taste and the brief asks for the planet.
         */
        label: 'Regional',
        note: 'Both homes in frame at once, and the crossing actually reads.',
        apexM: 2_600_000,
    }),
    orbital: Object.freeze({
        id: 'orbital',
        label: 'Orbital',
        note: 'The whole planet, edge to edge, with the limb visible.',
        apexM: 9_000_000,
    }),
    far: Object.freeze({
        id: 'far',
        label: 'Far orbital',
        note: 'A marble. Dramatic once; the destination arrives from nowhere.',
        apexM: 24_000_000,
    }),
});

/*
 * Easing.
 *
 * The ascent and descent are eased separately so the apex is the fastest point.
 * A symmetric in-out on each leg stalls at the top, which reads as a pause in
 * the middle of a single gesture.
 */
/*
 * Easing was a panel group here, and it is gone.
 *
 * It offered cubic, quintic and quadratic pairs, and it had never done
 * anything: flyToSite read the preset into easeIn and easeOut and then never
 * used either, because the journey stopped being three chained Cesium tweens
 * and became one parametric curve. The variables stayed behind.
 *
 * It was not re-wired, because every option it offered would have been worse
 * than what replaced it. Those families do not have zero slope at their ends --
 * t^3 arrives with a slope of 3 -- so joining an ascent to a descent at the
 * apex introduces a step change in velocity, a kick at the top of the arc. The
 * curve uses smootherstep, 6t^5 - 15t^4 + 10t^3, whose first AND second
 * derivatives vanish at both ends; that is exactly why the climb and descent
 * can now meet with no plateau between them and no corner. A control whose
 * every setting degrades the thing it claims to tune is worse than no control.
 */

/* ---------------------------------------------------------------- handoff */

/*
 * The Apartment-to-world collapse.
 *
 * `timeline` names the authored shape in handoff-timeline.mjs. The legacy
 * shape is the prototype's proportions, so its three tempos change speed
 * without changing what was visually approved; `particles` is its own shape,
 * with a brief firewall and a long particle window.
 */
export const HANDOFF_PRESETS = Object.freeze({
    particles: Object.freeze({
        id: 'particles',
        label: 'Particles (1s)',
        note: 'The default. Black for a blink, then the still arrives as particles for over half a second.',
        timeline: 'particles',
        durationMs: 1_000,
        particleCount: 24_000,
        streakFraction: 0.03,
    }),
    quick: Object.freeze({
        id: 'quick',
        label: 'Quick (300ms)',
        note: 'Almost a cut. The particles barely register.',
        timeline: 'legacy',
        durationMs: 300,
        particleCount: 18_000,
        streakFraction: 0.075,
    }),
    standard: Object.freeze({
        id: 'standard',
        label: 'Standard (420ms)',
        note: 'The previous default, kept for comparison. Mostly black; the field is a blink.',
        timeline: 'legacy',
        durationMs: 420,
        particleCount: 24_000,
        streakFraction: 0.055,
    }),
    cinematic: Object.freeze({
        id: 'cinematic',
        label: 'Cinematic (650ms)',
        note: 'The prototype tempo. You watch the particles travel.',
        timeline: 'legacy',
        durationMs: 650,
        particleCount: 12_000,
        streakFraction: 0.03,
    }),
});

/* --------------------------------------------------------- tile readiness */

/*
 * When a camera reaches a never-before-seen parcel, `tilesLoaded` commonly
 * blinks true while traversal is still discovering children. These presets
 * decide how long a descent cloud pass may hold its opaque plateau for several
 * genuinely quiet frames (`passWaitS`, on top of the pass's own minimum), and
 * whether a short optical cover may mask refinement after arrival. They never
 * change the provider's detail target, so choosing one cannot itself trigger a
 * coarse/sharp tile swap.
 *
 * CHANGED ON PURPOSE (round 2): these used to move the arrival hold (holdAt,
 * where on the descent the camera parked to wait). The hold is gone -- the
 * camera never parks -- so the wait they tune is now the one spent inside a
 * pass. The ids stay because they are persisted in URLs and localStorage.
 */
export const STREAMING_PRESETS = Object.freeze({
    strict: Object.freeze({
        id: 'strict',
        label: 'Preload detail',
        note: 'A cloud pass holds up to 0.45 s longer for four rendered quiet frames over 250 ms; a destination that drew nothing still ends in retry or return.',
        passWaitS: 0.45,
        requiredQuietFrames: 4,
        minimumQuietMs: 250,
        holdBudgetMs: 4_000,
        recoveryBudgetMs: 4_000,
        perceptualCover: true,
    }),
    quick: Object.freeze({
        id: 'quick',
        label: 'Brief wait',
        note: 'A cloud pass holds at most 0.25 s longer for quiet tiles; the same readiness and recovery requirements.',
        passWaitS: 0.25,
        requiredQuietFrames: 4,
        minimumQuietMs: 250,
        holdBudgetMs: 4_000,
        recoveryBudgetMs: 4_000,
        perceptualCover: true,
    }),
    immediate: Object.freeze({
        id: 'immediate',
        label: 'No wait',
        note: 'Cloud passes clear on their own clock; tiles resolve after them. Never bypasses the empty-destination recovery.',
        passWaitS: 0,
        requiredQuietFrames: 4,
        minimumQuietMs: 250,
        holdBudgetMs: 4_000,
        recoveryBudgetMs: 4_000,
        perceptualCover: true,
    }),
});

/* ---------------------------------------------------------------- quality */

/*
 * The render budget.
 *
 * Numbers taken from published implementations rather than invented: a few
 * hundred primary steps with a step that grows through empty space, one to three
 * sun steps, and aggressive early-out on transmittance. Measured spreads on real
 * hardware are enormous -- the same shader runs 0.5-2ms on a GTX 1050 and
 * 15-50ms on a low-end Chromebook -- so Adaptive exists and is the default.
 *
 * The ids remain `auto`, `high`, and `low` because they are persisted in URLs
 * and localStorage. Their labels state the real behavior. Every renderer value
 * comes from the shared budget ladder; the option is therefore a mode switch,
 * not a label sitting beside a separate collection of magic numbers.
 * The DPR caps bound high-DPI fill cost without scaling a 1x display below one
 * backing pixel per CSS pixel or a higher-density display below 1.5x.
 */
function makeQualityPreset({ id, label, note, mode, level, minTransmittance }) {
    const behavior = PERFORMANCE_MODES[mode];
    const budget = PERFORMANCE_BUDGETS[level];
    return Object.freeze({
        id,
        label,
        note,
        performanceMode: behavior.id,
        adaptive: behavior.adaptive,
        budgetLevel: budget.id,
        maxSteps: budget.cloudMaxSteps,
        sunSteps: budget.cloudSunSteps,
        worldDprCap: budget.worldDprCap,
        apartmentDprCap: budget.apartmentDprCap,
        particleCount: budget.handoffParticleCount,
        particleMultiplier: budget.handoffParticleMultiplier,
        minTransmittance,
    });
}

export const QUALITY_PRESETS = Object.freeze({
    auto: makeQualityPreset({
        id: 'auto',
        label: 'Adaptive',
        note: 'Measures active frames and adapts cloud work, particle density, and the effective-DPR cap while preserving one backing pixel per CSS pixel and at least 1.5x on higher-density displays.',
        mode: 'adaptive',
        level: 'high',
        minTransmittance: 0.05,
    }),
    high: makeQualityPreset({
        id: 'high',
        label: 'Max fidelity',
        note: 'Pins the highest configured rendering and tile budgets, including cloud, particle, and effective-DPR caps, without adapting to missed frames.',
        mode: 'maxFidelity',
        level: 'maximum',
        minTransmittance: 0.02,
    }),
    low: makeQualityPreset({
        id: 'low',
        label: 'Performance / battery',
        note: 'Pins lighter cloud and particle work and a lower effective-DPR cap for cooler rendering while keeping one backing pixel per CSS pixel and at least 1.5x on higher-density displays.',
        mode: 'performanceBattery',
        level: 'minimum',
        minTransmittance: 0.1,
    }),
});

/* --------------------------------------------------------------- assembly */

export const OPTION_GROUPS = Object.freeze([
    Object.freeze({ key: 'clouds', label: 'Clouds', presets: CLOUD_PRESETS, fallback: 'off' }),
    Object.freeze({ key: 'zoom', label: 'Zoom feel', presets: ZOOM_PRESETS, fallback: 'glide' }),
    Object.freeze({ key: 'journey', label: 'Journey', presets: JOURNEY_PRESETS, fallback: 'throughClouds' }),
    Object.freeze({ key: 'pacing', label: 'Flight pacing', presets: JOURNEY_PACING_PRESETS, fallback: 'gta' }),
    Object.freeze({ key: 'apex', label: 'Apex', presets: APEX_PRESETS, fallback: 'orbital' }),
    Object.freeze({ key: 'handoff', label: 'Handoff', presets: HANDOFF_PRESETS, fallback: 'particles' }),
    Object.freeze({ key: 'streaming', label: 'Tile arrival', presets: STREAMING_PRESETS, fallback: 'strict' }),
    Object.freeze({ key: 'quality', label: 'Quality', presets: QUALITY_PRESETS, fallback: 'auto' }),
]);

/*
 * The shipped defaults.
 *
 * Clouds default OFF. The panel is for discovery, and an unreviewed atmosphere
 * pass should not be the first thing anyone sees; it is opted into, not out of.
 */
export const DEFAULT_SELECTION = Object.freeze(
    Object.fromEntries(OPTION_GROUPS.map((group) => [group.key, group.fallback])),
);

/**
 * Coerce a stored or URL-supplied selection into something the renderer can use.
 *
 * Unknown ids fall back rather than throwing: this value comes from
 * localStorage and from query strings, both of which outlive the code that
 * wrote them. A preset removed in a later build must not strand the viewport.
 */
export function normalizeSelection(raw) {
    const source = raw && typeof raw === 'object' ? raw : {};
    const selection = {};
    for (const group of OPTION_GROUPS) {
        const candidate = source[group.key];
        selection[group.key] = Object.prototype.hasOwnProperty.call(group.presets, candidate)
            ? candidate
            : group.fallback;
    }
    return Object.freeze(selection);
}

/** The resolved preset objects for a selection, ready for the renderer. */
export function resolveSelection(raw) {
    const selection = normalizeSelection(raw);
    const resolved = {};
    for (const group of OPTION_GROUPS) {
        resolved[group.key] = group.presets[selection[group.key]];
    }
    return Object.freeze({ selection, ...resolved });
}

/**
 * A stable identity for a resolved selection.
 *
 * The renderer rebuilds only what changed, and comparing option objects by
 * reference is not enough once a selection round-trips through storage. This is
 * what lets a switch be applied live rather than by reload.
 */
export function selectionKey(raw) {
    const selection = normalizeSelection(raw);
    return OPTION_GROUPS.map((group) => `${group.key}:${selection[group.key]}`).join('|');
}
