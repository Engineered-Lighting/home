// Authored-room browser proof for Lighting in every view, part 1: with
// Lighting off the frame is identical and nothing lit loads; with it on Photo
// is relit from the SHA-256 bound authored layer (GPU = CPU twin, live spots,
// monotone dimming) and turning it off restores the exact frame.
// The proof lives in fixtures/apartment-lit-splats-browser-proof.mjs.
import test from 'node:test';
import { runLitSplatsBrowserProof } from './fixtures/apartment-lit-splats-browser-proof.mjs';

test('Lighting part 1: off is the identical frame; Photo relit from the bound layer and restored exactly', { timeout: 145000 },
  t => runLitSplatsBrowserProof({ part: 1, signal: t.signal }));
