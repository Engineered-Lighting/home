import test from 'node:test';
import { runLitSplatsBrowserProof } from './fixtures/apartment-lit-splats-browser-proof.mjs';

test('Echo Photo and Cloud relight every saved spotlight and isolate aiming and dimming from Home Assistant', { timeout: 150000 },
  ({ signal }) => runLitSplatsBrowserProof({ part: 2, site: 'echo', phase: 'receivers', signal }));
