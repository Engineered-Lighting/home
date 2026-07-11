(function () {
  const now = () => (window.performance && performance.now ? performance.now() : Date.now());
  const startedAt = Date.now();
  const marks = [];
  const measures = [];
  const bootFiles = [];
  const features = [];

  function cloneDetail(detail) {
    if (!detail || typeof detail !== "object") return detail || null;
    try {
      return JSON.parse(JSON.stringify(detail));
    } catch (_) {
      return String(detail);
    }
  }

  function mark(name, detail) {
    const entry = { name, t: Math.round(now() * 10) / 10, detail: cloneDetail(detail) };
    marks.push(entry);
    if (marks.length > 400) marks.shift();
    return entry;
  }

  function measure(name, startMs, endMs, detail) {
    const entry = {
      name,
      startMs: Math.round((startMs || 0) * 10) / 10,
      endMs: Math.round((endMs || now()) * 10) / 10,
      durationMs: Math.round(((endMs || now()) - (startMs || 0)) * 10) / 10,
      detail: cloneDetail(detail),
    };
    measures.push(entry);
    if (measures.length > 400) measures.shift();
    return entry;
  }

  function recordBootFile(file, phase, durationMs, detail) {
    const entry = {
      file,
      phase,
      durationMs: Math.round((durationMs || 0) * 10) / 10,
      t: Math.round(now() * 10) / 10,
      detail: cloneDetail(detail),
    };
    bootFiles.push(entry);
    if (bootFiles.length > 600) bootFiles.shift();
    return entry;
  }

  function recordFeature(feature, event, durationMs, detail) {
    const entry = {
      feature,
      event,
      durationMs: durationMs == null ? null : Math.round(durationMs * 10) / 10,
      t: Math.round(now() * 10) / 10,
      detail: cloneDetail(detail),
    };
    features.push(entry);
    if (features.length > 300) features.shift();
    return entry;
  }

  function summarizeBootFiles() {
    const byFile = new Map();
    for (const item of bootFiles) {
      const row = byFile.get(item.file) || { file: item.file, fetchMs: 0, transformMs: 0, executeMs: 0, totalMs: 0 };
      if (item.phase === "fetch") row.fetchMs += item.durationMs || 0;
      else if (item.phase === "transform") row.transformMs += item.durationMs || 0;
      else if (item.phase === "execute") row.executeMs += item.durationMs || 0;
      row.totalMs += item.durationMs || 0;
      byFile.set(item.file, row);
    }
    return Array.from(byFile.values())
      .map((row) => ({
        ...row,
        fetchMs: Math.round(row.fetchMs * 10) / 10,
        transformMs: Math.round(row.transformMs * 10) / 10,
        executeMs: Math.round(row.executeMs * 10) / 10,
        totalMs: Math.round(row.totalMs * 10) / 10,
      }))
      .sort((a, b) => b.totalMs - a.totalMs);
  }

  function snapshot() {
    return {
      startedAt,
      ageMs: Date.now() - startedAt,
      lazyFeaturesEnabled: !!window.__HOME_LAZY_FEATURES_ENABLED,
      boot: {
        state: window.__bootState ? {
          loaded: window.__bootState.loaded,
          total: window.__bootState.total,
          done: !!window.__bootState.done,
          current: window.__bootState.current || null,
          failed: window.__bootState.failed || null,
          skipped: window.__bootState.skipped || [],
          phase: window.__bootState.phase || null,
          shellEnabled: !!window.__bootState.shellEnabled,
        } : null,
        shell: window.__homeBootShell || null,
        slowestFiles: summarizeBootFiles().slice(0, 12),
      },
      warmup: window.__HOME_BACKGROUND_WARMUP || null,
      features: window.HomeFeatureLoader && window.HomeFeatureLoader.statusAll
        ? window.HomeFeatureLoader.statusAll()
        : null,
      marks: marks.slice(-80),
      measures: measures.slice(-80),
      bootFiles: bootFiles.slice(-160),
      featureEvents: features.slice(-120),
    };
  }

  function reset() {
    marks.length = 0;
    measures.length = 0;
    bootFiles.length = 0;
    features.length = 0;
    mark("perf:reset");
  }

  window.__homePerf = {
    mark,
    measure,
    recordBootFile,
    recordFeature,
    snapshot,
    reset,
  };

  mark("perf:init");
})();
