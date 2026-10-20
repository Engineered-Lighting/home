// Retain the original twelve calibration samples in their own bounded worker.
import test from 'node:test';
import {runLitSplatsBrowserProof} from './fixtures/apartment-lit-splats-browser-proof.mjs';

test('native WebGL: twelve stationary unlit calibration samples',{timeout:145000},
  t=>runLitSplatsBrowserProof({part:1,precision:'native',phase:'calibration',signal:t.signal}));
