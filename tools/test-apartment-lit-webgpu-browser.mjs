// L2 on the WebGPU path, part 1 of 3: Tier-0 Lit on node materials and the
// bright ladder through one spot. Part 3 overlaps that rung and reaches moonlight.
import test from 'node:test';
import { runLitWebgpuBrowserProof } from './fixtures/apartment-lit-webgpu-browser-proof.mjs';

test('Lit on the WebGPU path keeps Tier-0 and the bright ladder reads naturally (part 1)', { timeout: 145000 },
  t => runLitWebgpuBrowserProof({ part: 1, signal: t.signal }));
