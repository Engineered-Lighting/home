// Read-only authored harness predicates, not application callbacks or input.
// One by-value evaluation avoids Playwright's otherwise unused poller handles.
import assert from 'node:assert/strict';

export function pollDirectionalReadiness({ source, arg, timeoutMs }) {
  // This function is serialized into the authored page. It deliberately has
  // no module closure, application setter, native input or DOM event dispatch.
  const predicate = globalThis.eval('(' + source + ')');
  return new Promise((resolve, reject) => {
    let frame = null, finished = false;
    const finish = (error, value) => {
      if (finished) return; finished = true;
      clearTimeout(timer); if (frame != null) cancelAnimationFrame(frame);
      if (error) reject(error); else resolve(value);
    };
    const timer = setTimeout(() => {
      const error = new Error('Directional readiness timeout ' + timeoutMs + 'ms'); error.name = 'TimeoutError'; finish(error);
    }, timeoutMs);
    const next = () => {
      if (finished) return;
      try {
        const value = predicate(arg);
        if (value && typeof value.then === 'function') throw Error('unsupported async directional readiness predicate');
        if (value) {
          if (!['boolean', 'string', 'number'].includes(typeof value) || typeof value === 'number' && !Number.isFinite(value))
            throw Error('unsupported directional readiness success value');
          finish(null, value);
        } else frame = requestAnimationFrame(next);
      } catch (error) { finish(error); }
    };
    next(); // Same immediate-first ordering as the pinned poller.
  });
}

export async function waitForDirectionalReadiness(page, predicate, arg, options) {
  assert.equal(typeof page?.evaluate, 'function'); assert.equal(typeof predicate, 'function');
  assert.ok(options == null || Object.getPrototypeOf(options) === Object.prototype && Object.keys(options).length === 0,
    'only the existing default RAF polling/options are supported');
  const source = Function.prototype.toString.call(predicate);
  assert.ok(predicate.constructor.name === 'Function' && !source.includes('[native code]') && !/^\s*class\b/.test(source),
    'only synchronous static harness functions are supported');
  const timeoutMs = 15000;
  let timer;
  // Keep the original Node-side bound as well as the in-page loop bound.
  // Protocol transit never adds grace to the per-wait or global deadline.
  try {
    return await Promise.race([page.evaluate(pollDirectionalReadiness, { source, arg, timeoutMs }), new Promise((_, reject) => {
      timer = setTimeout(() => { const error = new Error('Directional readiness timeout ' + timeoutMs + 'ms'); error.name = 'TimeoutError'; reject(error); }, timeoutMs);
    })]);
  } finally { clearTimeout(timer); }
}
