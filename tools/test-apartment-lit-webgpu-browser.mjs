// L2 on the WebGPU path, part 1 of 2: Tier-0 Lit on node materials and the
// lights-off ladder (tools/fixtures/apartment-lit-webgpu-browser-proof.mjs).
import test from 'node:test';
import { runLitWebgpuBrowserProof } from './fixtures/apartment-lit-webgpu-browser-proof.mjs';

test('Lit on the WebGPU path keeps Tier-0 and the lights-off ladder reads naturally (part 1)', { timeout: 175000 },
  () => runLitWebgpuBrowserProof({ part: 1 }));
