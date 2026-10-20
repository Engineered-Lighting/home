// Authored-room browser proof for Lighting in every view, part 2: Lighting
// turned on in Photo follows the view to Cloud (relit from the bound authored
// layer, GPU = CPU twin) and Mesh, and off restores them; the node renderer's
// WebGL2 backend says plainly that it can't relight Photo, before reading any
// layer, leaving the frame as it was.
// The proof lives in fixtures/apartment-lit-splats-browser-proof.mjs.
import test from 'node:test';
import { runLitSplatsBrowserProof } from './fixtures/apartment-lit-splats-browser-proof.mjs';

test('Lighting part 2: follows the view through Cloud and Mesh; the WebGL2 backend declares lit receivers unavailable', { timeout: 145000 },
  t => runLitSplatsBrowserProof({ part: 2, signal: t.signal }));
