// Additional native WebGL qualification. The original two proofs retain
// byte equality on deterministic software; native quantization is measured.
import test from 'node:test';
import {runLitSplatsBrowserProof} from './fixtures/apartment-lit-splats-browser-proof.mjs';
test('native WebGL: bound relighting, GPU/CPU agreement and calibrated unlit restoration',{timeout:145000},
  t=>runLitSplatsBrowserProof({part:1,precision:'native',signal:t.signal}));
