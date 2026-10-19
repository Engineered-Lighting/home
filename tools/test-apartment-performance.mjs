// Public governor behavior for ordinary Apartment rendering. No world runtime,
// browser, GPU, timers or npm-installed dependencies are needed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createAdaptivePerformanceController,
  PERFORMANCE_BUDGETS,
  PERFORMANCE_TARGETS,
  selectClientPerformanceTarget,
} from '../app/src/home-3d/adaptive-performance.mjs';
import { ABBA_CRITERION, analyzeAbba, displayBound, onTimerGrid } from './apartment-overlay-abba.mjs';

test('client target remains stable when desktop and phone viewport widths change', () => {
  const desktop = { userAgentData: { mobile: false }, hardwareConcurrency: 8, deviceMemory: 8 };
  const phone = { userAgentData: { mobile: true }, hardwareConcurrency: 8, deviceMemory: 8 };
  for (const innerWidth of [390, 900, 1600]) {
    const windowRef = { innerWidth, innerHeight: 844, screen: { width: 1600, height: 900 } };
    assert.equal(selectClientPerformanceTarget({ navigatorRef: desktop, windowRef }), 'desktop');
    assert.equal(selectClientPerformanceTarget({ navigatorRef: phone, windowRef }), 'mobile60');
    assert.equal(selectClientPerformanceTarget({ navigatorRef: { ...phone, connection: { saveData: true } }, windowRef }), 'mobile30');
  }
});

test('budget subscribers observe synchronous commits and stop after unsubscribe', () => {
  let now = 0;
  const controller = createAdaptivePerformanceController({ clock: () => now });
  const seen = [];
  const unsubscribe = controller.subscribe(change => {
    seen.push({ published: change.budget, current: controller.getBudget() });
  });
  const battery = controller.setMode('performanceBattery');
  assert.equal(battery.budget, PERFORMANCE_BUDGETS.minimum);
  assert.deepEqual(seen.map(change => change.published.id), ['minimum']);
  assert.equal(seen[0].published, seen[0].current, 'the new budget is committed before publication');
  now += 1;
  const maximum = controller.setMode('maxFidelity');
  assert.equal(maximum.budget, PERFORMANCE_BUDGETS.maximum);
  assert.deepEqual(seen.map(change => change.published.id), ['minimum', 'maximum']);
  assert.equal(seen[1].published, seen[1].current);
  assert.equal(unsubscribe(), true);
  assert.equal(unsubscribe(), false);
  now += 1;
  controller.setMode('adaptive');
  assert.equal(controller.getBudget(), PERFORMANCE_BUDGETS.high);
  assert.deepEqual(seen.map(change => change.published.id), ['minimum', 'maximum']);
});

test('only the active sampler contributes frames and releasing it clears old evidence', () => {
  let now = 0;
  const controller = createAdaptivePerformanceController({ clock: () => now });
  const apartment = Symbol('apartment'), other = Symbol('other');
  controller.setSamplingSourceActive(other, true, { priority: 1 });
  controller.setSamplingSourceActive(apartment, true, { priority: 20 });
  assert.equal(controller.isSamplingAuthority(apartment), true);
  assert.equal(controller.isSamplingAuthority(other), false);
  controller.recordFrame(120, { samplingSource: other });
  assert.equal(controller.getDiagnostics().sampleCount, 0);
  now += 16;
  controller.recordFrame(16, { samplingSource: apartment });
  assert.equal(controller.getDiagnostics().sampleCount, 1);
  now += 16;
  controller.setSamplingSourceActive(apartment, false);
  assert.equal(controller.isSamplingAuthority(other), true);
  assert.equal(controller.getDiagnostics().sampleCount, 0);
  controller.recordFrame(120, { samplingSource: apartment });
  assert.equal(controller.getDiagnostics().sampleCount, 0);
  now += 16;
  controller.recordFrame(16, { samplingSource: other });
  assert.equal(controller.getDiagnostics().sampleCount, 1);
});

test('a measured slow-frame window lowers one budget rung and publishes the decision', () => {
  let now = 0;
  const controller = createAdaptivePerformanceController({ target: 'desktop', clock: () => now });
  const seen = [];
  controller.subscribe(change => seen.push(change));
  for (let frame = 0; frame < PERFORMANCE_TARGETS.desktop.minimumSamples; frame += 1) {
    now += 110;
    controller.recordFrame(110);
  }
  assert.equal(controller.getBudget(), PERFORMANCE_BUDGETS.balanced);
  assert.equal(controller.getDiagnostics().decisionCount, 1);
  assert.equal(controller.getDiagnostics().sampleCount, 0, 'the new budget starts a fresh evidence window');
  assert.equal(seen.length, 1);
  assert.equal(seen[0].previousLevel, 'high');
  assert.equal(seen[0].level, 'balanced');
  assert.equal(seen[0].reason, 'long-frame-burst');
});

// APT-F01 pre-registered ABBA criterion (docs/design/apartment-unified/
// overlay-performance-receipt-w1.md). Pure arithmetic; no browser.
const abbaBlocks = rows => rows.map(([A1, B1, B2, A2]) => ({ A1, B1, B2, A2 }));

test('ABBA criterion: identical vsync-pinned frames are quiet with the spread floored at one timer quantum', () => {
  const result = analyzeAbba(abbaBlocks(Array(4).fill([16.7, 16.7, 16.7, 16.7])), { metric: 'frameP95' });
  assert.equal(result.role, 'primary');
  assert.equal(result.snapped_to_timer_grid, true);
  assert.equal(result.measured_aa_spread, 0);
  assert.equal(result.aa_spread, ABBA_CRITERION.quantumMs);
  assert.equal(result.valid, true);
  assert.equal(result.result, 'no-measurable-regression');
  assert.equal(displayBound(result.a_median), true);
});

test('ABBA criterion: float residue on the 0.1 ms grid cannot flip a rule', () => {
  const result = analyzeAbba(abbaBlocks([
    [4.299999999999272, 4.300000000001091, 4.300000000001091, 4.299999999999272],
    [4.299999999999272, 4.299999999999272, 4.299999999999272, 4.299999999999272],
    [4.30000000000291, 4.30000000000291, 4.299999999995634, 4.30000000000291],
    [4.299999999995634, 4.30000000000291, 4.30000000000291, 4.299999999995634],
  ]), { metric: 'frameP95' });
  assert.equal(result.snapped_to_timer_grid, true);
  assert.equal(result.median_delta, 0);
  assert.equal(result.result, 'no-measurable-regression');
});

test('ABBA criterion: a consistent B cost beyond the AA spread is a measured cost, an improvement keeps its sign', () => {
  const cost = analyzeAbba(abbaBlocks(Array(4).fill([2.0, 2.4, 2.4, 2.1])), { metric: 'renderP95' });
  assert.equal(cost.role, 'descriptive');
  assert.equal(cost.valid, true);
  assert.ok(Math.abs(cost.median_delta - .35) < 1e-9);
  assert.equal(cost.result, 'measured-difference-cost');
  const better = analyzeAbba(abbaBlocks(Array(4).fill([2.4, 2.0, 2.0, 2.4])), { metric: 'renderP95' });
  assert.equal(better.result, 'measured-difference-improvement');
});

test('ABBA criterion: an A/A spread above 10% of the A median is invalid, not a pass', () => {
  const result = analyzeAbba(abbaBlocks([[2.0, 2.0, 2.0, 2.6], [2.0, 2.0, 2.0, 2.0], [2.0, 2.0, 2.0, 2.0], [2.0, 2.0, 2.0, 2.0]]), { metric: 'renderP95' });
  assert.equal(result.valid, false);
  assert.equal(result.result, 'invalid-noisy');
});

test('ABBA criterion: one outlier block is tolerated, two are a measured difference', () => {
  const one = analyzeAbba(abbaBlocks([[16.7, 17.3, 17.3, 16.7], [16.7, 16.7, 16.7, 16.7], [16.7, 16.7, 16.7, 16.7], [16.7, 16.7, 16.7, 16.7]]), { metric: 'frameP95' });
  assert.equal(one.outlier_blocks, 1);
  assert.equal(one.result, 'no-measurable-regression');
  const two = analyzeAbba(abbaBlocks([[16.7, 17.3, 17.3, 16.7], [16.7, 17.3, 17.3, 16.7], [16.7, 16.7, 16.7, 16.7], [16.7, 16.7, 16.7, 16.7]]), { metric: 'frameP95' });
  assert.equal(two.outlier_blocks, 2);
  assert.equal(two.result, 'measured-difference-cost');
});

test('ABBA criterion: off-grid values are analysed raw, without snapping or a floor', () => {
  assert.equal(onTimerGrid([16.7, 2.05]), false);
  const result = analyzeAbba(abbaBlocks(Array(4).fill([2.03, 2.05, 2.05, 2.04])), { metric: 'rafP95' });
  assert.equal(result.snapped_to_timer_grid, false);
  assert.ok(Math.abs(result.aa_spread - .01) < 1e-9);
  assert.equal(result.result, 'measured-difference-cost');
});

test('ABBA criterion: refuses short or non-finite designs', () => {
  assert.throws(() => analyzeAbba(abbaBlocks(Array(3).fill([1, 1, 1, 1]))), /abba_minimum_blocks/);
  assert.throws(() => analyzeAbba(abbaBlocks([[1, 1, NaN, 1], [1, 1, 1, 1], [1, 1, 1, 1], [1, 1, 1, 1]])), /abba_slot_not_finite/);
});
