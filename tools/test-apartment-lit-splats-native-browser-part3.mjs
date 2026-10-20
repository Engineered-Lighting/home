import test from 'node:test';
import {runLitSplatsBrowserProof} from './fixtures/apartment-lit-splats-browser-proof.mjs';
test('native WebGL: live spot rebakes, the complete dimming ladder and calibrated restoration',{timeout:145000},
  t=>runLitSplatsBrowserProof({part:1,precision:'native',phase:'controls',signal:t.signal}));
