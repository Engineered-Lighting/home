/* simulation-timeline.jsx — Simulation Mode scripted-scenario engine.
 *
 * Plays a sequence of timed deltas. Scenarios with a `timeline` array
 * use this to animate state transitions over time (e.g. action-success
 * scrolls: user message → thinking → action card → ok → assistant reply).
 *
 * Strict cleanup: switching scenarios cancels all pending timers BEFORE
 * applying the next scenario's snapshot. Same on exit. Prevents stale
 * timer fires from corrupting the next scenario's state.
 *
 * Public API:
 *   const tl = SimTimeline.create();
 *   tl.play(timelineArray, applyDelta);
 *   tl.cancel();
 */

(function () {
  function create() {
    const timers = [];
    let started = 0;

    function cancel() {
      while (timers.length) {
        const id = timers.pop();
        try { clearTimeout(id); } catch (e) { /* ignore */ }
      }
      started = 0;
    }

    /* Play a timeline.
     *   timeline: [{ at: <ms-from-start>, delta: <object | function> }, ...]
     *   applyDelta: (delta) => void
     *
     * `delta` can be a plain object (merged via the applyDelta caller)
     * OR a function (called with the current scenario's apply helper).
     */
    function play(timeline, applyDelta) {
      cancel();
      if (!Array.isArray(timeline) || !timeline.length) return;
      started = Date.now();
      for (const step of timeline) {
        const delay = Math.max(0, step.at | 0);
        const id = setTimeout(() => {
          // Drop this id from the active set
          const idx = timers.indexOf(id);
          if (idx >= 0) timers.splice(idx, 1);
          try {
            if (typeof step.delta === "function") {
              step.delta(applyDelta);
            } else if (step.delta && typeof step.delta === "object") {
              applyDelta(step.delta);
            }
          } catch (e) {
            console.warn("[sim-timeline] step failed:", e?.message || e);
          }
        }, delay);
        timers.push(id);
      }
    }

    function isActive() {
      return timers.length > 0;
    }

    return { play, cancel, isActive };
  }

  Object.assign(window, {
    SimTimeline: { create },
  });
})();
