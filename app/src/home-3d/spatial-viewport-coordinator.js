/*
 * Apartment/world semantic viewport coordinator.
 *
 * Navigation arbitration is informed by the latest-intent policy in God's Eye
 * View (MIT, Bilawal Sidhu), pinned at revision
 * d8f1742783cddd6bbc86033d0db06dc6ec746304. This is an original,
 * Home-specific implementation; it does not reuse that project's UI or data.
 * https://github.com/bilawalsidhu/gods-eye-view/blob/d8f1742783cddd6bbc86033d0db06dc6ec746304/src/navigationPolicy.js
 */

export const SPATIAL_VIEWPORT_SCALES = Object.freeze([
  "apartment",
  "parcel",
  "city",
  "country",
  "planet",
]);

export const SPATIAL_VIEWPORT_DIRECTIONS = Object.freeze({
  OUTWARD: "outward",
  INWARD: "inward",
});

export const SPATIAL_VIEWPORT_ACTIONS = Object.freeze({
  SET_REDUCED_MOTION: "spatial-viewport/set-reduced-motion",
  REQUEST_ZOOM: "spatial-viewport/request-zoom",
  TARGET_READY: "spatial-viewport/target-ready",
  COMMIT: "spatial-viewport/commit",
  FAIL: "spatial-viewport/fail",
  CANCEL: "spatial-viewport/cancel",
});

export const SPATIAL_VIEWPORT_DURATIONS_MS = Object.freeze({
  SURFACE_HANDOFF: 450,
  WORLD_SCALE: 300,
  REDUCED_MOTION: 0,
});

const SCALE_INDEX = new Map(SPATIAL_VIEWPORT_SCALES.map((scale, index) => [scale, index]));
const VALID_DIRECTIONS = new Set(Object.values(SPATIAL_VIEWPORT_DIRECTIONS));
const ADAPTER_HOOKS = Object.freeze(["prepare", "activate", "suspend", "cancel"]);

function surfaceForScale(scale) {
  return scale === "apartment" ? "apartment" : "world";
}

function freezeTransition(transition) {
  return transition ? Object.freeze({ ...transition }) : null;
}

function freezeState(state) {
  return Object.freeze({
    ...state,
    transition: freezeTransition(state.transition),
  });
}

function assertDirection(direction) {
  if (!VALID_DIRECTIONS.has(direction)) {
    throw new TypeError(`Unknown semantic zoom direction: ${String(direction)}`);
  }
}

function assertIntent(action) {
  if (typeof action.intentId !== "string" || action.intentId.length === 0) {
    throw new TypeError("intentId must be a non-empty opaque string");
  }
  if (!Number.isSafeInteger(action.intentRevision) || action.intentRevision <= 0) {
    throw new TypeError("intentRevision must be a positive safe integer");
  }
}

function transitionDuration(fromScale, toScale, reducedMotion) {
  if (reducedMotion) return SPATIAL_VIEWPORT_DURATIONS_MS.REDUCED_MOTION;
  return surfaceForScale(fromScale) === surfaceForScale(toScale)
    ? SPATIAL_VIEWPORT_DURATIONS_MS.WORLD_SCALE
    : SPATIAL_VIEWPORT_DURATIONS_MS.SURFACE_HANDOFF;
}

function nextScale(scale, direction) {
  const index = SCALE_INDEX.get(scale);
  if (index === undefined) throw new TypeError(`Unknown viewport scale: ${String(scale)}`);
  const delta = direction === SPATIAL_VIEWPORT_DIRECTIONS.OUTWARD ? 1 : -1;
  return SPATIAL_VIEWPORT_SCALES[Math.max(0, Math.min(SPATIAL_VIEWPORT_SCALES.length - 1, index + delta))];
}

function matchesActiveIntent(state, action) {
  return action.intentId === state.latestIntentId
    && action.intentRevision === state.latestIntentRevision;
}

export function createInitialSpatialViewportState({ reducedMotion = false } = {}) {
  return freezeState({
    committedScale: "apartment",
    targetScale: "apartment",
    phase: "idle",
    latestIntentId: null,
    latestIntentRevision: 0,
    reducedMotion: Boolean(reducedMotion),
    transition: null,
  });
}

/*
 * Pure transition reducer. Apartment's own room/close/overview detents are not
 * represented here: the rig keeps them. The host dispatches an outward request
 * only when the rig is already at Apartment overview.
 */
export function reduceSpatialViewport(state, action) {
  if (!state || !action || typeof action.type !== "string") {
    throw new TypeError("Spatial viewport state and action are required");
  }

  switch (action.type) {
    case SPATIAL_VIEWPORT_ACTIONS.SET_REDUCED_MOTION: {
      const reducedMotion = Boolean(action.enabled);
      return reducedMotion === state.reducedMotion
        ? state
        : freezeState({ ...state, reducedMotion });
    }

    case SPATIAL_VIEWPORT_ACTIONS.REQUEST_ZOOM: {
      assertDirection(action.direction);
      assertIntent(action);
      if (action.intentRevision <= state.latestIntentRevision) return state;

      const baseScale = state.transition?.toScale ?? state.committedScale;
      const stableApartment = state.phase === "idle" && state.committedScale === "apartment";
      if (baseScale === "apartment" && stableApartment) {
        if (action.direction !== SPATIAL_VIEWPORT_DIRECTIONS.OUTWARD || action.atApartmentOverview !== true) {
          return state;
        }
      }

      const destination = nextScale(baseScale, action.direction);
      if (destination === baseScale) return state;

      // Reversing a pending transition back to the committed scale invalidates
      // the older work without producing a redundant lifecycle transition.
      if (destination === state.committedScale) {
        return freezeState({
          ...state,
          targetScale: state.committedScale,
          phase: "idle",
          latestIntentId: action.intentId,
          latestIntentRevision: action.intentRevision,
          transition: null,
        });
      }

      const fromScale = state.committedScale;
      const fromSurface = surfaceForScale(fromScale);
      const toSurface = surfaceForScale(destination);
      return freezeState({
        ...state,
        targetScale: destination,
        phase: "preparing",
        latestIntentId: action.intentId,
        latestIntentRevision: action.intentRevision,
        transition: {
          intentId: action.intentId,
          intentRevision: action.intentRevision,
          direction: action.direction,
          fromScale,
          toScale: destination,
          fromSurface,
          toSurface,
          durationMs: transitionDuration(fromScale, destination, state.reducedMotion),
        },
      });
    }

    case SPATIAL_VIEWPORT_ACTIONS.TARGET_READY:
      assertIntent(action);
      if (!matchesActiveIntent(state, action) || state.phase !== "preparing") return state;
      return freezeState({ ...state, phase: "activating" });

    case SPATIAL_VIEWPORT_ACTIONS.COMMIT:
      assertIntent(action);
      if (!matchesActiveIntent(state, action) || state.phase !== "activating" || !state.transition) return state;
      return freezeState({
        ...state,
        committedScale: state.transition.toScale,
        targetScale: state.transition.toScale,
        phase: "idle",
        transition: null,
      });

    case SPATIAL_VIEWPORT_ACTIONS.FAIL:
    case SPATIAL_VIEWPORT_ACTIONS.CANCEL:
      assertIntent(action);
      if (!matchesActiveIntent(state, action) || !state.transition) return state;
      return freezeState({
        ...state,
        targetScale: state.committedScale,
        phase: "idle",
        transition: null,
      });

    default:
      throw new TypeError(`Unknown spatial viewport action: ${action.type}`);
  }
}

function validateSurfaceAdapter(name, adapter) {
  if (!adapter || typeof adapter !== "object") {
    throw new TypeError(`${name}Surface must be a lifecycle adapter object`);
  }
  for (const hook of ADAPTER_HOOKS) {
    if (adapter[hook] !== undefined && typeof adapter[hook] !== "function") {
      throw new TypeError(`${name}Surface.${hook} must be a function when provided`);
    }
  }
  return adapter;
}

function callHook(adapter, hook, context) {
  return adapter[hook] ? adapter[hook](context) : undefined;
}

async function ignoreHookFailure(adapter, hook, context) {
  try {
    await callHook(adapter, hook, context);
  } catch (_) {
    // Cancellation is best-effort cleanup. It must never outrank a newer
    // navigation intent or replace the renderer failure that caused it.
  }
}

function adapterForSurface(surface, apartmentSurface, worldSurface) {
  return surface === "apartment" ? apartmentSurface : worldSurface;
}

function isAbort(error) {
  return error?.name === "AbortError";
}

/*
 * Effect runner around the pure reducer. Adapters own renderer details; this
 * coordinator supplies only lifecycle, semantic scale, duration, and an abort
 * signal. Hooks must be idempotent and honor signal cancellation.
 */
export function createSpatialViewportCoordinator({
  apartmentSurface,
  worldSurface,
  reducedMotion = false,
  onStateChange = null,
} = {}) {
  const apartment = validateSurfaceAdapter("apartment", apartmentSurface);
  const world = validateSurfaceAdapter("world", worldSurface);
  if (onStateChange !== null && typeof onStateChange !== "function") {
    throw new TypeError("onStateChange must be a function when provided");
  }

  let state = createInitialSpatialViewportState({ reducedMotion });
  let intentSequence = 0;
  let active = null;
  let disposed = false;

  function publish(next) {
    if (next === state) return false;
    state = next;
    onStateChange?.(state);
    return true;
  }

  function dispatch(action) {
    publish(reduceSpatialViewport(state, action));
    return state;
  }

  function current(context) {
    return !disposed
      && !context.signal.aborted
      && state.latestIntentId === context.intentId
      && state.latestIntentRevision === context.intentRevision
      && state.transition?.intentId === context.intentId
      && state.transition?.intentRevision === context.intentRevision;
  }

  function cancelActive() {
    if (!active) return;
    const previous = active;
    active = null;
    previous.controller.abort();
    void ignoreHookFailure(previous.targetAdapter, "cancel", previous.context);
  }

  async function execute(transition) {
    const controller = new AbortController();
    const sourceAdapter = adapterForSurface(transition.fromSurface, apartment, world);
    const targetAdapter = adapterForSurface(transition.toSurface, apartment, world);
    const context = Object.freeze({ ...transition, signal: controller.signal });
    const record = { controller, sourceAdapter, targetAdapter, context };
    active = record;

    try {
      await callHook(targetAdapter, "prepare", context);
      if (!current(context)) return Object.freeze({ status: "stale", intentId: context.intentId });

      dispatch({
        type: SPATIAL_VIEWPORT_ACTIONS.TARGET_READY,
        intentId: context.intentId,
        intentRevision: context.intentRevision,
      });
      await callHook(targetAdapter, "activate", context);
      if (!current(context)) return Object.freeze({ status: "stale", intentId: context.intentId });

      if (transition.fromSurface !== transition.toSurface) {
        await callHook(sourceAdapter, "suspend", context);
        if (!current(context)) return Object.freeze({ status: "stale", intentId: context.intentId });
      }

      dispatch({
        type: SPATIAL_VIEWPORT_ACTIONS.COMMIT,
        intentId: context.intentId,
        intentRevision: context.intentRevision,
      });
      return Object.freeze({ status: "committed", intentId: context.intentId, scale: context.toScale });
    } catch (error) {
      if (controller.signal.aborted || isAbort(error) || !current(context)) {
        if (current(context)) {
          dispatch({
            type: SPATIAL_VIEWPORT_ACTIONS.CANCEL,
            intentId: context.intentId,
            intentRevision: context.intentRevision,
          });
        }
        return Object.freeze({ status: "cancelled", intentId: context.intentId });
      }
      dispatch({
        type: SPATIAL_VIEWPORT_ACTIONS.FAIL,
        intentId: context.intentId,
        intentRevision: context.intentRevision,
      });
      await ignoreHookFailure(targetAdapter, "cancel", context);
      return Object.freeze({ status: "failed", intentId: context.intentId, error });
    } finally {
      if (active === record) active = null;
    }
  }

  async function requestSemanticZoom(direction, {
    atApartmentOverview = false,
    intentId = null,
  } = {}) {
    if (disposed) throw new Error("Spatial viewport coordinator is disposed");
    assertDirection(direction);
    const intentRevision = ++intentSequence;
    const resolvedIntentId = intentId ?? `viewport-intent-${intentRevision}`;
    const before = state;
    const previousTransition = state.transition;
    dispatch({
      type: SPATIAL_VIEWPORT_ACTIONS.REQUEST_ZOOM,
      direction,
      atApartmentOverview,
      intentId: resolvedIntentId,
      intentRevision,
    });

    if (state === before) {
      return Object.freeze({ status: "rig-owned-or-limit", scale: state.targetScale });
    }

    if (previousTransition) cancelActive();
    if (!state.transition) {
      return Object.freeze({ status: "settled", intentId: resolvedIntentId, scale: state.committedScale });
    }
    return execute(state.transition);
  }

  return Object.freeze({
    getState() { return state; },
    requestSemanticZoom,
    setReducedMotion(enabled) {
      if (disposed) throw new Error("Spatial viewport coordinator is disposed");
      dispatch({ type: SPATIAL_VIEWPORT_ACTIONS.SET_REDUCED_MOTION, enabled });
      return state;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      cancelActive();
    },
  });
}
