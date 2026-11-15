import test from 'node:test';
import { runLitSplatsBrowserProof } from './fixtures/apartment-lit-splats-browser-proof.mjs';

// Cloud's half of the Echo receivers proof; Photo's is part 1, which says why they run apart.
test('Echo Cloud relights every saved spotlight and isolates aiming and dimming from Home Assistant', { timeout: 150000 },
  ({ signal }) => runLitSplatsBrowserProof({ part: 2, site: 'echo', phase: 'receivers-points', signal }));
