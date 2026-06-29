/**
 * render-stability.js — re-render detector + perf instrument.
 *
 * Browser-side helper loaded into a preview-server page via preview_eval
 * to detect re-render storms (the people-drawer flicker bug from
 * Addendum 17 had ~1000 mutations/sec).
 *
 * API:
 *   const probe = RenderStability.start(target, opts)
 *       target: CSS selector or Element
 *       opts: { windowMs (default 3000), include: { children, attrs, text } }
 *
 *   probe.metrics() → { mutationCount, attributeCount, childListCount,
 *                       characterDataCount, durationMs, ratePerSec }
 *
 *   probe.stop() → final metrics + disconnects observer
 *
 *   probe.assertStable({ maxMutations, maxRatePerSec })
 *       throws Error if budget exceeded
 *
 * Usage (preview eval):
 *   const p = RenderStability.start('[role="dialog"]', { windowMs: 3000 });
 *   await new Promise(r => setTimeout(r, 3000));
 *   const m = p.stop();
 *   return { ok: m.mutationCount <= 30, mutations: m.mutationCount };
 *
 * Self-test: this file isn't directly runnable (needs DOM). Smoke test
 * lives in tools/run-people-tests.js (the people-drawer-no-flicker
 * scenario uses this harness).
 */

(function (root) {
  "use strict";

  function start(target, opts) {
    opts = opts || {};
    const el = typeof target === "string" ? document.querySelector(target) : target;
    if (!el) throw new Error("RenderStability: target not found: " + target);

    const observerOpts = {
      childList: opts.include && opts.include.children !== false,
      attributes: opts.include && opts.include.attrs !== false,
      characterData: opts.include && opts.include.text !== false,
      subtree: opts.include && opts.include.subtree !== false,
    };
    // Default to all-on if no include opts passed
    if (!opts.include) {
      observerOpts.childList = true;
      observerOpts.attributes = true;
      observerOpts.characterData = true;
      observerOpts.subtree = true;
    }

    const counts = {
      mutationCount: 0,
      attributeCount: 0,
      childListCount: 0,
      characterDataCount: 0,
    };

    const startedAt = Date.now();
    const observer = new MutationObserver(function (mutations) {
      counts.mutationCount += mutations.length;
      for (let i = 0; i < mutations.length; i++) {
        const m = mutations[i];
        if (m.type === "attributes") counts.attributeCount++;
        else if (m.type === "childList") counts.childListCount++;
        else if (m.type === "characterData") counts.characterDataCount++;
      }
    });
    observer.observe(el, observerOpts);

    let stopped = false;
    function _snapshot() {
      const durationMs = Date.now() - startedAt;
      const ratePerSec = durationMs > 0
        ? Math.round((counts.mutationCount / durationMs) * 1000)
        : 0;
      return Object.assign({ durationMs, ratePerSec }, counts);
    }

    return {
      metrics: _snapshot,
      stop() {
        if (stopped) return _snapshot();
        observer.disconnect();
        stopped = true;
        return _snapshot();
      },
      assertStable(budget) {
        budget = budget || {};
        const m = _snapshot();
        const issues = [];
        if (typeof budget.maxMutations === "number" &&
            m.mutationCount > budget.maxMutations) {
          issues.push("mutations " + m.mutationCount + " > " + budget.maxMutations);
        }
        if (typeof budget.maxRatePerSec === "number" &&
            m.ratePerSec > budget.maxRatePerSec) {
          issues.push("rate/s " + m.ratePerSec + " > " + budget.maxRatePerSec);
        }
        if (issues.length > 0) {
          throw new Error("RenderStability violated: " + issues.join(", ") +
                          " (full: " + JSON.stringify(m) + ")");
        }
        return m;
      },
    };
  }

  /** Convenience: observe for N ms then return metrics. */
  async function watch(target, windowMs, opts) {
    const probe = start(target, opts);
    await new Promise(function (r) { setTimeout(r, windowMs); });
    return probe.stop();
  }

  const api = { start, watch };
  if (typeof root !== "undefined") root.RenderStability = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof self !== "undefined" ? self : (typeof window !== "undefined" ? window : globalThis));
