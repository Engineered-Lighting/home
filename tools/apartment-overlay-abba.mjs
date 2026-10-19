// Pre-registered APT-F01 ABBA noise criterion. Pure: no browser, timers or
// files. The protocol lives in docs/design/apartment-unified/
// overlay-performance-receipt-w1.md; this module only implements it.

export const ABBA_CRITERION = Object.freeze({
  schema: 'home-apartment-overlay-abba-criterion-1',
  order: Object.freeze(['A1', 'B1', 'B2', 'A2']),
  minimumBlocks: 4,
  validityFraction: 0.1,
  outlierFactor: 2,
  maximumOutlierBlocks: 1,
  quantumMs: 0.1,
  primaryMetrics: Object.freeze(['frameP95', 'medianFrame']),
  descriptiveMetrics: Object.freeze(['renderP95', 'rafP95']),
});

const median = values => {
  const sorted = [...values].sort((a, b) => a - b), middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

// True when every value lies on the 0.1 ms timer grid up to float noise.
export function onTimerGrid(values, quantum = ABBA_CRITERION.quantumMs) {
  return values.every(value => Number.isFinite(value) && Math.abs(value / quantum - Math.round(value / quantum)) < 1e-6);
}

export function snap(value, quantum = ABBA_CRITERION.quantumMs) {
  return Math.round(value / quantum) * quantum;
}

// blocks: [{A1, B1, B2, A2}] raw values for one scene and one metric.
export function analyzeAbba(blocks, { metric, quantum = ABBA_CRITERION.quantumMs } = {}) {
  const c = ABBA_CRITERION;
  if (!Array.isArray(blocks) || blocks.length < c.minimumBlocks) throw Error('abba_minimum_blocks');
  for (const block of blocks) {
    for (const slot of c.order) if (!Number.isFinite(block?.[slot])) throw Error('abba_slot_not_finite');
  }
  const raw = blocks.flatMap(block => c.order.map(slot => block[slot]));
  const gridded = onTimerGrid(raw, quantum);
  const value = x => gridded ? snap(x, quantum) : x;
  const rows = blocks.map(block => {
    const a1 = value(block.A1), b1 = value(block.B1), b2 = value(block.B2), a2 = value(block.A2);
    return { a1, b1, b2, a2, noise: Math.abs(a1 - a2), delta: (b1 + b2) / 2 - (a1 + a2) / 2 };
  });
  const measuredSpread = Math.max(...rows.map(row => row.noise));
  const aaSpread = gridded ? Math.max(measuredSpread, quantum) : measuredSpread;
  const aMedian = median(rows.flatMap(row => [row.a1, row.a2]));
  const validityLimit = c.validityFraction * aMedian;
  // Compare on the snapped grid so float residue cannot flip a rule.
  const epsilon = gridded ? quantum * 1e-6 : 0;
  const valid = aaSpread <= validityLimit + epsilon;
  const medianDelta = median(rows.map(row => row.delta));
  const outlierBlocks = rows.filter(row => Math.abs(row.delta) > c.outlierFactor * aaSpread + epsilon).length;
  const quiet = Math.abs(medianDelta) <= aaSpread + epsilon && outlierBlocks <= c.maximumOutlierBlocks;
  const result = !valid ? 'invalid-noisy'
    : quiet ? 'no-measurable-regression'
      : medianDelta > 0 ? 'measured-difference-cost' : 'measured-difference-improvement';
  return {
    metric, role: c.primaryMetrics.includes(metric) ? 'primary' : 'descriptive',
    blocks: rows.length, snapped_to_timer_grid: gridded, quantum_ms: quantum,
    raw_values: raw, block_rows: rows,
    measured_aa_spread: measuredSpread, aa_spread: aaSpread,
    a_median: aMedian, validity_limit: validityLimit, valid,
    median_delta: medianDelta, outlier_blocks: outlierBlocks, result,
  };
}

// Display-bound when the A frame-p95 median sits within one quantum of a
// common refresh interval; primary metrics then cannot show sub-frame cost.
export function displayBound(frameP95Median, quantum = ABBA_CRITERION.quantumMs) {
  return [1000 / 240, 1000 / 144, 1000 / 120, 1000 / 90, 1000 / 75, 1000 / 60, 1000 / 30]
    .some(interval => Math.abs(frameP95Median - interval) <= 2 * quantum);
}
