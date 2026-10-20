// The complete HDR camera-look ladder, independently bounded from the eye look.
import test from 'node:test';
import { runLitWebgpuBrowserProof } from './fixtures/apartment-lit-webgpu-browser-proof.mjs';
test('HDR camera look retains every dimming rung and adjacent luminance comparison', {timeout:145000},
  t=>runLitWebgpuBrowserProof({part:4,signal:t.signal}));
