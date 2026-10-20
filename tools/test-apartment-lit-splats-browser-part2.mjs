// Authored-room browser proof for L4 lit splats and points, part 2: with
// ?lit-splats=1 Cloud is relit from the bound authored layer (GPU = CPU twin)
// and restored, and the node renderer's WebGL2 backend declares lit receivers
// unavailable before reading any layer, leaving the frame as it was.
// The proof lives in fixtures/apartment-lit-splats-browser-proof.mjs.
import test from 'node:test';
import { runLitSplatsBrowserProof } from './fixtures/apartment-lit-splats-browser-proof.mjs';

test('L4 part 2: Cloud relit and restored; the WebGL2 backend declares lit receivers unavailable', { timeout: 145000 },
  t => runLitSplatsBrowserProof({ part: 2, signal: t.signal }));
