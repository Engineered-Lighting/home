// Authored Apartment shell, part 2 of 3: camera, metrics, the classic escape
// hatch and the unified URL override (tools/fixtures/apartment-home-shell-browser-proof.mjs),
// on its own worker so no part runs near the 145 s deadline.
import test from 'node:test';
import { runShellBrowserProof } from './fixtures/apartment-home-shell-browser-proof.mjs';

test('unified shell keeps the real room, private controls and one chat controller through responsive navigation (part 2)', { timeout: 145000 },
  () => runShellBrowserProof({ part: 2 }));
