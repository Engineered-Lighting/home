/*
 * adaptive-performance.mjs -- renderer-independent viewport budget governor.
 *
 * The Apartment renderer, Cesium world, cloud pass, and handoff compositor all
 * spend from one frame budget. Letting each degrade itself independently makes
 * quality seesaw: one surface recovers while another falls back, then they swap
 * on the next sample. This module owns the shared ladder and nothing renderer-
 * specific. Callers apply the returned numbers to their own APIs.
 *
 * Time is injected through `clock`. Tests can therefore drive the exact same
 * state machine without sleeping, and production can pass performance.now.
 * The controller accepts only timing numbers and activity flags. In particular,
 * it has no metadata bag in which a site, address, coordinate, or viewport can
 * accidentally end up in diagnostics.
 */

const freeze = (value) => Object.freeze(value);

export const PERFORMANCE_MODES = freeze({
    adaptive: freeze({
        id: 'adaptive',
        label: 'Adaptive',
        adaptive: true,
    }),
    maxFidelity: freeze({
        id: 'maxFidelity',
        label: 'Max fidelity',
        adaptive: false,
        fixedLevel: 'maximum',
    }),
    performanceBattery: freeze({
        id: 'performanceBattery',
        label: 'Performance / battery',
        adaptive: false,
        fixedLevel: 'minimum',
    }),
});

/*
 * Highest fidelity first. Adaptive operation deliberately tops out at `high`;
 * `maximum` is an explicit, fixed experiment because it spends every budget at
 * once and should never be selected merely because a quiet scene had headroom.
 *
 * `apartmentDprCap` and `worldDprCap` are upper bounds on each renderer's
 * effective device-pixel ratio, not multipliers applied to the whole frame.
 * That distinction keeps a 1x display at one backing pixel per CSS pixel at
 * every rung and preserves at least 1.5x on higher-density displays while still
 * bounding the fill cost of 2x-4x mobile displays. Cloud march and lighting work
 * remain independently scalable, but the current one-pass cloud compositor
 * stays at full resolution because reducing it also reduces the map beneath it.
 * Particle count is absolute as well as relative, so the compositor need not
 * know the author's reference count to apply a decision.
 */
export const PERFORMANCE_BUDGETS = freeze({
    maximum: freeze({
        id: 'maximum',
        apartmentDprCap: 2.0,
        worldDprCap: 2.0,
        cloudMaxSteps: 320,
        cloudSunSteps: 4,
        handoffParticleCount: 24_000,
        handoffParticleMultiplier: 1.0,
    }),
    high: freeze({
        id: 'high',
        apartmentDprCap: 1.75,
        worldDprCap: 2.0,
        cloudMaxSteps: 208,
        cloudSunSteps: 4,
        handoffParticleCount: 22_000,
        handoffParticleMultiplier: 0.92,
    }),
    balanced: freeze({
        id: 'balanced',
        apartmentDprCap: 1.5,
        worldDprCap: 1.85,
        cloudMaxSteps: 144,
        cloudSunSteps: 3,
        handoffParticleCount: 18_000,
        handoffParticleMultiplier: 0.75,
    }),
    low: freeze({
        id: 'low',
        apartmentDprCap: 1.5,
        worldDprCap: 1.65,
        cloudMaxSteps: 88,
        cloudSunSteps: 2,
        handoffParticleCount: 12_000,
        handoffParticleMultiplier: 0.5,
    }),
    minimum: freeze({
        id: 'minimum',
        apartmentDprCap: 1.5,
        worldDprCap: 1.5,
        cloudMaxSteps: 56,
        cloudSunSteps: 1,
        handoffParticleCount: 8_000,
        handoffParticleMultiplier: 1 / 3,
    }),
});

export const PERFORMANCE_LEVEL_ORDER = freeze([
    'maximum',
    'high',
    'balanced',
    'low',
    'minimum',
]);

/*
 * Mobile has two explicit targets. A capable phone can be asked to hold 60 Hz;
 * a constrained or battery-conscious phone can be told honestly to hold 30 Hz.
 * The governor does not infer device class from a user agent or silently lower
 * the target. Runtime policy makes that decision and records only the tier id.
 */
export const PERFORMANCE_TARGETS = freeze({
    desktop: freeze({
        id: 'desktop',
        deviceClass: 'desktop',
        targetFps: 60,
        frameBudgetMs: 1000 / 60,
        degradeP95Ms: 20,
        recoverP95Ms: 14.5,
        rollingSamples: 120,
        minimumSamples: 45,
        assessmentIntervalMs: 1_000,
        badAssessmentsToDegrade: 2,
        goodAssessmentsToRecover: 8,
        recoveryHoldMs: 15_000,
        recoveryCooldownMs: 12_000,
        degradeCooldownMs: 2_500,
        longFrameMs: 50,
        catastrophicFrameMs: 100,
        longFrameScoreToDegrade: 2,
        maximumSampleGapMs: 750,
        adaptiveStart: 'high',
        adaptiveBest: 'high',
        adaptiveWorst: 'minimum',
    }),
    mobile60: freeze({
        id: 'mobile60',
        deviceClass: 'mobile',
        targetFps: 60,
        frameBudgetMs: 1000 / 60,
        degradeP95Ms: 21,
        recoverP95Ms: 15,
        rollingSamples: 90,
        minimumSamples: 30,
        assessmentIntervalMs: 1_000,
        badAssessmentsToDegrade: 2,
        goodAssessmentsToRecover: 8,
        recoveryHoldMs: 18_000,
        recoveryCooldownMs: 15_000,
        degradeCooldownMs: 2_500,
        longFrameMs: 50,
        catastrophicFrameMs: 100,
        longFrameScoreToDegrade: 2,
        maximumSampleGapMs: 750,
        adaptiveStart: 'balanced',
        adaptiveBest: 'high',
        adaptiveWorst: 'minimum',
    }),
    mobile30: freeze({
        id: 'mobile30',
        deviceClass: 'mobile',
        targetFps: 30,
        frameBudgetMs: 1000 / 30,
        degradeP95Ms: 38,
        recoverP95Ms: 28,
        rollingSamples: 60,
        minimumSamples: 20,
        assessmentIntervalMs: 1_000,
        badAssessmentsToDegrade: 2,
        goodAssessmentsToRecover: 8,
        recoveryHoldMs: 18_000,
        recoveryCooldownMs: 15_000,
        degradeCooldownMs: 2_500,
        longFrameMs: 66,
        catastrophicFrameMs: 120,
        longFrameScoreToDegrade: 2,
        maximumSampleGapMs: 1_250,
        adaptiveStart: 'balanced',
        adaptiveBest: 'high',
        adaptiveWorst: 'minimum',
    }),
});

const DEFAULT_CLOCK = () => {
    if (typeof globalThis.performance?.now === 'function') return globalThis.performance.now();
    return Date.now();
};

function percentile(sorted, fraction) {
    if (sorted.length === 0) return 0;
    const index = Math.max(0, Math.ceil(sorted.length * fraction) - 1);
    return sorted[Math.min(sorted.length - 1, index)];
}

function roundTiming(value) {
    return Math.round(value * 100) / 100;
}

export function normalizePerformanceMode(value) {
    if (typeof value === 'string' && Object.hasOwn(PERFORMANCE_MODES, value)) return value;
    return 'adaptive';
}

export function normalizePerformanceTarget(value) {
    if (typeof value === 'string' && Object.hasOwn(PERFORMANCE_TARGETS, value)) return value;
    /* `mobile` is a useful declarative default without pretending every phone
     * can hold 60 Hz. Callers opt into mobile60 after capability measurement. */
    if (value === 'mobile') return 'mobile30';
    return 'desktop';
}

/**
 * Classify a client as mobile without using the viewport as a device proxy.
 *
 * A landscape phone is commonly wider than 700 CSS pixels, while a narrow
 * desktop window can be smaller. Asset choice must not invert for either case.
 * Prefer the browser's coarse mobile bit, then the known iPad desktop-UA shape,
 * then explicit mobile UAs. The final touch-only fallback covers foldables and
 * browsers that redact UA data without turning a touch-capable laptop mobile:
 * it requires a coarse primary pointer, no fine pointer anywhere, and a
 * portable-size browser-reported screen footprint.
 */
export function classifyClientDevice({
    userAgentDataMobile = null,
    userAgent = '',
    platform = '',
    maxTouchPoints = 0,
    primaryPointerCoarse = false,
    anyPointerFine = true,
    screenWidthPx = 0,
    screenHeightPx = 0,
} = {}) {
    if (userAgentDataMobile === true) return 'mobile';
    const ua = String(userAgent || '');
    const iPadDesktopUa = String(platform || '') === 'MacIntel'
        && Number(maxTouchPoints) > 1;
    if (iPadDesktopUa || /Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return 'mobile';

    const dimensions = [Number(screenWidthPx), Number(screenHeightPx)]
        .filter((value) => Number.isFinite(value) && value > 0);
    const longestSide = dimensions.length ? Math.max(...dimensions) : Number.POSITIVE_INFINITY;
    const touchOnlyPortable = Number(maxTouchPoints) > 0
        && primaryPointerCoarse === true
        && anyPointerFine !== true
        && longestSide <= 1_440;
    return touchOnlyPortable ? 'mobile' : 'desktop';
}

/** Read the browser signals once without consulting the current viewport size. */
export function clientDeviceSignals({
    navigatorRef = globalThis.navigator,
    windowRef = globalThis.window,
} = {}) {
    const mediaMatches = (query, fallback = false) => {
        try {
            const result = windowRef?.matchMedia?.(query);
            return result && typeof result.matches === 'boolean' ? result.matches : fallback;
        } catch (_) {
            return fallback;
        }
    };
    return freeze({
        userAgentDataMobile: navigatorRef?.userAgentData?.mobile ?? null,
        userAgent: navigatorRef?.userAgent ?? '',
        platform: navigatorRef?.platform ?? '',
        maxTouchPoints: navigatorRef?.maxTouchPoints ?? 0,
        primaryPointerCoarse: mediaMatches('(pointer: coarse)'),
        /* Unknown means "possibly fine": a redacted browser must not turn a
         * touch-capable desktop into a phone merely because a query failed. */
        anyPointerFine: mediaMatches('(any-pointer: fine)', true),
        screenWidthPx: windowRef?.screen?.width ?? 0,
        screenHeightPx: windowRef?.screen?.height ?? 0,
    });
}

/** Canonical browser-to-target boundary shared by Apartment, world, and prewarm. */
export function selectClientPerformanceTarget({
    navigatorRef = globalThis.navigator,
    windowRef = globalThis.window,
} = {}) {
    const network = navigatorRef?.connection
        || navigatorRef?.mozConnection
        || navigatorRef?.webkitConnection
        || null;
    return selectPerformanceTarget({
        deviceClass: classifyClientDevice(clientDeviceSignals({ navigatorRef, windowRef })),
        hardwareConcurrency: navigatorRef?.hardwareConcurrency,
        deviceMemoryGb: navigatorRef?.deviceMemory,
        saveData: network?.saveData === true,
    });
}

/**
 * Choose an honest initial frame target from coarse device capabilities.
 *
 * This is intentionally pure and conservative. It never parses a user agent,
 * viewport, locale, or any other potentially identifying value. The runtime
 * decides whether the device is mobile and supplies only coarse capacity
 * numbers; adaptation then measures the real result instead of trusting this
 * one-time guess forever.
 */
export function selectPerformanceTarget({
    deviceClass = 'desktop',
    hardwareConcurrency = 0,
    deviceMemoryGb = 0,
    saveData = false,
} = {}) {
    if (deviceClass !== 'mobile') return 'desktop';
    const capable = Number(hardwareConcurrency) >= 8 && Number(deviceMemoryGb) >= 6;
    return capable && saveData !== true ? 'mobile60' : 'mobile30';
}

function levelIndex(levelId) {
    return PERFORMANCE_LEVEL_ORDER.indexOf(levelId);
}

function clampAdaptiveLevel(levelId, target) {
    const best = levelIndex(target.adaptiveBest);
    const worst = levelIndex(target.adaptiveWorst);
    const candidate = levelIndex(levelId);
    const safe = candidate < 0 ? levelIndex(target.adaptiveStart) : candidate;
    return PERFORMANCE_LEVEL_ORDER[Math.min(worst, Math.max(best, safe))];
}

/**
 * Create one shared performance controller for an Apartment/world viewport.
 *
 * `clock` must return monotonically increasing milliseconds. A production
 * caller should inject `() => performance.now()`; tests inject a mutable clock.
 * `recordFrame` expects end-to-end presentation interval, not only JavaScript
 * render-call duration, otherwise idle GPU time and missed vsyncs are invisible.
 */
export function createAdaptivePerformanceController({
    mode = 'adaptive',
    target = 'desktop',
    initialLevel,
    clock = DEFAULT_CLOCK,
} = {}) {
    if (typeof clock !== 'function') throw new TypeError('clock must be a function');

    let modeId = normalizePerformanceMode(mode);
    let targetId = normalizePerformanceTarget(target);
    let targetConfig = PERFORMANCE_TARGETS[targetId];
    let adaptiveLevel = clampAdaptiveLevel(initialLevel || targetConfig.adaptiveStart, targetConfig);
    let samples = [];
    let longFrameScore = 0;
    let ignoredSamples = 0;
    let badAssessmentStreak = 0;
    let goodAssessmentStreak = 0;
    let decisionCount = 0;
    let lastAssessmentAt = null;
    let lastSampleAt = null;
    let lastChangeAt = Number.NEGATIVE_INFINITY;
    let lastDegradeAt = Number.NEGATIVE_INFINITY;
    let lastDecision = 'initial';
    let latestP50 = 0;
    let latestP95 = 0;
    const subscribers = new Set();
    const samplingSources = new Map();
    let samplingAuthority = null;

    const activeLevel = () => PERFORMANCE_MODES[modeId].fixedLevel || adaptiveLevel;

    function nowMs(explicitNow) {
        const value = explicitNow === undefined ? Number(clock()) : Number(explicitNow);
        if (!Number.isFinite(value)) throw new TypeError('clock must return finite milliseconds');
        return value;
    }

    function clearEvidence(now, { clearMetrics = false } = {}) {
        samples = [];
        longFrameScore = 0;
        badAssessmentStreak = 0;
        goodAssessmentStreak = 0;
        lastAssessmentAt = now;
        if (clearMetrics) {
            latestP50 = 0;
            latestP95 = 0;
        }
    }

    function transition(nextLevel, reason, now) {
        const previousLevel = adaptiveLevel;
        if (nextLevel === previousLevel) return null;
        adaptiveLevel = nextLevel;
        decisionCount += 1;
        lastDecision = reason;
        lastChangeAt = now;
        if (levelIndex(nextLevel) > levelIndex(previousLevel)) lastDegradeAt = now;
        /* Old-budget samples must never cascade into a second quality change.
         * The new budget gets a fresh measurement window. */
        clearEvidence(now);
        const decision = freeze({
            changed: true,
            reason,
            previousLevel,
            level: nextLevel,
            budget: PERFORMANCE_BUDGETS[nextLevel],
        });
        publish(decision);
        return decision;
    }

    function publish(change) {
        for (const subscriber of [...subscribers]) {
            try { subscriber(change); } catch (_) { /* one renderer cannot block the others */ }
        }
    }

    function assess(now) {
        if (samples.length < targetConfig.minimumSamples) return null;
        if (lastAssessmentAt !== null && now - lastAssessmentAt < targetConfig.assessmentIntervalMs) {
            return null;
        }
        lastAssessmentAt = now;

        const sorted = [...samples].sort((a, b) => a - b);
        latestP50 = percentile(sorted, 0.5);
        latestP95 = percentile(sorted, 0.95);

        const overBudget = latestP95 > targetConfig.degradeP95Ms;
        const hasHeadroom = latestP95 < targetConfig.recoverP95Ms && longFrameScore === 0;
        const longFrameBurst = longFrameScore >= targetConfig.longFrameScoreToDegrade;

        if (overBudget) badAssessmentStreak += 1;
        else badAssessmentStreak = 0;

        if (hasHeadroom) goodAssessmentStreak += 1;
        else goodAssessmentStreak = 0;

        if (!PERFORMANCE_MODES[modeId].adaptive) {
            longFrameScore = 0;
            return null;
        }

        const current = levelIndex(adaptiveLevel);
        const worst = levelIndex(targetConfig.adaptiveWorst);
        const best = levelIndex(targetConfig.adaptiveBest);
        const canDegrade = current < worst && now - lastChangeAt >= targetConfig.degradeCooldownMs;
        const canRecover = current > best
            && now - lastChangeAt >= targetConfig.recoveryCooldownMs
            && now - lastDegradeAt >= targetConfig.recoveryHoldMs;

        if (canDegrade && (longFrameBurst
            || badAssessmentStreak >= targetConfig.badAssessmentsToDegrade)) {
            return transition(
                PERFORMANCE_LEVEL_ORDER[current + 1],
                longFrameBurst ? 'long-frame-burst' : 'sustained-over-budget',
                now,
            );
        }

        if (canRecover && goodAssessmentStreak >= targetConfig.goodAssessmentsToRecover) {
            return transition(PERFORMANCE_LEVEL_ORDER[current - 1], 'sustained-headroom', now);
        }

        /* A burst is evidence for this interval, not permanent poison in the
         * rolling window. p95 still carries sustained slowness across windows. */
        longFrameScore = 0;
        return null;
    }

    function recordFrame(frameTimeMs, {
        now: explicitNow,
        active = true,
        longTaskMs = 0,
        samplingSource,
    } = {}) {
        /* During a handoff both render loops observe the same presentation
         * interval. Only the deterministic authority contributes it. */
        if (samplingSources.size > 0 && samplingSource !== samplingAuthority) return null;
        const now = nowMs(explicitNow);
        const duration = Number(frameTimeMs);
        const taskDuration = Number(longTaskMs);

        if (!active) {
            ignoredSamples += 1;
            lastSampleAt = now;
            clearEvidence(now);
            return null;
        }
        if (!Number.isFinite(duration) || duration <= 0
            || !Number.isFinite(taskDuration) || taskDuration < 0) {
            ignoredSamples += 1;
            return null;
        }
        if (lastSampleAt !== null && now < lastSampleAt) {
            ignoredSamples += 1;
            return null;
        }
        if (lastSampleAt !== null && now - lastSampleAt > targetConfig.maximumSampleGapMs) {
            /* A suspended tab, debugger pause, or sleeping device is not a slow
             * renderer. Discard its resume frame and begin a new window. */
            ignoredSamples += 1;
            lastSampleAt = now;
            clearEvidence(now);
            return null;
        }

        lastSampleAt = now;
        samples.push(duration);
        if (samples.length > targetConfig.rollingSamples) samples.shift();

        const frameSeverity = duration >= targetConfig.catastrophicFrameMs
            ? 2
            : duration >= targetConfig.longFrameMs ? 1 : 0;
        const taskSeverity = taskDuration >= targetConfig.catastrophicFrameMs
            ? 2
            : taskDuration >= targetConfig.longFrameMs ? 1 : 0;
        /* A PerformanceObserver entry may describe the same stall as the frame
         * interval. `max`, not addition, prevents double-counting it. */
        longFrameScore += Math.max(frameSeverity, taskSeverity);

        if (lastAssessmentAt === null) lastAssessmentAt = now;
        return assess(now);
    }

    function setMode(nextMode, { now: explicitNow } = {}) {
        const normalized = normalizePerformanceMode(nextMode);
        if (normalized === modeId) return null;
        const now = nowMs(explicitNow);
        const previousLevel = activeLevel();
        modeId = normalized;
        adaptiveLevel = clampAdaptiveLevel(adaptiveLevel, targetConfig);
        clearEvidence(now, { clearMetrics: true });
        lastSampleAt = null;
        lastDecision = `mode:${modeId}`;
        const nextLevel = activeLevel();
        const change = freeze({
            changed: previousLevel !== nextLevel,
            reason: lastDecision,
            previousLevel,
            level: nextLevel,
            budget: PERFORMANCE_BUDGETS[nextLevel],
        });
        publish(change);
        return change;
    }

    function setTarget(nextTarget, { now: explicitNow } = {}) {
        const normalized = normalizePerformanceTarget(nextTarget);
        if (normalized === targetId) return null;
        const now = nowMs(explicitNow);
        const previousLevel = activeLevel();
        targetId = normalized;
        targetConfig = PERFORMANCE_TARGETS[targetId];
        adaptiveLevel = clampAdaptiveLevel(adaptiveLevel, targetConfig);
        clearEvidence(now, { clearMetrics: true });
        lastSampleAt = null;
        lastDecision = `target:${targetId}`;
        const nextLevel = activeLevel();
        const change = freeze({
            changed: previousLevel !== nextLevel,
            reason: lastDecision,
            previousLevel,
            level: nextLevel,
            budget: PERFORMANCE_BUDGETS[nextLevel],
        });
        publish(change);
        return change;
    }

    function subscribe(subscriber) {
        if (typeof subscriber !== 'function') throw new TypeError('subscriber must be a function');
        subscribers.add(subscriber);
        let subscribed = true;
        return () => {
            if (!subscribed) return false;
            subscribed = false;
            return subscribers.delete(subscriber);
        };
    }

    function setSamplingSourceActive(source, active, {
        priority = 0,
        now: explicitNow,
    } = {}) {
        if (source === null || source === undefined) {
            throw new TypeError('sampling source identity is required');
        }
        const numericPriority = Number(priority);
        const normalizedPriority = Number.isFinite(numericPriority) ? numericPriority : 0;
        if (active === true) {
            if (samplingSources.get(source) === normalizedPriority) {
                return samplingAuthority === source;
            }
            samplingSources.set(source, normalizedPriority);
        } else if (!samplingSources.delete(source)) {
            return false;
        }

        let nextAuthority = null;
        let bestPriority = Number.NEGATIVE_INFINITY;
        for (const [candidate, candidatePriority] of samplingSources) {
            /* Map insertion order breaks equal-priority ties, so ownership is
             * stable instead of flipping with whichever frame arrived last. */
            if (candidatePriority > bestPriority) {
                nextAuthority = candidate;
                bestPriority = candidatePriority;
            }
        }
        if (nextAuthority !== samplingAuthority) {
            const now = nowMs(explicitNow);
            samplingAuthority = nextAuthority;
            lastSampleAt = null;
            clearEvidence(now);
        }
        return samplingAuthority === source;
    }

    function isSamplingAuthority(source) {
        return samplingSources.size === 0 || samplingAuthority === source;
    }

    function getBudget() {
        return PERFORMANCE_BUDGETS[activeLevel()];
    }

    function getDiagnostics() {
        /* Deliberately an allowlist. Do not spread constructor options or sample
         * metadata here; those are common routes for location-bearing state to
         * leak into logs. */
        return freeze({
            schemaVersion: 1,
            mode: modeId,
            targetTier: targetId,
            deviceClass: targetConfig.deviceClass,
            targetFps: targetConfig.targetFps,
            frameBudgetMs: roundTiming(targetConfig.frameBudgetMs),
            qualityLevel: activeLevel(),
            adaptive: PERFORMANCE_MODES[modeId].adaptive,
            sampleCount: samples.length,
            p50FrameMs: roundTiming(latestP50),
            p95FrameMs: roundTiming(latestP95),
            longFrameScore,
            badAssessmentStreak,
            goodAssessmentStreak,
            ignoredSamples,
            decisionCount,
            lastDecision,
            budget: getBudget(),
        });
    }

    return freeze({
        recordFrame,
        setMode,
        setTarget,
        subscribe,
        setSamplingSourceActive,
        isSamplingAuthority,
        getBudget,
        getDiagnostics,
    });
}
