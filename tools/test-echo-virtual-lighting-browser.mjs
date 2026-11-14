import test from 'node:test';
import { runLitSplatsBrowserProof } from './fixtures/apartment-lit-splats-browser-proof.mjs';

test('Echo Mesh renders all saved lights and supports direct beam dragging', { timeout: 150000 },
  ({ signal }) => runLitSplatsBrowserProof({ part: 2, site: 'echo', phase: 'mesh', signal }));
