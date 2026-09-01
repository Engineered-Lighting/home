const WEBGL_KINDS = new Set(["webgl", "webgl2", "experimental-webgl"]);

const quantile = (values, percentile) => {
  if (!values.length) return null;
  const ordered = [...values].sort((left, right) => left - right);
  const index = Math.min(ordered.length - 1, Math.ceil(percentile * ordered.length) - 1);
  return Number(ordered[Math.max(0, index)].toFixed(2));
};

export function summarizeDurations(values) {
  const finite = values.filter((value) => Number.isFinite(value) && value >= 0);
  return Object.freeze({
    samples: finite.length,
    minMs: finite.length ? Number(Math.min(...finite).toFixed(2)) : null,
    medianMs: quantile(finite, 0.5),
    p95Ms: quantile(finite, 0.95),
    maxMs: finite.length ? Number(Math.max(...finite).toFixed(2)) : null,
  });
}

export function installRuntimeInstrumentation(globalRef = globalThis) {
  if (globalRef.__HOME_SPATIAL_SPIKE_INSTRUMENTATION__) {
    return globalRef.__HOME_SPATIAL_SPIKE_INSTRUMENTATION__;
  }

  const contextRecords = [];
  let workersCreated = 0;
  let workersTerminated = 0;
  let webglTrackingInstalled = false;
  let workerTrackingInstalled = false;
  const activeWorkers = new Set();
  const Canvas = globalRef.HTMLCanvasElement;
  const originalGetContext = Canvas?.prototype?.getContext;

  if (originalGetContext) {
    try {
      Canvas.prototype.getContext = function observedGetContext(kind, ...args) {
        const context = originalGetContext.call(this, kind, ...args);
        if (context && WEBGL_KINDS.has(String(kind).toLowerCase())) {
          const known = contextRecords.some((record) => record.context === context);
          if (!known) contextRecords.push({ canvas: this, context });
        }
        return context;
      };
      webglTrackingInstalled = Canvas.prototype.getContext !== originalGetContext;
    } catch {
      webglTrackingInstalled = false;
    }
  }

  const OriginalWorker = globalRef.Worker;
  if (typeof OriginalWorker === "function") {
    class ObservedWorker extends OriginalWorker {
      constructor(...args) {
        super(...args);
        workersCreated += 1;
        activeWorkers.add(this);
      }

      terminate() {
        if (activeWorkers.delete(this)) workersTerminated += 1;
        return super.terminate();
      }
    }
    try {
      globalRef.Worker = ObservedWorker;
      workerTrackingInstalled = globalRef.Worker === ObservedWorker;
    } catch {
      workerTrackingInstalled = false;
    }
  }

  const instrumentation = Object.freeze({
    snapshot() {
      const webgl = contextRecords.reduce((counts, record) => {
        const lost = typeof record.context.isContextLost === "function" && record.context.isContextLost();
        if (!lost) {
          counts.live += 1;
          counts[record.canvas.isConnected ? "attached" : "detached"] += 1;
        }
        return counts;
      }, { observed: contextRecords.length, live: 0, attached: 0, detached: 0 });
      return Object.freeze({
        tracking: Object.freeze({
          workers: workerTrackingInstalled,
          webgl: webglTrackingInstalled,
        }),
        workers: Object.freeze({
          created: workersCreated,
          terminated: workersTerminated,
          active: activeWorkers.size,
        }),
        webgl: Object.freeze(webgl),
      });
    },
  });

  Object.defineProperty(globalRef, "__HOME_SPATIAL_SPIKE_INSTRUMENTATION__", {
    value: instrumentation,
    configurable: false,
    enumerable: false,
    writable: false,
  });
  return instrumentation;
}

export function resourceTotals(performanceRef = performance, since = 0) {
  const entries = performanceRef.getEntriesByType("resource")
    .filter((entry) => entry.startTime >= since);
  const localHost = globalThis.location?.hostname || "";
  let otherHostEntries = 0;
  for (const entry of entries) {
    try {
      const hostname = new URL(entry.name, globalThis.location?.href).hostname;
      if (hostname && hostname !== localHost) otherHostEntries += 1;
    } catch {
      otherHostEntries += 1;
    }
  }
  return Object.freeze({
    entries: entries.length,
    transferBytes: entries.reduce((sum, entry) => sum + (entry.transferSize || 0), 0),
    encodedBytes: entries.reduce((sum, entry) => sum + (entry.encodedBodySize || 0), 0),
    decodedBytes: entries.reduce((sum, entry) => sum + (entry.decodedBodySize || 0), 0),
    otherHostEntries,
  });
}

export function startFrameSampler({ requestFrame = requestAnimationFrame, cancelFrame = cancelAnimationFrame } = {}) {
  const intervals = [];
  let previous = null;
  let handle = null;
  let stopped = false;

  const tick = (now) => {
    if (stopped) return;
    if (previous !== null) intervals.push(Math.max(0, now - previous));
    previous = now;
    handle = requestFrame(tick);
  };
  handle = requestFrame(tick);

  return Object.freeze({
    stop() {
      if (!stopped) {
        stopped = true;
        if (handle !== null) cancelFrame(handle);
      }
      const distribution = summarizeDurations(intervals);
      return Object.freeze({
        ...distribution,
        fpsAtMedian: distribution.medianMs ? Number((1000 / distribution.medianMs).toFixed(1)) : null,
        fpsAtP95: distribution.p95Ms ? Number((1000 / distribution.p95Ms).toFixed(1)) : null,
      });
    },
  });
}

export function afterVisualSettlement(frameCount = 2) {
  return new Promise((resolve) => {
    const advance = (remaining) => {
      if (remaining <= 0) {
        resolve();
        return;
      }
      requestAnimationFrame(() => advance(remaining - 1));
    };
    advance(Math.max(1, frameCount));
  });
}

export function memorySample(performanceRef = performance) {
  const memory = performanceRef.memory;
  if (!memory) return Object.freeze({ supported: false });
  return Object.freeze({
    supported: true,
    usedHeapBytes: memory.usedJSHeapSize,
    totalHeapBytes: memory.totalJSHeapSize,
    heapLimitBytes: memory.jsHeapSizeLimit,
  });
}
