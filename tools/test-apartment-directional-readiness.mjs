import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { pollDirectionalReadiness, waitForDirectionalReadiness } from './fixtures/apartment-directional-readiness.mjs';

function frameFixture(source, arg, timeoutMs = 15000) {
  const frames = new Map(), timers = new Map(), cleared = [], cancelled = []; let sequence = 0;
  const context = vm.createContext({ value: 0, calls: [],
    requestAnimationFrame: fn => { const id = ++sequence; frames.set(id, fn); return id; },
    cancelAnimationFrame: id => { cancelled.push(id); frames.delete(id); },
    setTimeout: (fn, ms) => { const id = ++sequence; timers.set(id, { fn, ms }); return id; },
    clearTimeout: id => { cleared.push(id); timers.delete(id); } });
  const poll = vm.runInContext('(' + pollDirectionalReadiness.toString() + ')', context);
  const result = poll({ source, arg, timeoutMs });
  const tick = () => { const pending = [...frames.values()]; frames.clear(); for (const fn of pending) fn(); };
  return { context, result, frames, timers, tick, cleared, cancelled };
}

test('an admitted predicate resolves its actual scalar value immediately without requesting a frame', async () => {
  const f = frameFixture('arg => { calls.push(arg); return 7; }', 'exact-argument');
  assert.equal(await f.result, 7); assert.deepEqual([...f.context.calls], ['exact-argument']);
  assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
});
test('false predicates retry only on real RAF callbacks with the same argument until admitted', async () => {
  const f = frameFixture('arg => { calls.push(arg); return value === 2; }', 42);
  assert.deepEqual([...f.context.calls], [42]); assert.equal(f.frames.size, 1);
  f.context.value = 1; f.tick(); assert.deepEqual([...f.context.calls], [42, 42]); assert.equal(f.frames.size, 1);
  f.context.value = 2; f.tick(); assert.equal(await f.result, true); assert.deepEqual([...f.context.calls], [42, 42, 42]);
  assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
});
test('a predicate error propagates without retry or successful-result transformation', async () => {
  const f = frameFixture('() => { throw new TypeError("exact predicate refusal"); }');
  await assert.rejects(f.result, error => error.name === 'TypeError' && error.message === 'exact predicate refusal');
  assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
});
test('the browser bound expires even if no RAF callback is serviced', async () => {
  const f = frameFixture('() => false'); const timer = [...f.timers.values()][0]; assert.equal(timer.ms, 15000);
  timer.fn(); await assert.rejects(f.result, error => error.name === 'TimeoutError');
  assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
});
for (const [name, source] of [['promise', '() => Promise.resolve(true)'], ['object/handle', '() => ({ready:true})'], ['nonfinite', '() => Infinity']])
  test('unsupported browser result fails closed: ' + name, async () => {
    const f = frameFixture(source); await assert.rejects(f.result, /unsupported/); assert.equal(f.frames.size, 0);
  });
test('one by-value evaluation receives the exact predicate source, argument and original bound', async () => {
  const predicate = arg => arg.id === 'authored'; const arg = { id: 'authored' }; let calls = 0;
  const page = { evaluate: async (fn, value) => {
    calls++; assert.equal(fn, pollDirectionalReadiness); assert.equal(value.source, predicate.toString());
    assert.equal(value.arg, arg); assert.equal(value.timeoutMs, 15000); return true;
  } };
  assert.equal(await waitForDirectionalReadiness(page, predicate, arg), true); assert.equal(calls, 1);
});
for (const [name, predicate, options] of [
  ['async function', async () => true], ['generator', function* () { yield true; }], ['native function', Math.abs],
  ['alternate polling', () => true, { polling: 100 }], ['timeout extension', () => true, { timeout: 16000 }],
]) test('unsupported readiness adapter contract refuses before evaluation: ' + name, async () => {
  let called = false; await assert.rejects(waitForDirectionalReadiness({ evaluate: async () => { called = true; } }, predicate, null, options));
  assert.equal(called, false);
});
