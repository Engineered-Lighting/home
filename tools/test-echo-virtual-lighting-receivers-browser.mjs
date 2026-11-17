import test from 'node:test';
import { runLitSplatsBrowserProof } from './fixtures/apartment-lit-splats-browser-proof.mjs';

// Photo and Cloud are relit by different receivers, so each has its own file and the proof's whole deadline (Cloud is
// part 2). Run one after the other in one page they passed locally in 84 s but ran past the deadline on a hosted runner,
// where each settles within two frames and takes about 30 s (2026-11-15).
test('Echo Photo relights every saved spotlight and isolates aiming and dimming from Home Assistant', { timeout: 150000 },
  ({ signal }) => runLitSplatsBrowserProof({ part: 2, site: 'echo', phase: 'receivers-splat', signal }));
