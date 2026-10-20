// Additional native WebGL qualification. The original two proofs retain
// byte equality on deterministic software; native quantization is measured.
import test from 'node:test';
import {runLitSplatsBrowserProof} from './fixtures/apartment-lit-splats-browser-proof.mjs';
test('native WebGL: bound relighting, HDR passes, GPU/CPU agreement and calibrated restoration',{timeout:145000},
  t=>runLitSplatsBrowserProof({part:1,precision:'native',phase:'frames',signal:t.signal}));
