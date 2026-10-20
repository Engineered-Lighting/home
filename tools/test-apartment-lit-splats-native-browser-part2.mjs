import test from 'node:test';
import {runLitSplatsBrowserProof} from './fixtures/apartment-lit-splats-browser-proof.mjs';
test('native WebGL: lighting follows every view and preserves fallback refusal/material restoration',{timeout:145000},
  t=>runLitSplatsBrowserProof({part:2,precision:'native',signal:t.signal}));
