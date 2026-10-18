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
