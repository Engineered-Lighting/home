// L2 on the WebGPU path, part 2 of 2: the moonlit tone views, the adaptation
// ease, timing and the exact restore (tools/fixtures/apartment-lit-webgpu-browser-proof.mjs).
import test from 'node:test';
import { runLitWebgpuBrowserProof } from './fixtures/apartment-lit-webgpu-browser-proof.mjs';

test('Lit on the WebGPU path: comparison views, night adaptation ease and exact restore (part 2)', { timeout: 175000 },
  () => runLitWebgpuBrowserProof({ part: 2 }));
