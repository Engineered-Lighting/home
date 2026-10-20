// Authored Apartment shell, part 1 of 3: the Chat presenter proof, boot, Echo, Victoria fixtures,
// radial power, the canonical drags and the command ladder
// (tools/fixtures/apartment-home-shell-browser-proof.mjs). Parts 2 and 3 run
// on another worker.
import test from 'node:test';
import { runShellBrowserProof } from './fixtures/apartment-home-shell-browser-proof.mjs';

test('unified shell keeps the real room, private controls and one chat controller through responsive navigation (part 1)', { timeout: 145000 },
  () => runShellBrowserProof({ part: 1 }));
