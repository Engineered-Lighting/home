// L2 on the WebGPU path, part 3 of 3: the ladder from one spot through 5 %
// output to moonlight, with the original luminance, adaptation and warmth gates.
import test from 'node:test';
import { runLitWebgpuBrowserProof } from './fixtures/apartment-lit-webgpu-browser-proof.mjs';

test('Lit on the WebGPU path: the dim ladder, bounded moonlight and warm spot pool (part 3)', { timeout: 175000 },
  () => runLitWebgpuBrowserProof({ part: 3 }));
