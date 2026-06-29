/**
 * fake-timers.js — deterministic clock + timer control for Node tests.
 *
 * Replaces setTimeout / setInterval / clearTimeout / clearInterval /
 * setImmediate / clearImmediate / Date.now / performance.now with
 * controllable versions. Tests advance time via clock.tick(ms) or
 * clock.tickAsync(ms) (the async variant lets queued microtasks flush
 * between timer fires).
 *
 * Dual export (window + module.exports) per existing helper convention.
 *
 * Usage:
 *   const FT = require('./fake-timers.js');
 *   const clock = FT.install();
 *   setTimeout(() => doThing(), 500);
 *   clock.tick(500);     // fires the timer synchronously
 *   FT.uninstall(clock);  // restore real timers
 *
 * Self-test: node tools/test_helpers/fake-timers.js
 */

(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) {
    module.exports = factory();
  } else {
    root.FakeTimers = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function install(opts) {
    opts = opts || {};
    const now0 = typeof opts.now === "number" ? opts.now : 1_700_000_000_000;

    // Save originals
    const real = {
      setTimeout: globalThis.setTimeout,
      clearTimeout: globalThis.clearTimeout,
      setInterval: globalThis.setInterval,
      clearInterval: globalThis.clearInterval,
      setImmediate: globalThis.setImmediate,
      clearImmediate: globalThis.clearImmediate,
      dateNow: Date.now,
      perfNow: typeof performance !== "undefined" ? performance.now.bind(performance) : null,
    };

    const state = {
      currentTime: now0,
      nextId: 1,
      // Sorted heap-ish: { id, fireAt, callback, periodMs (or 0), args }
      timers: [],
      installed: true,
      _real: real,
    };

    function _add(timer) {
      state.timers.push(timer);
      state.timers.sort((a, b) => a.fireAt - b.fireAt || a.id - b.id);
      return timer.id;
    }

    function _remove(id) {
      const i = state.timers.findIndex((t) => t.id === id);
      if (i >= 0) state.timers.splice(i, 1);
    }

    globalThis.setTimeout = function fakeSetTimeout(cb, ms) {
      const args = Array.prototype.slice.call(arguments, 2);
      return _add({
        id: state.nextId++,
        fireAt: state.currentTime + (ms || 0),
        callback: cb,
        periodMs: 0,
        args,
      });
    };
    globalThis.clearTimeout = function (id) { _remove(id); };

    globalThis.setInterval = function fakeSetInterval(cb, ms) {
      const args = Array.prototype.slice.call(arguments, 2);
      return _add({
        id: state.nextId++,
        fireAt: state.currentTime + (ms || 0),
        callback: cb,
        periodMs: ms || 0,
        args,
      });
    };
    globalThis.clearInterval = function (id) { _remove(id); };

    globalThis.setImmediate = function fakeSetImmediate(cb) {
      const args = Array.prototype.slice.call(arguments, 1);
      return _add({
        id: state.nextId++,
        fireAt: state.currentTime,  // fires on next tick(0)
        callback: cb,
        periodMs: 0,
        args,
      });
    };
    globalThis.clearImmediate = function (id) { _remove(id); };

    Date.now = function () { return state.currentTime; };
    if (typeof performance !== "undefined") {
      performance.now = function () { return state.currentTime - now0; };
    }

    // ── Clock control ──
    const clock = {
      get now() { return state.currentTime; },
      timersPending() { return state.timers.length; },

      /** Synchronous advance — fires every timer up to + including the
       * new time. Re-queues intervals. Does NOT flush microtasks
       * between fires; use tickAsync for that. */
      tick(ms) {
        const target = state.currentTime + (ms || 0);
        while (state.timers.length && state.timers[0].fireAt <= target) {
          const t = state.timers.shift();
          state.currentTime = t.fireAt;
          try {
            t.callback.apply(null, t.args);
          } catch (e) {
            console.error("[fake-timers] timer threw:", e);
          }
          if (t.periodMs > 0) {
            _add({
              id: t.id,
              fireAt: state.currentTime + t.periodMs,
              callback: t.callback,
              periodMs: t.periodMs,
              args: t.args,
            });
          }
        }
        state.currentTime = target;
      },

      /** Async variant: yield to the microtask queue between fires.
       * Required when test code awaits Promises that resolve inside
       * timer callbacks. Returns a Promise. */
      async tickAsync(ms) {
        const target = state.currentTime + (ms || 0);
        while (state.timers.length && state.timers[0].fireAt <= target) {
          const t = state.timers.shift();
          state.currentTime = t.fireAt;
          try {
            const res = t.callback.apply(null, t.args);
            if (res && typeof res.then === "function") {
              await res;
            }
          } catch (e) {
            console.error("[fake-timers] timer threw:", e);
          }
          // Flush pending microtasks before the next timer fires.
          await new Promise((r) => real.setImmediate(r));
          if (t.periodMs > 0) {
            _add({
              id: t.id,
              fireAt: state.currentTime + t.periodMs,
              callback: t.callback,
              periodMs: t.periodMs,
              args: t.args,
            });
          }
        }
        state.currentTime = target;
      },

      reset(toNow) {
        state.currentTime = typeof toNow === "number" ? toNow : now0;
        state.timers.length = 0;
        state.nextId = 1;
      },

      _state: state,
    };

    return clock;
  }

  function uninstall(clock) {
    if (!clock || !clock._state || !clock._state.installed) return;
    const r = clock._state._real;
    globalThis.setTimeout = r.setTimeout;
    globalThis.clearTimeout = r.clearTimeout;
    globalThis.setInterval = r.setInterval;
    globalThis.clearInterval = r.clearInterval;
    globalThis.setImmediate = r.setImmediate;
    globalThis.clearImmediate = r.clearImmediate;
    Date.now = r.dateNow;
    if (typeof performance !== "undefined" && r.perfNow) {
      performance.now = r.perfNow;
    }
    clock._state.installed = false;
  }

  return { install, uninstall };
});


// ───────────────────────────────────────────────────────────────────
// Self-test — runs when executed via `node fake-timers.js`
// ───────────────────────────────────────────────────────────────────
if (require.main === module) {
  const FT = module.exports;
  let pass = 0, fail = 0;
  function ok(name, cond, detail) {
    if (cond) { pass++; console.log("  PASS  " + name); }
    else      { fail++; console.log("  FAIL  " + name + " (" + JSON.stringify(detail) + ")"); }
  }

  console.log("\n[fake-timers self-test]\n");

  // ── basic setTimeout / tick ──
  let clock = FT.install();
  let fired = false;
  setTimeout(() => { fired = true; }, 500);
  clock.tick(499);
  ok("setTimeout doesn't fire before its time", fired === false);
  clock.tick(1);
  ok("setTimeout fires exactly at its time", fired === true);
  FT.uninstall(clock);

  // ── clearTimeout ──
  clock = FT.install();
  fired = false;
  const id = setTimeout(() => { fired = true; }, 100);
  clearTimeout(id);
  clock.tick(200);
  ok("clearTimeout cancels the timer", fired === false);
  FT.uninstall(clock);

  // ── setInterval ──
  clock = FT.install();
  let count = 0;
  const intId = setInterval(() => { count++; }, 100);
  clock.tick(350);
  ok("setInterval fires N times for N periods", count === 3);
  clearInterval(intId);
  clock.tick(500);
  ok("clearInterval stops the interval", count === 3);
  FT.uninstall(clock);

  // ── Date.now ──
  clock = FT.install({ now: 1_000_000 });
  ok("Date.now returns installed initial time", Date.now() === 1_000_000);
  clock.tick(750);
  ok("Date.now advances with the clock", Date.now() === 1_000_750);
  FT.uninstall(clock);

  // ── performance.now relative to install ──
  clock = FT.install();
  const p0 = performance.now();
  clock.tick(100);
  const p1 = performance.now();
  ok("performance.now advances by tick amount", p1 - p0 === 100);
  FT.uninstall(clock);

  // ── async tick + microtask flush ──
  clock = FT.install();
  let order = [];
  setTimeout(async () => { order.push("a"); await Promise.resolve(); order.push("b"); }, 100);
  setTimeout(() => { order.push("c"); }, 200);
  (async () => {
    await clock.tickAsync(200);
    ok("tickAsync interleaves microtasks between timers",
       JSON.stringify(order) === '["a","b","c"]', { order });
    FT.uninstall(clock);
  })().then(() => {
    // ── uninstall restores originals ──
    const realNow = Date.now();
    const fakeWas = realNow > 1_000_000_000_000;
    ok("uninstall restores real Date.now", fakeWas);

    console.log("\n" + pass + " pass · " + fail + " fail");
    process.exit(fail === 0 ? 0 : 1);
  });
}
