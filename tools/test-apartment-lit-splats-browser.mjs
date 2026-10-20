// Authored-room browser proof for L4 lit splats and points, part 1: with the
// flag off the frame is identical and nothing lit loads; with ?lit-splats=1
// Photo is relit from the SHA-256 bound authored layer (GPU = CPU twin, live
// spots, monotone dimming) and leaving Lit restores the exact frame.
// The proof lives in fixtures/apartment-lit-splats-browser-proof.mjs.
import test from 'node:test';
import { runLitSplatsBrowserProof } from './fixtures/apartment-lit-splats-browser-proof.mjs';

test('L4 part 1: flag off is the identical frame; Photo relit from the bound layer and restored exactly', { timeout: 180000 },
  () => runLitSplatsBrowserProof({ part: 1 }));
