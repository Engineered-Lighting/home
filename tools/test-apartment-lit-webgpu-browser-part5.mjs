// Actual renderer sample clocks and unchanged night-adaptation easing bounds.
import test from 'node:test';
import {runLitWebgpuBrowserProof} from './fixtures/apartment-lit-webgpu-browser-proof.mjs';

test('Lit night adaptation follows its sampled brightening and darkening bounds', {timeout:145000},
  t=>runLitWebgpuBrowserProof({part:5,signal:t.signal}));
